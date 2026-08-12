"""청킹 전략(baseline vs tag전용) A/B 실측 스크립트.

`docs/architect-review/13-rag-context-chunking-experiment.md` 4절이 정의한 실험 하네스다.
`compare_strategies.py`의 임시 DB 생성/등록/지표 로직을 재사용하되, 전략은
`rrf`로 고정하고 **청킹만** baseline vs 방향1 tag전용 변형으로 비교한다.

프로덕션 코드는 한 줄도 건드리지 않는다 — `app.services.indexer.indexer_service`
모듈이 `chunk_builder.build_chunks`를 바인딩해 쓰는 지점을, 이 스크립트가 실행
중에만 몽키패치해 tag전용 변형으로 갈아끼운다.

사용법(로컬 postgres 필요, `docker compose up -d postgres`):
    uv run python tests/fixtures/rrf_eval/compare_chunking.py
"""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

from metrics import dcg_at, recall_at, reciprocal_rank  # type: ignore[import-not-found]
from sqlalchemy import text

import app.services.indexer.indexer_service as indexer_service_module
from app.composition import AppState, build_services
from app.core.db import create_db_engine
from app.models.openapi import create_all
from app.services.indexer.chunk_builder import (
    BuiltChunk,
    build_chunks as build_chunks_baseline,
    build_endpoint_chunk_text,
)
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from app.services.parser.openapi_parser import ParsedDocument
from app.services.search.endpoint_candidate_search import CandidateSearchOptions

_DIR = Path(__file__).parent
TOP_K = 10
RECALL_KS = (1, 3, 5, 10)


@dataclass
class EvalQuery:
    query: str
    category: str
    accepted: list[tuple[str, str]]


def _load_queries() -> list[EvalQuery]:
    raw = json.loads((_DIR / "queries.json").read_text())
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


def _make_temp_db() -> tuple[str, str]:
    """관리자 접속 URL과 새로 만든 임시 DB의 접속 URL을 반환한다."""
    admin_url = "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
    dbname = f"chunkeval_{uuid.uuid4().hex[:8]}"
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
    admin = create_db_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{dbname}" WITH (FORCE)'))


def _rank_of_answer(candidates, accepted: list[tuple[str, str]]) -> int | None:
    accepted_set = set(accepted)
    for i, c in enumerate(candidates, start=1):
        if (c.method, c.path) in accepted_set:
            return i
    return None


def build_endpoint_chunk_text_contextual(endpoint) -> str:
    """방향1 tag-only 변형: 대표 tag만 엔드포인트 청크 앞에 접두한다(`API: {title}` 줄 제거).

    `docs/13` §5-실측 후속 지시: title+tag 변형의 교차언어 이득이 유일한 한글
    doc title이 전 청크에 번진 픽스처 아티팩트인지 확인하기 위해 title을 뺀다.
    """
    if not endpoint.tags:
        return build_endpoint_chunk_text(endpoint)
    return f"Tag: {endpoint.tags[0]}\n" + build_endpoint_chunk_text(endpoint)


def build_chunks_contextual(
    document: ParsedDocument,
    endpoint_ids: dict[tuple[str, str], str],
    section_ids: dict[int, str] | None = None,
) -> list[BuiltChunk]:
    """`chunk_builder.build_chunks`를 미러링하되 엔드포인트 청크만 tag를 접두한다.

    스키마/섹션 청크는 baseline과 동일 — 방향1 실험 대상이 엔드포인트 청크뿐이므로.
    """
    chunks: list[BuiltChunk] = []
    for endpoint in document.endpoints:
        eid = endpoint_ids.get((endpoint.method, endpoint.path))
        if not eid:
            continue
        chunks.append(
            BuiltChunk(
                chunk_type="endpoint",
                ref_id=eid,
                text=build_endpoint_chunk_text_contextual(endpoint),
            )
        )
    baseline_chunks = build_chunks_baseline(document, endpoint_ids, section_ids)
    chunks.extend(c for c in baseline_chunks if c.chunk_type != "endpoint")
    return chunks


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


def _run_variant(
    openapi_doc: str,
    queries: list[EvalQuery],
    build_chunks_fn: Callable[..., list[BuiltChunk]],
) -> list[int | None]:
    """임시 DB를 만들어 `build_chunks_fn`으로 등록하고 rrf 전략으로 84질의를 실행한다."""
    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    original_build_chunks = indexer_service_module.build_chunks
    indexer_service_module.build_chunks = build_chunks_fn
    try:
        engine = create_db_engine(test_url)
        create_all(engine)
        state = AppState.from_engine(
            engine=engine, fetcher=InMemoryFetcher(), search_strategy="rrf"
        )
        bundle = next(build_services(state))
        bundle.sync_service.register(project="default", source_url=None, raw_document=openapi_doc)

        ranks: list[int | None] = []
        for eq in queries:
            candidates = bundle.candidate_search.search(eq.query, CandidateSearchOptions(top_k=TOP_K))
            ranks.append(_rank_of_answer(candidates, eq.accepted))
        return ranks
    finally:
        indexer_service_module.build_chunks = original_build_chunks
        _drop_temp_db(admin_url, dbname)


def main() -> None:
    queries = _load_queries()
    openapi_doc = (_DIR / "openapi.json").read_text()
    _validate_labels(queries, _load_valid_endpoints(openapi_doc))

    print("=== 1/2: baseline 청킹 등록 + rrf 84질의 실행 ===")
    baseline_ranks = _run_variant(openapi_doc, queries, build_chunks_baseline)

    print("=== 2/2: tag전용 변형(방향1) 청킹 등록 + rrf 84질의 실행 ===")
    contextual_ranks = _run_variant(openapi_doc, queries, build_chunks_contextual)

    print("\n| # | 질의 | 카테고리 | 정답 | baseline 순위 | tag전용 순위 | 판정 |")
    print("|---|---|---|---|---|---|---|")
    regressions: list[tuple[str, str, int | None, int | None]] = []
    improvements: list[tuple[str, str, int | None, int | None]] = []
    for i, eq in enumerate(queries):
        bl = baseline_ranks[i]
        ctx = contextual_ranks[i]
        accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)

        if bl == ctx:
            verdict = "무변"
        elif ctx is not None and (bl is None or ctx < bl):
            verdict = "개선"
            improvements.append((eq.query, eq.category, bl, ctx))
        else:
            verdict = "악화"
            regressions.append((eq.query, eq.category, bl, ctx))

        bl_disp = bl if bl is not None else "미검출"
        ctx_disp = ctx if ctx is not None else "미검출"
        print(f"| {i + 1} | {eq.query} | {eq.category} | {accepted_str} | {bl_disp} | {ctx_disp} | {verdict} |")

    n = len(queries)
    bl_summary = _summarize(baseline_ranks)
    ctx_summary = _summarize(contextual_ranks)
    print("\n### 지표 요약(전체)")
    print(f"(n={n})")
    print(_format_summary_line("baseline  ", bl_summary))
    print(_format_summary_line("tag전용  ", ctx_summary))

    print("\n### 카테고리별 분해(Recall@1/3/5/10 · MRR · nDCG@10)")
    categories = sorted({eq.category for eq in queries})
    header_ks = " | ".join(f"baseline R@{k}" for k in RECALL_KS)
    header_ks_ctx = " | ".join(f"tag전용 R@{k}" for k in RECALL_KS)
    print(f"| 카테고리 | n | {header_ks} | baseline MRR | baseline nDCG@10 | {header_ks_ctx} | tag전용 MRR | tag전용 nDCG@10 |")
    print("|" + "---|" * (3 + len(RECALL_KS) * 2))
    for cat in categories:
        idxs = [i for i, eq in enumerate(queries) if eq.category == cat]
        cat_bl = _summarize([baseline_ranks[i] for i in idxs])
        cat_ctx = _summarize([contextual_ranks[i] for i in idxs])
        bl_recalls = " | ".join(f"{cat_bl.recall[k]:.0%}" for k in RECALL_KS)
        ctx_recalls = " | ".join(f"{cat_ctx.recall[k]:.0%}" for k in RECALL_KS)
        print(
            f"| {cat} | {len(idxs)} | {bl_recalls} | {cat_bl.mrr:.3f} | {cat_bl.ndcg10:.3f} "
            f"| {ctx_recalls} | {cat_ctx.mrr:.3f} | {cat_ctx.ndcg10:.3f} |"
        )

    cross_lingual_cat = next(
        (c for c in categories if c.startswith("교차언어")), None
    )
    if cross_lingual_cat is not None:
        idxs = [i for i, eq in enumerate(queries) if eq.category == cross_lingual_cat]
        print(f"\n### 교차언어({len(idxs)}건) 질의별 순위 이동")
        print("| 질의 | 정답 | baseline 순위 | tag전용 순위 | 판정 |")
        print("|---|---|---|---|---|")
        for i in idxs:
            eq = queries[i]
            bl = baseline_ranks[i]
            ctx = contextual_ranks[i]
            accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)
            bl_disp = bl if bl is not None else "미검출"
            ctx_disp = ctx if ctx is not None else "미검출"
            if bl == ctx:
                verdict = "무변"
            elif ctx is not None and (bl is None or ctx < bl):
                verdict = "개선"
            else:
                verdict = "악화"
            print(f"| {eq.query} | {accepted_str} | {bl_disp} | {ctx_disp} | {verdict} |")

        cross_bl_r3 = sum(recall_at(baseline_ranks[i], 3) for i in idxs)
        cross_ctx_r3 = sum(recall_at(contextual_ranks[i], 3) for i in idxs)
        print(
            f"\n교차언어 Recall@3: baseline {cross_bl_r3}/{len(idxs)} "
            f"-> tag전용 {cross_ctx_r3}/{len(idxs)} (delta {cross_ctx_r3 - cross_bl_r3:+d}질의)"
        )

    print("\n### 타 카테고리 회귀(tag전용이 baseline보다 나빠진 케이스)")
    non_cross_regressions = [r for r in regressions if not r[1].startswith("교차언어")]
    if non_cross_regressions:
        for q, cat, bl, ctx in non_cross_regressions:
            mrr_delta = reciprocal_rank(ctx) - reciprocal_rank(bl)
            print(f"- {q!r} ({cat}): baseline={bl} -> tag전용={ctx} (MRR delta {mrr_delta:+.3f})")
    else:
        print("- 없음")


if __name__ == "__main__":
    main()
