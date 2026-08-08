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
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from app.composition import AppState, build_services
from app.core.db import create_db_engine
from app.models.openapi import create_all
from app.services.ingestor.openapi_fetcher import InMemoryFetcher
from app.services.search.endpoint_candidate_search import CandidateSearchOptions

_DIR = Path(__file__).parent
TOP_K = 10  # 순위 해상도를 넉넉히 보기 위해 10까지 조회(운영 기본 top_k=5보다 넓게)


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


def main() -> None:
    queries = _load_queries()
    openapi_doc = (_DIR / "openapi.json").read_text()

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
        fb_top1 = fb_top3 = rrf_top1 = rrf_top3 = 0
        regressions: list[tuple[str, int | None, int | None]] = []
        for i, eq in enumerate(queries, start=1):
            fb = results[eq.query]["fallback"]
            rr = results[eq.query]["rrf"]
            accepted_str = " or ".join(f"{m} {p}" for m, p in eq.accepted)
            fb_top1 += 1 if fb is not None and fb <= 1 else 0
            fb_top3 += 1 if fb is not None and fb <= 3 else 0
            rrf_top1 += 1 if rr is not None and rr <= 1 else 0
            rrf_top3 += 1 if rr is not None and rr <= 3 else 0

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
        print("\n### 지표 요약")
        print(f"- fallback: top-1 정확도 {fb_top1}/{n} ({fb_top1 / n:.0%}), top-3 recall {fb_top3}/{n} ({fb_top3 / n:.0%})")
        print(f"- rrf     : top-1 정확도 {rrf_top1}/{n} ({rrf_top1 / n:.0%}), top-3 recall {rrf_top3}/{n} ({rrf_top3 / n:.0%})")

        print("\n### 회귀(rrf가 fallback보다 나빠진 케이스)")
        if regressions:
            for q, fb, rr in regressions:
                print(f"- {q!r}: fallback={fb} -> rrf={rr}")
        else:
            print("- 없음")
    finally:
        _drop_temp_db(admin_url, dbname)


if __name__ == "__main__":
    main()
