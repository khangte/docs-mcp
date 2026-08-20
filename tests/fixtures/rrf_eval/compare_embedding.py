"""임베딩 모델(e5-small vs e5-base) A/B 실측 스크립트.

`docs/architect-review/15_embedding_model_swap_experiment.md` 3절이 정의한 실험 하네스다.
`compare_strategies.py`의 temp-DB 생성/등록/지표 로직을 재사용하되, 바꾸는
변형 축은 "청킹"이 아니라 "임베딩 provider + 벡터 컬럼 dim"이다.

**비자명한 마찰(docs/15 §3-1)**: `Chunk.embedding = mapped_column(Vector(EMBEDDING_DIM))`
은 `app/models/chunk.py` **소스 레벨 상수**로 고정돼 있다. 하지만 pgvector의
`Vector` 타입 객체는 `dim`을 인스턴스 속성으로 들고 있고, DDL을 렌더링하는
`get_col_spec()`이 `create_all()` 호출 시점에 그 속성을 동적으로 읽는다 —
즉 이미 정상적으로 import된 `Chunk.__table__.c.embedding.type.dim`을
`create_all()` 전에 바꿔치기하면, 소스 재작성이나 `sys.modules` 대체 없이도
그 프로세스에서 후보 dim으로 테이블이 만들어진다(§3-1이 우려한 "import 시점
고정"은 소스 상수 얘기고, 런타임 타입 객체는 그 상수를 한 번 읽어 보관만 할
뿐 재고정되지 않는다). 이 스크립트는 여전히 각 변형을 `--worker`
서브프로세스로 격리해 실행한다(변형 간 DB/모델 상태를 완전히 분리하려는
목적, §3-1 권장안). 프로덕션 파일(`app/models/chunk.py`)은 전혀 수정하지
않는다 — 바꿔치기는 이미 메모리에 로드된 컬럼 타입 인스턴스에만 적용된다.

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
from pathlib import Path

from metrics import recall_at, reciprocal_rank  # type: ignore[import-not-found]

from app.composition import AppState, build_services
from app.core.db import create_db_engine
from app.models import Chunk, create_all
from app.services.indexer.embedding_provider import LocalEmbeddingProvider
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from app.services.search.endpoint_candidate_search import CandidateSearchOptions
from compare_strategies import (  # type: ignore[import-not-found]
    RECALL_KS,
    _drop_temp_db,
    _format_summary_line,
    _load_queries,
    _load_valid_endpoints,
    _make_temp_db,
    _rank_of_answer,
    _summarize,
    _validate_labels,
)

_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DIR.parent.parent.parent
TOP_K = 10

#: 실행 축(docs/15 §3-3). baseline=현행, 후보1=1차 드롭인 후보.
VARIANTS = [
    {"label": "baseline(e5-small)", "model": "intfloat/multilingual-e5-small", "dim": 384},
    {"label": "후보1(e5-base)", "model": "intfloat/multilingual-e5-base", "dim": 768},
]


def _patch_embedding_dim(candidate_dim: int) -> None:
    """이미 로드된 pgvector 컬럼 타입과 `EMBEDDING_DIM` 바인딩을 후보 dim으로
    바꿔치기한다.

    - `Chunk.__table__.c.embedding.type.dim`: `create_all()`이 호출하는 DDL
      렌더링(`Vector.get_col_spec()`)이 이 속성을 그 시점에 동적으로 읽으므로,
      바꿔두면 실제 컬럼이 후보 dim으로 생성된다.
    - `app.models.EMBEDDING_DIM`과 `app.composition.EMBEDDING_DIM`: `from X
      import Y`는 값을 복사해 새 이름으로 바인딩하므로, `app.models` 쪽만
      바꾸면 이미 `from app.models import EMBEDDING_DIM`으로 자기 네임스페이스에
      옛 값을 박아둔 `app.composition`은 그대로 384를 본다.
      `AppState.from_engine()`이 `provider.dim == EMBEDDING_DIM`을 단언하므로
      (모델 교체 시 컬럼 dim 불일치를 막는 프로덕션 안전장치) 두 곳 다 바꿔야
      후보 dim으로 정상 부트스트랩된다.

    소스 재작성이나 `sys.modules` 대체는 필요 없다(모듈 상단 docstring 참고).
    """
    import app.composition
    import app.models

    Chunk.__table__.c.embedding.type.dim = candidate_dim
    app.models.EMBEDDING_DIM = candidate_dim
    app.composition.EMBEDDING_DIM = candidate_dim


def _run_worker(model_name: str, candidate_dim: int) -> dict:
    """서브프로세스 본체: dim 패치 → 임베딩·색인·84+질의 평가 → 지연 측정."""
    queries = _load_queries()
    openapi_doc = (_DIR / "openapi.json").read_text()
    _validate_labels(queries, _load_valid_endpoints(openapi_doc))

    provider = LocalEmbeddingProvider(model_name=model_name)
    assert provider.dim == candidate_dim, (
        f"모델 {model_name}의 실제 dim({provider.dim})이 지정한 candidate_dim({candidate_dim})과 다르다"
        " — VARIANTS 설정을 확인하라."
    )

    _patch_embedding_dim(candidate_dim)

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)

        # 못박기(docs/15 §3-1 필수 검증): provider.dim == 실제 생성된 벡터 컬럼 dim.
        # 어긋나면 dim 불일치로 인한 조용한 오색인이 일어날 수 있으므로 여기서 죽는다.
        column_dim = Chunk.__table__.c.embedding.type.dim
        assert column_dim == provider.dim, (
            f"벡터 컬럼 dim({column_dim}) != provider.dim({provider.dim}) — dim 바꿔치기 실패"
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
    queries = _load_queries()
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
