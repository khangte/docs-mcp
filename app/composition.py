"""의존성 컨테이너.

앱 시작 시 만들어진 엔진/프로바이더를 요청 범위 객체와 합쳐 서비스 인스턴스를 만든다.
테스트에서는 `app.dependency_overrides` 로 교체할 수 있게 함수 인터페이스로 제공.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import create_session_factory
from app.models import EMBEDDING_DIM
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.repositories.project_source_repository import ProjectSourceRepository
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.services.documents.document_index_service import DocumentIndexService
from app.services.documents.document_search_service import DocumentSearchService
from app.services.documents.sources.document_source import DocumentSource
from app.services.documents.sources.google_drive_source import ServiceAccountTokenProvider
from app.services.documents.project_source_resolver import ProjectSourceResolver
from app.services.documents.project_source_service import ProjectSourceService
from app.services.documents.sources.source_factory import build_drive_token_provider
from app.services.endpoints.endpoint_details_service import EndpointDetailsService
from app.services.examples.request_example_service import RequestExampleService
from app.services.indexer.embedding_provider import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    LocalEmbeddingProvider,
)
from app.services.indexer.indexer_service import IndexerService
from app.services.ingestor.openapi_fetcher import OpenAPIFetcher
from app.services.ingestor.sync_service import SyncService
from app.services.schema_resolution.schema_ref_resolver import SchemaRefResolver
from app.services.search.endpoint_candidate_search import EndpointCandidateSearch
from app.services.search.keyword_search import KeywordSearch
from app.services.search.vector_search import VectorSearch
from app.services.tags.tag_catalog_service import TagCatalogService


@dataclass
class AppState:
    """앱 전역 상태(엔진/프로바이더 등)를 보관하는 컨테이너."""

    engine: Engine
    session_factory: sessionmaker[Session]
    embedding_provider: EmbeddingProvider
    fetcher: OpenAPIFetcher
    vector_fallback_enabled: bool = True
    #: "rrf"(기본) | "fallback"(롤백 스위치). `EndpointCandidateSearch` 로 그대로
    #: 전달된다.
    search_strategy: str = "rrf"
    #: Drive 서비스 계정 토큰 발급기. 자격증명이 없으면 None. project 마다
    #: 새로 만들지 않고 재사용해 credentials 캐싱 중복을 막는다.
    drive_token_provider: ServiceAccountTokenProvider | None = None
    #: folder_id → Drive 어댑터 팩토리. 테스트에서 페이크를 주입하는 지점.
    #: None 이면 `build_drive_source` 가 기본으로 쓰인다.
    drive_source_builder: Callable[[str], DocumentSource | None] | None = None
    #: (notion_id, kind) → Notion 어댑터 팩토리. 테스트에서 페이크를 주입하는
    #: 지점. None 이면 `build_notion_source` 가 기본으로 쓰인다.
    notion_source_builder: Callable[[str, str], DocumentSource | None] | None = None

    @classmethod
    def from_engine(
        cls,
        engine: Engine,
        fetcher: OpenAPIFetcher,
        vector_fallback_enabled: bool | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        drive_source_builder: Callable[[str], DocumentSource | None] | None = None,
        notion_source_builder: Callable[[str, str], DocumentSource | None] | None = None,
        search_strategy: str | None = None,
    ) -> "AppState":
        """엔진과 fetcher 를 받아 기본 의존성(세션 팩토리·프로바이더)을 채운 AppState 를 만든다.

        `vector_fallback_enabled` 를 생략하면 임베딩 백엔드 설정
        (`DOCS_MCP_EMBEDDING_BACKEND`)으로 결정한다. `embedding_provider` 를
        생략하면 백엔드 설정에 따라 기본 프로바이더를 만든다 — 테스트에서
        무거운 로컬 모델 로딩을 피하려면 `HashEmbeddingProvider` 를 명시적으로
        주입한다(env 오염 없는 dependency override).

        Drive/Notion 어댑터는 여기서 고정 dict 로 만들어 보관하지 않는다.
        `project_source` 매핑은 요청/갱신마다 `ProjectSourceResolver` 가 새로
        조회해 만들어내므로(SPEC 기능 5·6),
        `register_drive_source`/`register_notion_source` 로 매핑을 바꾸면
        서버 재시작 없이 다음 `search_documents`/`refresh_index` 호출부터
        바로 반영된다. `drive_source_builder`/`notion_source_builder` 는
        테스트에서 실제 자격증명 없이 페이크 어댑터를 주입하는 지점이다.
        """
        settings = get_settings()
        provider = (
            embedding_provider if embedding_provider is not None else _build_embedding_provider()
        )
        assert provider.dim == EMBEDDING_DIM, (
            f"임베딩 프로바이더 차원({provider.dim})이 DB 컬럼 차원(EMBEDDING_DIM="
            f"{EMBEDDING_DIM})과 다릅니다 — 모델 교체 시 컬럼도 함께 마이그레이션하세요."
        )
        return cls(
            engine=engine,
            session_factory=create_session_factory(engine),
            embedding_provider=provider,
            fetcher=fetcher,
            vector_fallback_enabled=(
                is_vector_fallback_available()
                if vector_fallback_enabled is None
                else vector_fallback_enabled
            ),
            search_strategy=(
                settings.search_strategy if search_strategy is None else search_strategy
            ),
            drive_token_provider=build_drive_token_provider(settings),
            drive_source_builder=drive_source_builder,
            notion_source_builder=notion_source_builder,
        )


_HASH_BACKEND = "hash"


def is_vector_fallback_available() -> bool:
    """search_endpoints 의 벡터 보조 단계를 쓸 수 있는지 설정으로 판별한다.

    임베딩 백엔드가 해시 폴백이면 의미 유사도가 없어 벡터 보조로서 의미가
    없다. 로컬 모델은 "키 유무" 개념이 없으므로, 판별 기준은 프로바이더가
    실제로 만들어내는 `is_semantic` 값과 동치인 백엔드 설정(local|hash)이다
    — 판별을 위해 무거운 모델을 로드하지 않도록 설정값만으로 판단한다.
    """
    return get_settings().embedding_backend != _HASH_BACKEND


def _build_embedding_provider() -> EmbeddingProvider:
    """임베딩 백엔드 설정에 따라 LocalEmbeddingProvider 또는 HashEmbeddingProvider 를 만든다."""
    settings = get_settings()
    if settings.embedding_backend == _HASH_BACKEND:
        return HashEmbeddingProvider(dim=EMBEDDING_DIM)
    return LocalEmbeddingProvider(model_name=settings.embedding_model)


@dataclass
class ServiceBundle:
    """요청 스코프 서비스 컨테이너."""

    session: Session
    document_repo: DocumentRepository
    endpoint_repo: EndpointRepository
    chunk_repo: ChunkRepository
    sync_history_repo: SyncHistoryRepository
    document_meta_repo: DocumentMetaRepository
    sync_service: SyncService
    example_service: RequestExampleService
    candidate_search: EndpointCandidateSearch
    endpoint_details_service: EndpointDetailsService
    schema_ref_resolver: SchemaRefResolver
    tag_catalog_service: TagCatalogService
    document_search_service: DocumentSearchService
    document_index_service: DocumentIndexService
    project_source_repo: ProjectSourceRepository
    project_source_service: ProjectSourceService
    project_source_resolver: ProjectSourceResolver


def build_services(state: AppState) -> Iterator[ServiceBundle]:
    """요청 스코프 ServiceBundle 을 생성하는 제너레이터.

    요청 종료 시 세션 close.
    """
    session = state.session_factory()
    try:
        document_repo = DocumentRepository(session)
        endpoint_repo = EndpointRepository(session)
        chunk_repo = ChunkRepository(session)
        sync_history_repo = SyncHistoryRepository(session)
        document_meta_repo = DocumentMetaRepository(session)
        indexer = IndexerService(
            endpoint_repo=endpoint_repo,
            chunk_repo=chunk_repo,
            embedding_provider=state.embedding_provider,
        )
        sync_service = SyncService(
            session=session,
            document_repo=document_repo,
            endpoint_repo=endpoint_repo,
            chunk_repo=chunk_repo,
            sync_history_repo=sync_history_repo,
            indexer=indexer,
            fetcher=state.fetcher,
        )
        keyword_search = KeywordSearch(chunk_repo)
        vector_search = VectorSearch(state.embedding_provider, chunk_repo)
        example_service = RequestExampleService(endpoint_repo)
        candidate_search = EndpointCandidateSearch(
            chunk_repo=chunk_repo,
            endpoint_repo=endpoint_repo,
            keyword_search=keyword_search,
            vector_search=vector_search,
            vector_fallback_enabled=state.vector_fallback_enabled,
            document_repo=document_repo,
            search_strategy=state.search_strategy,
        )
        endpoint_details_service = EndpointDetailsService(
            endpoint_repo=endpoint_repo,
            example_service=example_service,
        )
        schema_ref_resolver = SchemaRefResolver(
            endpoint_repo=endpoint_repo,
            document_repo=document_repo,
        )
        tag_catalog_service = TagCatalogService(
            endpoint_repo=endpoint_repo,
            document_repo=document_repo,
        )
        project_source_repo = ProjectSourceRepository(session)
        project_source_service = ProjectSourceService(session, project_source_repo)
        # 매 요청마다 새로 만든다: project_source 매핑을 이 세션 기준으로
        # 즉시 다시 읽으므로, register_drive_source 로 바꾼 값이 서버 재시작
        # 없이 바로 다음 요청에 반영된다(SPEC 377행).
        project_source_resolver = ProjectSourceResolver(
            settings=get_settings(),
            drive_token_provider=state.drive_token_provider,
            source_repo=project_source_repo,
            drive_source_builder=state.drive_source_builder,
            notion_source_builder=state.notion_source_builder,
        )
        document_search_service = DocumentSearchService(
            meta_repo=document_meta_repo,
            resolver=project_source_resolver,
        )
        document_index_service = DocumentIndexService(
            session=session,
            meta_repo=document_meta_repo,
            resolver=project_source_resolver,
            document_repo=document_repo,
            endpoint_repo=endpoint_repo,
            chunk_repo=chunk_repo,
            indexer=indexer,
        )
        yield ServiceBundle(
            session=session,
            document_repo=document_repo,
            endpoint_repo=endpoint_repo,
            chunk_repo=chunk_repo,
            sync_history_repo=sync_history_repo,
            document_meta_repo=document_meta_repo,
            sync_service=sync_service,
            example_service=example_service,
            candidate_search=candidate_search,
            endpoint_details_service=endpoint_details_service,
            schema_ref_resolver=schema_ref_resolver,
            tag_catalog_service=tag_catalog_service,
            document_search_service=document_search_service,
            document_index_service=document_index_service,
            project_source_repo=project_source_repo,
            project_source_service=project_source_service,
            project_source_resolver=project_source_resolver,
        )
    finally:
        session.close()
