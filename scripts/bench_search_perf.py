"""검색 성능 벤치마크 (`docs/11-search-performance-round2.md` Quick win Q1~Q3 before/after 측정용).

임시 postgres 데이터베이스(pgvector/pg_trgm 확장 포함)를 만들어 규모 있는
코퍼스를 시딩한 뒤, 다음 세 케이스를 반복 호출해 latency(평균/p95)를 잰다.

1. `endpoint_search_global` — `EndpointCandidateSearch`, 스코프 없음(전역)
2. `endpoint_search_narrow` — `EndpointCandidateSearch`, document_id 로 좁힌 스코프
3. `get_document` — `DocumentSearchService.get_document`

실행이 끝나면 만든 데이터베이스를 자동 삭제한다. Q1~Q3 구현 전/후 동일하게
실행해 콘솔 출력(before/after)을 비교하는 방식으로 쓴다.

사용법: `uv run python scripts/bench_search_perf.py`
"""

from __future__ import annotations

import statistics
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text as sa_text

from app.core.config import Settings
from app.core.db import create_db_engine, create_session_factory
from app.models import ApiChunk, ApiDocument, ApiEndpoint, create_all
from app.models.document_meta import SOURCE_DRIVE, DocumentMeta
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.repositories.project_source_repository import (
    ProjectDriveSourceRepository,
    ProjectNotionSourceRepository,
)
from app.services.documents.document_search_service import DocumentSearchService
from app.services.documents.project_source_resolver import ProjectSourceResolver
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.search.endpoint_candidate_search import (
    CandidateSearchOptions,
    EndpointCandidateSearch,
)
from app.services.search.keyword_search import KeywordSearch
from app.services.search.vector_search import VectorSearch

_ADMIN_DATABASE_URL = "postgresql+psycopg://docs_mcp:docs_mcp@localhost:5432/docs_mcp"

#: 코퍼스 규모(엔드포인트 검색용) — 전역 스코프가 "낭비"라고 지목된 상황을
#: 재현할 만큼 문서/청크 수를 키운다.
N_DOCUMENTS = 40
ENDPOINTS_PER_DOC = 15
#: 협업 문서 메타 캐시 규모(get_document O(N) 스캔을 체감할 만큼).
META_ROWS = 800
#: 케이스당 반복 호출 수.
REPEAT = 20
#: 통계 계산 전 버리는 워밍업 호출 수.
WARMUP = 2

_METHODS = ["GET", "POST", "PUT", "DELETE"]
_NOUNS = ["pet", "order", "user", "invoice", "payment", "session", "report", "ticket"]


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _create_bench_engine() -> tuple[object, str]:
    """전용 벤치마크 데이터베이스를 만들고 (engine, db_name) 을 반환한다."""
    db_name = f"bench_{uuid.uuid4().hex[:12]}"
    admin_engine = create_db_engine(_ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    bench_url = _with_database(_ADMIN_DATABASE_URL, db_name)
    setup_engine = create_db_engine(bench_url)
    with setup_engine.begin() as conn:
        conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    setup_engine.dispose()

    engine = create_db_engine(bench_url)
    create_all(engine)
    return engine, db_name


def _drop_bench_database(db_name: str) -> None:
    admin_engine = create_db_engine(_ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
    admin_engine.dispose()


def _seed_endpoint_corpus(session_factory, embedding_provider) -> str:
    """endpoint 청크를 시딩하고 narrow 스코프 벤치용 document_id 를 반환한다."""
    session = session_factory()
    texts: list[str] = []
    rows: list[tuple[ApiEndpoint, ApiChunk]] = []
    target_document_id = ""
    try:
        for doc_index in range(N_DOCUMENTS):
            document_id = f"doc-{doc_index:03d}"
            if doc_index == 0:
                target_document_id = document_id
            session.add(
                ApiDocument(
                    id=document_id,
                    project="default",
                    source_url=None,
                    title=f"문서 {doc_index}",
                    content_hash="hash",
                    raw_text="{}",
                )
            )
            for endpoint_index in range(ENDPOINTS_PER_DOC):
                noun = _NOUNS[(doc_index + endpoint_index) % len(_NOUNS)]
                method = _METHODS[endpoint_index % len(_METHODS)]
                path = f"/{noun}/{doc_index}/{endpoint_index}"
                summary = f"{method} {noun} by id (find {noun} record {doc_index}-{endpoint_index})"
                endpoint_id = f"ep-{doc_index:03d}-{endpoint_index:03d}"
                endpoint = ApiEndpoint(
                    id=endpoint_id,
                    document_id=document_id,
                    method=method,
                    path=path,
                    summary=summary,
                    description="",
                )
                chunk = ApiChunk(
                    id=f"chunk-{doc_index:03d}-{endpoint_index:03d}",
                    document_id=document_id,
                    chunk_type="endpoint",
                    ref_id=endpoint_id,
                    text=summary,
                )
                texts.append(summary)
                rows.append((endpoint, chunk))

        vectors = embedding_provider.embed_documents(texts)
        for (endpoint, chunk), vector in zip(rows, vectors, strict=True):
            chunk.embedding = vector
            session.add(endpoint)
            session.add(chunk)
        session.commit()
    finally:
        session.close()
    return target_document_id


def _seed_document_meta(session_factory) -> str:
    """META_ROWS 개 document_meta 행을 시딩하고 벤치 대상 external_id 를 반환한다."""
    session = session_factory()
    target_external_id = ""
    try:
        now = datetime.now(timezone.utc)
        for index in range(META_ROWS):
            external_id = f"meta-{index:04d}"
            if index == META_ROWS // 2:
                target_external_id = external_id
            session.add(
                DocumentMeta(
                    project="default",
                    source=SOURCE_DRIVE,
                    external_id=external_id,
                    title=f"문서 {index} 가이드",
                    url=f"https://example.test/drive/{external_id}",
                    modified_at=now,
                    last_synced_at=now,
                )
            )
        session.commit()
    finally:
        session.close()
    return target_external_id


class _FakeDriveSource:
    """get_document 벤치용 초경량 페이크 — fetch 비용을 무시할 수 있게 즉시 반환한다."""

    source_name = SOURCE_DRIVE

    def list_files(self):  # pragma: no cover - 벤치에서는 쓰지 않음
        return []

    def fetch(self, external_id: str):
        from app.services.documents.sources.document_source import FetchedDocument

        return FetchedDocument(text=f"본문 {external_id}", truncated=False)


def _make_resolver(session_factory) -> tuple[ProjectSourceResolver, object]:
    """벤치용 resolver 를 만든다.

    resolver 가 내부적으로 물고 있는 `drive_repo`/`notion_repo` 는 이 함수가 연
    session 을 계속 참조한다(get_document 벤치가 매 호출 `resolve_for_project()`
    로 다시 조회하므로). 그 session 은 호출측이 반환값으로 받아 명시적으로
    close 해야 한다 — 열어둔 채 두면 `DROP DATABASE ... WITH (FORCE)` 가 이
    세션을 강제 종료시켜 매 실행마다 SQLAlchemy pool 의 reset 실패 트레이스백이
    stderr 에 찍힌다(측정값 자체는 영향 없지만 출력 신뢰도를 해친다).
    """
    session = session_factory()
    drive_repo = ProjectDriveSourceRepository(session)
    notion_repo = ProjectNotionSourceRepository(session)
    drive_repo.upsert("default", "folder-bench")
    session.commit()
    fake = _FakeDriveSource()
    resolver = ProjectSourceResolver(
        settings=Settings(),
        drive_token_provider=None,
        drive_repo=drive_repo,
        notion_repo=notion_repo,
        drive_source_builder=lambda folder_id: fake,
        notion_source_builder=lambda notion_id, kind: None,
    )
    return resolver, session


def _percentile(values: list[float], pct: float) -> float:
    """values 의 pct(0~100) 백분위수를 최근접 순위법으로 계산한다."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[rank]


def _time_calls(label: str, fn, repeat: int = REPEAT, warmup: int = WARMUP) -> None:
    """fn() 을 warmup + repeat 회 호출해 latency(ms) 평균/p95 를 출력한다."""
    for _ in range(warmup):
        fn()
    samples_ms: list[float] = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples_ms.append((time.perf_counter() - started) * 1000)
    mean_ms = statistics.mean(samples_ms)
    p95_ms = _percentile(samples_ms, 95)
    print(f"  {label:<28} mean={mean_ms:8.2f}ms  p95={p95_ms:8.2f}ms  (n={repeat})")


def main() -> None:
    print(
        f"벤치마크 DB 생성 중... (N_DOCUMENTS={N_DOCUMENTS}, "
        f"ENDPOINTS_PER_DOC={ENDPOINTS_PER_DOC}, META_ROWS={META_ROWS})"
    )
    engine, db_name = _create_bench_engine()
    resolver_session = None
    try:
        session_factory = create_session_factory(engine)
        embedding_provider = HashEmbeddingProvider(dim=384)

        narrow_document_id = _seed_endpoint_corpus(session_factory, embedding_provider)
        target_external_id = _seed_document_meta(session_factory)
        resolver, resolver_session = _make_resolver(session_factory)

        print(
            f"시딩 완료 (narrow_document_id={narrow_document_id}, "
            f"target_external_id={target_external_id})"
        )
        print(f"반복 횟수: {REPEAT}회 (워밍업 {WARMUP}회 제외)\n")

        def _run_endpoint_search(document_id: str | None) -> None:
            session = session_factory()
            try:
                chunk_repo = ChunkRepository(session)
                endpoint_repo = EndpointRepository(session)
                document_repo = DocumentRepository(session)
                search = EndpointCandidateSearch(
                    chunk_repo=chunk_repo,
                    endpoint_repo=endpoint_repo,
                    keyword_search=KeywordSearch(chunk_repo),
                    vector_search=VectorSearch(embedding_provider, chunk_repo),
                    document_repo=document_repo,
                )
                search.search(
                    "find pet order user record",
                    CandidateSearchOptions(top_k=10, document_id=document_id),
                )
            finally:
                session.close()

        def _run_get_document() -> None:
            session = session_factory()
            try:
                service = DocumentSearchService(
                    meta_repo=DocumentMetaRepository(session), resolver=resolver
                )
                service.get_document(SOURCE_DRIVE, target_external_id)
            finally:
                session.close()

        print("[EndpointCandidateSearch]")
        _time_calls("endpoint_search_global", lambda: _run_endpoint_search(None))
        _time_calls("endpoint_search_narrow", lambda: _run_endpoint_search(narrow_document_id))
        print("\n[DocumentSearchService.get_document]")
        _time_calls("get_document", _run_get_document)
    finally:
        if resolver_session is not None:
            resolver_session.close()
        engine.dispose()
        _drop_bench_database(db_name)
        print(f"\n벤치마크 DB 삭제 완료 ({db_name})")


if __name__ == "__main__":
    main()
