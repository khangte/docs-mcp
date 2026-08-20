"""실 코퍼스(Stripe/GitHub OpenAPI) 기반 검색 품질 평가 스크립트.

`docs/architect-review/27_search_quality_eval_real_corpus_design.md` §7 계약의
구현이다. `tests/fixtures/rrf_eval/compare_strategies.py`(synthetic 20-엔드포인트
하네스)의 DB·순위·지표·요약 로직을 그대로 재사용하고(§7.1), 이 스크립트가
새로 갖는 것은 (1) 코퍼스 매니페스트 로더 (2) 다-문서 라벨 검증 게이트뿐이다.

pytest로 수집되지 않는 독립 스크립트다(대형 스펙 색인 + 실 임베딩 모델
로딩이 무거워 CI 상시 실행용이 아니라 수동 회귀 재실행 용도).

사용법(로컬 postgres 필요, `docker compose up -d postgres`):
    uv run python tests/fixtures/corpus_eval/run_corpus_eval.py [--strategy rrf|fallback|both] [--top-k 10]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).parent

# compare_strategies.py는 스크립트 직접 실행(같은 디렉터리가 sys.path[0])을
# 전제로 `from metrics import ...`(flat import)한다. 같은 방식으로 재사용하려고
# rrf_eval 디렉터리를 sys.path에 얹고 flat import한다(패키지 경로 임포트는
# "tests"가 sys.path에 없는 직접 스크립트 실행에서 깨진다).
sys.path.insert(0, str(_DIR.parent / "rrf_eval"))

from compare_strategies import (  # noqa: E402  type: ignore[import-not-found]
    TOP_K,
    _drop_temp_db,
    _format_summary_line,
    _make_temp_db,
    _rank_of_answer,
    _summarize,
)

from app.composition import AppState, build_services
from app.core.db import create_db_engine
from app.models import create_all
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from app.services.search.endpoint_candidate_search import CandidateSearchOptions


@dataclass
class EvalQuery:
    id: str
    query: str
    category: str
    accepted: list[tuple[str, str]]  # (method, path) — doc은 §3.3 검증에만 쓰고 채점에는 무관
    #: 클라 LLM이 함께 제공했을 영문 변형(query_variants). --with-variants 일 때만 사용.
    variants: list[str]


def _load_manifest() -> list[dict]:
    return json.loads((_DIR / "corpus_manifest.json").read_text())


def _load_corpus_texts(manifest: list[dict]) -> dict[str, str]:
    """소스 키 → 원문. 프리즈된 파일이 매니페스트의 content_sha256과 일치하는지 검증한다."""
    texts: dict[str, str] = {}
    for entry in manifest:
        raw = (_DIR / entry["file"]).read_text()
        actual = hashlib.sha256(raw.encode()).hexdigest()
        if actual != entry["content_sha256"]:
            raise ValueError(
                f"content_sha256 불일치: {entry['source_key']}({entry['file']}) "
                f"— 스펙이 재수집되었거나 변조되었을 수 있음"
            )
        texts[entry["source_key"]] = raw
    return texts


def _valid_endpoints_by_doc(texts: dict[str, str]) -> dict[str, set[tuple[str, str]]]:
    result: dict[str, set[tuple[str, str]]] = {}
    for source_key, raw in texts.items():
        paths = json.loads(raw)["paths"]
        result[source_key] = {(m.upper(), p) for p, methods in paths.items() for m in methods}
    return result


def _load_and_validate_queries(valid_by_doc: dict[str, set[tuple[str, str]]]) -> list[EvalQuery]:
    """queries.json을 읽고, 다-문서 버전 라벨 검증 게이트를 통과시킨다(§3.3).

    오타/추정 라벨이 조용히 미검출(rank=None)로 집계되는 것을 막기 위해,
    실행 초입에 명확한 에러로 죽인다.
    """
    raw_items = json.loads((_DIR / "queries.json").read_text())
    bad: list[tuple[str, str, str, str]] = []
    for item in raw_items:
        for acc in item["accepted"]:
            doc, method, path = acc["doc"], acc["method"], acc["path"]
            if (method, path) not in valid_by_doc.get(doc, set()):
                bad.append((item["query"], doc, method, path))
    if bad:
        raise ValueError(f"미존재 라벨(프리즈 코퍼스에 없는 accepted 엔드포인트): {bad}")

    return [
        EvalQuery(
            id=item["id"],
            query=item["query"],
            category=item["category"],
            accepted=[(acc["method"], acc["path"]) for acc in item["accepted"]],
            variants=item.get("variants", []),
        )
        for item in raw_items
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("rrf", "fallback", "both"), default="both")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument(
        "--with-variants",
        action="store_true",
        help="queries.json의 variants(클라 LLM이 제공했을 영문 변형)를 query_variants로 함께 넘겨 재측정한다(doc/30 §7.3).",
    )
    return parser.parse_args()


def _run_strategy(
    bundle, queries: list[EvalQuery], top_k: int, with_variants: bool
) -> list[int | None]:
    return [
        _rank_of_answer(
            bundle.candidate_search.search(
                eq.query,
                CandidateSearchOptions(
                    top_k=top_k,
                    query_variants=eq.variants if with_variants and eq.variants else None,
                ),
            ),
            eq.accepted,
        )
        for eq in queries
    ]


def _print_category_breakdown(queries: list[EvalQuery], ranks_by_strategy: dict[str, list[int | None]]) -> None:
    print("\n### 카테고리별 분해(Recall@3 / MRR)")
    categories = sorted({eq.category for eq in queries})
    strategies = list(ranks_by_strategy)
    header = " | ".join(f"{s} Recall@3 | {s} MRR" for s in strategies)
    print(f"| 카테고리 | n | {header} |")
    print("|---|---|" + "---|" * (2 * len(strategies)))
    for cat in categories:
        idxs = [i for i, eq in enumerate(queries) if eq.category == cat]
        cells = []
        for s in strategies:
            summary = _summarize([ranks_by_strategy[s][i] for i in idxs])
            cells.append(f"{summary.recall[3]:.0%} | {summary.mrr:.3f}")
        print(f"| {cat} | {len(idxs)} | " + " | ".join(cells) + " |")


def main() -> None:
    args = _parse_args()
    strategies = ("fallback", "rrf") if args.strategy == "both" else (args.strategy,)

    manifest = _load_manifest()
    texts = _load_corpus_texts(manifest)
    queries = _load_and_validate_queries(_valid_endpoints_by_doc(texts))
    doc_type_by_key = {e["source_key"]: e["doc_type"] for e in manifest}

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)
        state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
        print("is_semantic:", state.embedding_provider.is_semantic)
        print("with_variants:", args.with_variants)
        bundle = next(build_services(state))
        for source_key, raw in texts.items():
            result = bundle.sync_service.register(
                project="default",
                source_url=None,
                raw_document=raw,
                doc_type=doc_type_by_key[source_key],
            )
            print(f"등록: {source_key} -> document_id={result.document.id} endpoints={result.endpoints_count}")

        ranks_by_strategy: dict[str, list[int | None]] = {}
        for strategy in strategies:
            state.search_strategy = strategy
            b = next(build_services(state))
            ranks_by_strategy[strategy] = _run_strategy(b, queries, args.top_k, args.with_variants)

        print(f"\n| # | 질의 | 카테고리 | 정답 | " + " | ".join(f"{s} 순위" for s in strategies) + " |")
        print("|---|---|---|---|" + "---|" * len(strategies))
        for i, eq in enumerate(queries):
            accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)
            cells = []
            for s in strategies:
                r = ranks_by_strategy[s][i]
                cells.append(str(r) if r is not None else "미검출")
            print(f"| {eq.id} | {eq.query} | {eq.category} | {accepted_str} | " + " | ".join(cells) + " |")

        print("\n### 지표 요약")
        print(f"(n={len(queries)}, top_k={args.top_k})")
        for s in strategies:
            print(_format_summary_line(s, _summarize(ranks_by_strategy[s])))

        _print_category_breakdown(queries, ranks_by_strategy)

        if args.strategy == "both":
            print("\n### 회귀(rrf가 fallback보다 나빠진 케이스, MRR 기준 병행 표기)")
            fb_ranks, rrf_ranks = ranks_by_strategy["fallback"], ranks_by_strategy["rrf"]
            regressions = [
                (eq.query, fb, rr)
                for eq, fb, rr in zip(queries, fb_ranks, rrf_ranks, strict=True)
                if fb != rr and not (fb is None or (rr is not None and rr < fb))
            ]
            if regressions:
                from metrics import reciprocal_rank  # type: ignore[import-not-found]

                for q, fb, rr in regressions:
                    mrr_delta = reciprocal_rank(rr) - reciprocal_rank(fb)
                    print(f"- {q!r}: fallback={fb} -> rrf={rr} (MRR delta {mrr_delta:+.3f})")
            else:
                print("- 없음")
    finally:
        _drop_temp_db(admin_url, dbname)


if __name__ == "__main__":
    main()
