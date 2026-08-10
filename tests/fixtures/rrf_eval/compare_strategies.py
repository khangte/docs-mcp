"""RRF vs fallback 검색 품질 정량 비교 스크립트.

`docs/search-rrf-reevaluation.md` 5.0(전제 B)이 요구한 "최소 평가셋 기반
before/after 측정"의 재실행 가능한 도구다. pytest 로 수집되지 않는 독립
스크립트다(느린 실제 임베딩 모델 로딩·전용 임시 DB 필요 — CI 상시 실행용이
아니라 회귀 의심 시 수동으로 재실행하는 용도).

사용법(로컬 postgres 필요, `docker compose up -d postgres`):
    uv run python tests/fixtures/rrf_eval/compare_strategies.py

`queries.json`(질의·카테고리·정답 라벨)과 `openapi.json`(평가용 API 문서)을
같은 디렉터리에서 읽어, 임시 DB에 등록한 뒤 fallback/rrf 두 전략으로 각
질의를 실행해 정답 순위를 비교한다. 끝나면 임시 DB를 삭제한다.
"""

from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from metrics import dcg_at, recall_at, reciprocal_rank  # type: ignore[import-not-found]
from sqlalchemy import text

from app.composition import AppState, build_services
from app.core.db import create_db_engine
from app.models.openapi import create_all
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from app.services.search.endpoint_candidate_search import CandidateSearchOptions

_DIR = Path(__file__).parent
TOP_K = 10  # 순위 해상도를 넉넉히 보기 위해 10까지 조회(운영 기본 top_k=5보다 넓게)
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
    """queries.json의 모든 accepted가 openapi.json에 실재하는지 확인한다.

    오타 라벨이 조용히 미검출(rank=None)로 집계되는 것을 막기 위해, 스크립트
    실행 초입에 명확한 에러로 죽인다.
    """
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
    dbname = f"rrfeval_{uuid.uuid4().hex[:8]}"
    admin = create_db_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{dbname}"'))
    test_url = admin_url.rsplit("/", 1)[0] + "/" + dbname
    setup = create_db_engine(test_url)
    with setup.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
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


class EvalSummary(NamedTuple):
    recall: dict[int, float]
    mrr: float
    ndcg10: float


def _summarize(ranks: list[int | None]) -> EvalSummary:
    """질의별 정답 순위 리스트에서 Recall@k/MRR/nDCG@10을 계산한다."""
    n = len(ranks)
    recall = {k: sum(recall_at(r, k) for r in ranks) / n for k in RECALL_KS}
    mrr = statistics.mean(reciprocal_rank(r) for r in ranks)
    ndcg10 = statistics.mean(dcg_at(r, 10) for r in ranks)
    return EvalSummary(recall=recall, mrr=mrr, ndcg10=ndcg10)


def _format_summary_line(label: str, summary: EvalSummary) -> str:
    recall_str = " ".join(f"Recall@{k} {summary.recall[k]:.0%}" for k in RECALL_KS)
    return f"- {label}: {recall_str} | MRR {summary.mrr:.3f} | nDCG@10 {summary.ndcg10:.3f}"


def main() -> None:
    queries = _load_queries()
    openapi_doc = (_DIR / "openapi.json").read_text()
    _validate_labels(queries, _load_valid_endpoints(openapi_doc))

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)
        state = AppState.from_engine(engine=engine, fetcher=InMemoryFetcher())
        print("is_semantic:", state.embedding_provider.is_semantic)
        bundle = next(build_services(state))
        bundle.sync_service.register(project="default", source_url=None, raw_document=openapi_doc)

        results: dict[str, dict[str, int | None]] = {}
        for strategy in ("fallback", "rrf"):
            state.search_strategy = strategy
            b = next(build_services(state))
            for eq in queries:
                candidates = b.candidate_search.search(eq.query, CandidateSearchOptions(top_k=TOP_K))
                results.setdefault(eq.query, {})[strategy] = _rank_of_answer(candidates, eq.accepted)

        print("\n| # | 질의 | 카테고리 | 정답 | fallback 순위 | rrf 순위 | 판정 |")
        print("|---|---|---|---|---|---|---|")
        fb_ranks: list[int | None] = []
        rrf_ranks: list[int | None] = []
        regressions: list[tuple[str, int | None, int | None]] = []
        for i, eq in enumerate(queries, start=1):
            fb = results[eq.query]["fallback"]
            rr = results[eq.query]["rrf"]
            fb_ranks.append(fb)
            rrf_ranks.append(rr)
            accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)

            if fb == rr:
                verdict = "무변"
            elif fb is None or (rr is not None and rr < fb):
                verdict = "개선"
            else:
                verdict = "악화"
                regressions.append((eq.query, fb, rr))

            fb_disp = fb if fb is not None else "미검출"
            rr_disp = rr if rr is not None else "미검출"
            print(f"| {i} | {eq.query} | {eq.category} | {accepted_str} | {fb_disp} | {rr_disp} | {verdict} |")

        n = len(queries)
        fb_summary = _summarize(fb_ranks)
        rrf_summary = _summarize(rrf_ranks)
        print("\n### 지표 요약")
        print(f"(n={n})")
        print(_format_summary_line("fallback", fb_summary))
        print(_format_summary_line("rrf     ", rrf_summary))

        print("\n### 카테고리별 분해(Recall@3 / MRR)")
        categories = sorted({eq.category for eq in queries})
        print("| 카테고리 | n | fallback Recall@3 | fallback MRR | rrf Recall@3 | rrf MRR |")
        print("|---|---|---|---|---|---|")
        for cat in categories:
            idxs = [i for i, eq in enumerate(queries) if eq.category == cat]
            cat_fb = _summarize([fb_ranks[i] for i in idxs])
            cat_rrf = _summarize([rrf_ranks[i] for i in idxs])
            print(
                f"| {cat} | {len(idxs)} | {cat_fb.recall[3]:.0%} | {cat_fb.mrr:.3f} "
                f"| {cat_rrf.recall[3]:.0%} | {cat_rrf.mrr:.3f} |"
            )

        print("\n### 회귀(rrf가 fallback보다 나빠진 케이스, MRR 기준 병행 표기)")
        if regressions:
            for q, fb, rr in regressions:
                mrr_delta = reciprocal_rank(rr) - reciprocal_rank(fb)
                print(f"- {q!r}: fallback={fb} -> rrf={rr} (MRR delta {mrr_delta:+.3f})")
        else:
            print("- 없음")
    finally:
        _drop_temp_db(admin_url, dbname)


if __name__ == "__main__":
    main()
