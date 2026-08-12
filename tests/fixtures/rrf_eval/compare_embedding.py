"""임베딩 모델(e5-small vs e5-base) A/B 실측 스크립트.

`docs/15-embedding-model-swap-experiment.md` 3절이 정의한 실험 하네스다.
`compare_chunking.py`의 temp-DB 생성/등록/지표 로직을 재사용하되, 바꾸는
변형 축은 "청킹"이 아니라 "임베딩 provider + 벡터 컬럼 dim"이다.

**비자명한 마찰(docs/15 §3-1)**: `ApiChunk.embedding = mapped_column(Vector(EMBEDDING_DIM))`
은 `app/models/openapi.py` **import 시점**에 dim이 고정된다. 런타임에 상수만
바꿔도 이미 정의된 컬럼 타입은 안 바뀐다. 이 스크립트는 §3-1 권장안(모델별
서브프로세스 격리)을 따른다 — 각 변형을 `--worker` 서브프로세스로 띄우고,
그 프로세스가 `app.models.openapi`를 import하기 **전에** 소스 코드 레벨에서
`EMBEDDING_DIM` 상수를 후보 dim으로 패치한 모듈을 `sys.modules`에 미리
등록한다. 프로덕션 파일(`app/models/openapi.py`)은 디스크에서 전혀 수정되지
않는다 — 패치는 이 스크립트가 메모리에서 읽어 exec하는 사본에만 적용된다.

사용법(로컬 postgres 필요, `docker compose up -d postgres`):
    uv run python tests/fixtures/rrf_eval/compare_embedding.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from metrics import dcg_at, recall_at, reciprocal_rank  # type: ignore[import-not-found]

_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DIR.parent.parent.parent
TOP_K = 10
RECALL_KS = (1, 3, 5, 10)

#: 실행 축(docs/15 §3-3). baseline=현행, 후보1=1차 드롭인 후보.
VARIANTS = [
    {"label": "baseline(e5-small)", "model": "intfloat/multilingual-e5-small", "dim": 384},
    {"label": "후보1(e5-base)", "model": "intfloat/multilingual-e5-base", "dim": 768},
]


@dataclass
class EvalQuery:
    query: str
    category: str
    accepted: list[tuple[str, str]]


def _load_queries(path: Path) -> list[EvalQuery]:
    raw = json.loads(path.read_text())
    return [
        EvalQuery(
            query=item["query"],
            category=item["category"],
            accepted=[(m, p) for m, p in item["accepted"]],
        )
        for item in raw
    ]


def _load_valid_endpoints(openapi_doc: str) -> set[tuple[str, str]]:
    paths = json.loads(openapi_doc)["paths"]
    return {(method.upper(), path) for path, methods in paths.items() for method in methods}


def _validate_labels(queries: list[EvalQuery], valid_endpoints: set[tuple[str, str]]) -> None:
    bad = [
        (eq.query, method, path)
        for eq in queries
        for method, path in eq.accepted
        if (method, path) not in valid_endpoints
    ]
    if bad:
        raise ValueError(f"미존재 라벨(openapi.json에 없는 accepted 엔드포인트): {bad}")


def _rank_of_answer(candidates, accepted: list[tuple[str, str]]) -> int | None:
    accepted_set = set(accepted)
    for i, c in enumerate(candidates, start=1):
        if (c.method, c.path) in accepted_set:
            return i
    return None


class EvalSummary(NamedTuple):
    recall: dict[int, float]
    mrr: float
    ndcg10: float


def _summarize(ranks: list[int | None]) -> EvalSummary:
    n = len(ranks)
    recall = {k: sum(recall_at(r, k) for r in ranks) / n for k in RECALL_KS}
    mrr = statistics.mean(reciprocal_rank(r) for r in ranks)
    ndcg10 = statistics.mean(dcg_at(r, 10) for r in ranks)
    return EvalSummary(recall=recall, mrr=mrr, ndcg10=ndcg10)


def _format_summary_line(label: str, summary: EvalSummary) -> str:
    recall_str = " ".join(f"Recall@{k} {summary.recall[k]:.0%}" for k in RECALL_KS)
    return f"- {label}: {recall_str} | MRR {summary.mrr:.3f} | nDCG@10 {summary.ndcg10:.3f}"


# ---------------------------------------------------------------------------
# 워커(서브프로세스) 전용 — 아래 함수들은 `_patch_embedding_dim` 호출 *이후*에만
# app.* 를 import한다. 모듈 최상단에서 app.* 를 import하면 서브프로세스마다
# dim을 다르게 패치하는 의미가 없어진다(첫 import 시점에 컬럼 dim이 박히므로).
# ---------------------------------------------------------------------------


def _patch_embedding_dim(candidate_dim: int) -> None:
    """`app.models.openapi` 소스의 `EMBEDDING_DIM = 384`를 후보 dim으로 바꿔
    `sys.modules`에 미리 등록한다. 디스크의 원본 파일은 건드리지 않는다.

    이후 어디서든 `from app.models.openapi import ...`를 하면 import 기계가
    `sys.modules["app.models.openapi"]`를 먼저 확인하므로, 이 패치된 모듈을
    그대로 재사용한다(원본 재실행 없음) — `ApiChunk.embedding` 컬럼이 후보
    dim으로 정의된다.
    """
    import importlib.util

    spec = importlib.util.find_spec("app.models.openapi")
    assert spec is not None and spec.loader is not None
    source = spec.loader.get_source("app.models.openapi")
    assert source is not None
    marker = "EMBEDDING_DIM = 384"
    hit_count = source.count(marker)
    if hit_count != 1:
        raise RuntimeError(
            f"EMBEDDING_DIM 패치 지점을 못박지 못함(marker {marker!r} 매칭 {hit_count}건, 기대값 1) "
            "— app/models/openapi.py가 바뀌어 이 스크립트의 패치 가정이 깨졌을 수 있다."
        )
    patched_source = source.replace(marker, f"EMBEDDING_DIM = {candidate_dim}", 1)
    module = importlib.util.module_from_spec(spec)
    sys.modules["app.models.openapi"] = module
    exec(compile(patched_source, spec.origin, "exec"), module.__dict__)


def _make_temp_db() -> tuple[str, str]:
    from app.core.db import create_db_engine
    from sqlalchemy import text

    admin_url = "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
    dbname = f"embedeval_{uuid.uuid4().hex[:8]}"
    admin = create_db_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dbname}"'))
    test_url = admin_url.rsplit("/", 1)[0] + "/" + dbname
    setup = create_db_engine(test_url)
    with setup.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    return admin_url, test_url


def _drop_temp_db(admin_url: str, dbname: str) -> None:
    from app.core.db import create_db_engine
    from sqlalchemy import text

    admin = create_db_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{dbname}" WITH (FORCE)'))


def _run_worker(model_name: str, candidate_dim: int) -> dict:
    """서브프로세스 본체: dim 패치 → 임베딩·색인·84+질의 평가 → 지연 측정."""
    _patch_embedding_dim(candidate_dim)

    from app.composition import AppState, build_services
    from app.core.db import create_db_engine
    from app.models.openapi import ApiChunk, EMBEDDING_DIM, create_all
    from app.services.indexer.embedding_provider import LocalEmbeddingProvider
    from app.services.ingestor.openapi_fetcher import InMemoryFetcher
    from app.services.search.endpoint_candidate_search import CandidateSearchOptions

    queries = _load_queries(_DIR / "queries.json")
    openapi_doc = (_DIR / "openapi.json").read_text()
    _validate_labels(queries, _load_valid_endpoints(openapi_doc))

    provider = LocalEmbeddingProvider(model_name=model_name)
    assert provider.dim == candidate_dim, (
        f"모델 {model_name}의 실제 dim({provider.dim})이 지정한 candidate_dim({candidate_dim})과 다르다"
        " — VARIANTS 설정을 확인하라."
    )
    assert EMBEDDING_DIM == candidate_dim, "EMBEDDING_DIM 패치가 적용되지 않았다"

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)

        # 못박기(docs/15 §3-1 필수 검증): provider.dim == 실제 생성된 벡터 컬럼 dim.
        # 어긋나면 dim 불일치로 인한 조용한 오색인이 일어날 수 있으므로 여기서 죽는다.
        column_dim = ApiChunk.__table__.c.embedding.type.dim
        assert column_dim == provider.dim, (
            f"벡터 컬럼 dim({column_dim}) != provider.dim({provider.dim}) — EMBEDDING_DIM 패치 실패"
        )

        state = AppState.from_engine(
            engine=engine,
            fetcher=InMemoryFetcher(),
            search_strategy="rrf",
            embedding_provider=provider,
        )
        bundle = next(build_services(state))
        bundle.sync_service.register(project="default", source_url=None, raw_document=openapi_doc)

        ranks: list[int | None] = []
        for eq in queries:
            candidates = bundle.candidate_search.search(eq.query, CandidateSearchOptions(top_k=TOP_K))
            ranks.append(_rank_of_answer(candidates, eq.accepted))

        # 지연 측정(docs/15 §6 G4): 새 provider로 질의당 1회씩만 encode해
        # 캐시 미스 경로(=실사용 대표값)를 측정한다. 워밍업 1회는 측정 제외.
        latency_provider = LocalEmbeddingProvider(model_name=model_name)
        latency_provider.embed_query("__warmup__")
        timings_ms: list[float] = []
        for eq in queries:
            start = time.perf_counter()
            latency_provider.embed_query(eq.query)
            timings_ms.append((time.perf_counter() - start) * 1000)
        timings_ms.sort()
        p95_idx = max(0, int(len(timings_ms) * 0.95) - 1)
        latency = {"mean_ms": statistics.mean(timings_ms), "p95_ms": timings_ms[p95_idx]}

        return {
            "model": model_name,
            "dim": candidate_dim,
            "ranks": ranks,
            "latency": latency,
        }
    finally:
        _drop_temp_db(admin_url, dbname)


def _run_variant_subprocess(model_name: str, candidate_dim: int) -> dict:
    """모델 변형 하나를 격리된 서브프로세스로 실행해 결과 JSON을 받는다."""
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--model",
            model_name,
            "--dim",
            str(candidate_dim),
        ],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"워커 서브프로세스 실패(model={model_name}, dim={candidate_dim}, "
            f"returncode={proc.returncode}):\n{proc.stderr}"
        )
    last_line = proc.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def main() -> None:
    queries = _load_queries(_DIR / "queries.json")
    openapi_doc = (_DIR / "openapi.json").read_text()
    _validate_labels(queries, _load_valid_endpoints(openapi_doc))

    results: list[dict] = []
    for i, variant in enumerate(VARIANTS, start=1):
        print(f"=== {i}/{len(VARIANTS)}: {variant['label']} 서브프로세스 색인 + rrf {len(queries)}질의 실행 ===")
        results.append(_run_variant_subprocess(variant["model"], variant["dim"]))

    baseline, *candidates = results
    baseline_ranks: list[int | None] = baseline["ranks"]

    for candidate, variant in zip(candidates, VARIANTS[1:]):
        candidate_ranks: list[int | None] = candidate["ranks"]
        label = variant["label"]

        print(f"\n| # | 질의 | 카테고리 | 정답 | baseline 순위 | {label} 순위 | 판정 |")
        print("|---|---|---|---|---|---|---|")
        regressions: list[tuple[str, str, int | None, int | None]] = []
        for i, eq in enumerate(queries):
            bl = baseline_ranks[i]
            cd = candidate_ranks[i]
            accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)
            if bl == cd:
                verdict = "무변"
            elif cd is not None and (bl is None or cd < bl):
                verdict = "개선"
            else:
                verdict = "악화"
                regressions.append((eq.query, eq.category, bl, cd))
            bl_disp = bl if bl is not None else "미검출"
            cd_disp = cd if cd is not None else "미검출"
            print(f"| {i + 1} | {eq.query} | {eq.category} | {accepted_str} | {bl_disp} | {cd_disp} | {verdict} |")

        n = len(queries)
        bl_summary = _summarize(baseline_ranks)
        cd_summary = _summarize(candidate_ranks)
        print("\n### 지표 요약(전체)")
        print(f"(n={n})")
        print(_format_summary_line("baseline  ", bl_summary))
        print(_format_summary_line(f"{label}  ", cd_summary))

        print("\n### 카테고리별 분해(Recall@1/3/5/10 · MRR · nDCG@10)")
        categories = sorted({eq.category for eq in queries})
        header_bl = " | ".join(f"baseline R@{k}" for k in RECALL_KS)
        header_cd = " | ".join(f"{label} R@{k}" for k in RECALL_KS)
        print(f"| 카테고리 | n | {header_bl} | baseline MRR | baseline nDCG@10 | {header_cd} | {label} MRR | {label} nDCG@10 |")
        print("|" + "---|" * (3 + len(RECALL_KS) * 2))
        for cat in categories:
            idxs = [i for i, eq in enumerate(queries) if eq.category == cat]
            cat_bl = _summarize([baseline_ranks[i] for i in idxs])
            cat_cd = _summarize([candidate_ranks[i] for i in idxs])
            bl_recalls = " | ".join(f"{cat_bl.recall[k]:.0%}" for k in RECALL_KS)
            cd_recalls = " | ".join(f"{cat_cd.recall[k]:.0%}" for k in RECALL_KS)
            print(
                f"| {cat} | {len(idxs)} | {bl_recalls} | {cat_bl.mrr:.3f} | {cat_bl.ndcg10:.3f} "
                f"| {cd_recalls} | {cat_cd.mrr:.3f} | {cat_cd.ndcg10:.3f} |"
            )

        cross_lingual_cat = next((c for c in categories if c.startswith("교차언어")), None)
        if cross_lingual_cat is not None:
            idxs = [i for i, eq in enumerate(queries) if eq.category == cross_lingual_cat]
            print(f"\n### 교차언어({len(idxs)}건) 질의별 순위 이동")
            print(f"| 질의 | 정답 | baseline 순위 | {label} 순위 | 판정 |")
            print("|---|---|---|---|---|")
            for i in idxs:
                eq = queries[i]
                bl = baseline_ranks[i]
                cd = candidate_ranks[i]
                accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)
                bl_disp = bl if bl is not None else "미검출"
                cd_disp = cd if cd is not None else "미검출"
                if bl == cd:
                    verdict = "무변"
                elif cd is not None and (bl is None or cd < bl):
                    verdict = "개선"
                else:
                    verdict = "악화"
                print(f"| {eq.query} | {accepted_str} | {bl_disp} | {cd_disp} | {verdict} |")

            cross_bl_r3 = sum(recall_at(baseline_ranks[i], 3) for i in idxs)
            cross_cd_r3 = sum(recall_at(candidate_ranks[i], 3) for i in idxs)
            print(
                f"\n교차언어 Recall@3: baseline {cross_bl_r3}/{len(idxs)} "
                f"-> {label} {cross_cd_r3}/{len(idxs)} (delta {cross_cd_r3 - cross_bl_r3:+d}질의)"
            )

        print(f"\n### 타 카테고리 회귀({label}이 baseline보다 나빠진 케이스)")
        non_cross_regressions = [r for r in regressions if not r[1].startswith("교차언어")]
        if non_cross_regressions:
            for q, cat, bl, cd in non_cross_regressions:
                mrr_delta = reciprocal_rank(cd) - reciprocal_rank(bl)
                print(f"- {q!r} ({cat}): baseline={bl} -> {label}={cd} (MRR delta {mrr_delta:+.3f})")
        else:
            print("- 없음")

        bl_latency = baseline["latency"]
        cd_latency = candidate["latency"]
        mean_mult = cd_latency["mean_ms"] / bl_latency["mean_ms"]
        p95_mult = cd_latency["p95_ms"] / bl_latency["p95_ms"]
        print("\n### embed_query 지연(캐시 미스 경로)")
        print(
            f"- baseline: mean {bl_latency['mean_ms']:.2f}ms | p95 {bl_latency['p95_ms']:.2f}ms"
        )
        print(
            f"- {label}: mean {cd_latency['mean_ms']:.2f}ms | p95 {cd_latency['p95_ms']:.2f}ms "
            f"(mean {mean_mult:.2f}x | p95 {p95_mult:.2f}x baseline 대비)"
        )


def _parse_worker_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dim", type=int, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    if "--worker" in sys.argv[1:]:
        args = _parse_worker_args(sys.argv[1:])
        result = _run_worker(args.model, args.dim)
        print(json.dumps(result))
    else:
        main()
