"""FastAPI 의존성 컨테이너.

앱 시작 시 만들어진 엔진/프로바이더를 요청 범위 객체와 합쳐 서비스 인스턴스를 만든다.
테스트에서는 `app.dependency_overrides` 로 교체할 수 있게 함수 인터페이스로 제공.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import create_session_factory
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_meta_repository import DocumentMetaRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.services.documents.document_index_service import DocumentIndexService
from app.services.documents.document_search_service import DocumentSearchService
from app.services.documents.document_source import DocumentSource
from app.services.documents.source_factory import build_document_sources
from app.services.endpoints.endpoint_details_service import EndpointDetailsService
from app.services.examples.request_example_service import RequestExampleService
from app.services.indexer.embedding_provider import (
    EmbeddingProvider,
    GeminiEmbeddingProvider,
    HashEmbeddingProvider,
)
from app.services.indexer.indexer_service import IndexerService
from app.services.ingestor.openapi_fetcher import OpenAPIFetcher
from app.services.ingestor.sync_service import SyncService
from app.services.rag.llm_provider import GeminiLLMProvider, LLMProvider, TemplateLLMProvider
from app.services.rag.rag_service import RAGService
from app.services.schemas.schema_ref_resolver import SchemaRefResolver
from app.services.search.endpoint_candidate_search import EndpointCandidateSearch
from app.services.search.keyword_search import KeywordSearch
from app.services.search.search_service import SearchService
from app.services.search.vector_search import VectorSearch
from app.services.tags.tag_catalog_service import TagCatalogService


@dataclass
class AppState:
    """앱 전역 상태(엔진/프로바이더 등)를 보관하는 컨테이너."""

    engine: Engine
    session_factory: sessionmaker[Session]
    embedding_provider: EmbeddingProvider
    llm_provider: LLMProvider
    fetcher: OpenAPIFetcher
    hybrid_alpha: float = 0.4
    vector_fallback_enabled: bool = True
    #: Drive/Notion 어댑터 매핑(`drive`/`notion` → 어댑터). 자격증명이 없으면 빈 dict.
    document_sources: dict[str, DocumentSource] = field(default_factory=dict)

    @classmethod
    def from_engine(
        cls,
        engine: Engine,
        fetcher: OpenAPIFetcher,
        embedding_dim: int = 256,
        hybrid_alpha: float = 0.4,
        vector_fallback_enabled: bool | None = None,
        document_sources: dict[str, DocumentSource] | None = None,
    ) -> "AppState":
        """엔진과 fetcher 를 받아 기본 의존성(세션 팩토리·프로바이더)을 채운 AppState 를 만든다.

        `vector_fallback_enabled` 를 생략하면 설정의 Gemini API 키 유무로 결정한다.
        `document_sources` 를 생략하면 설정값에서 구성 가능한 Drive/Notion
        어댑터만 자동으로 채운다(테스트에서는 페이크를 명시 주입한다).
        """
        return cls(
            engine=engine,
            session_factory=create_session_factory(engine),
            embedding_provider=_build_embedding_provider(embedding_dim),
            llm_provider=_build_llm_provider(),
            fetcher=fetcher,
            hybrid_alpha=hybrid_alpha,
            vector_fallback_enabled=(
                is_vector_fallback_available()
                if vector_fallback_enabled is None
                else vector_fallback_enabled
            ),
            document_sources=(
                build_document_sources(get_settings())
                if document_sources is None
                else dict(document_sources)
            ),
        )


def is_vector_fallback_available() -> bool:
    """search_endpoints 의 벡터 보조 단계를 쓸 수 있는지 설정으로 판별한다.

    Gemini API 키가 없으면 임베딩 프로바이더가 HashEmbeddingProvider 로
    폴백되는데, 해시 임베딩은 의미 유사도가 없어 벡터 보조로서 의미가 없다.
    프로바이더 클래스 종류가 아니라 설정값(키 유무)을 판별 기준으로 삼아
    폴백 구현이 바뀌어도 이 판단이 흔들리지 않게 한다.
    """
    return bool(get_settings().gemini_api_key)


def _build_llm_provider() -> LLMProvider:
    """Gemini API 키가 설정돼 있으면 GeminiLLMProvider 를, 아니면 TemplateLLMProvider 로 폴백."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return TemplateLLMProvider()
    return GeminiLLMProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)


def _build_embedding_provider(embedding_dim: int) -> EmbeddingProvider:
    """Gemini API 키가 있으면 GeminiEmbeddingProvider, 없으면 HashEmbeddingProvider 로 폴백."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return HashEmbeddingProvider(dim=embedding_dim)
    return GeminiEmbeddingProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_embedding_model,
        dim=embedding_dim,
    )


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
    search_service: SearchService
    rag_service: RAGService
    example_service: RequestExampleService
    candidate_search: EndpointCandidateSearch
    endpoint_details_service: EndpointDetailsService
    schema_ref_resolver: SchemaRefResolver
    tag_catalog_service: TagCatalogService
    document_search_service: DocumentSearchService
    document_index_service: DocumentIndexService


def build_services(state: AppState) -> Iterator[ServiceBundle]:
    """FastAPI Depends 로 사용되는 생성기.

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
        search_service = SearchService(
            chunk_repo=chunk_repo,
            endpoint_repo=endpoint_repo,
            keyword_search=keyword_search,
            vector_search=vector_search,
            hybrid_alpha=state.hybrid_alpha,
        )
        rag_service = RAGService(search_service, state.llm_provider)
        example_service = RequestExampleService(endpoint_repo)
        candidate_search = EndpointCandidateSearch(
            chunk_repo=chunk_repo,
            endpoint_repo=endpoint_repo,
            keyword_search=keyword_search,
            vector_search=vector_search,
            vector_fallback_enabled=state.vector_fallback_enabled,
            document_repo=document_repo,
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
        document_search_service = DocumentSearchService(
            meta_repo=document_meta_repo,
            sources=state.document_sources,
        )
        document_index_service = DocumentIndexService(
            session=session,
            meta_repo=document_meta_repo,
            sources=list(state.document_sources.values()),
        )
        yield ServiceBundle(
            session=session,
            document_repo=document_repo,
            endpoint_repo=endpoint_repo,
            chunk_repo=chunk_repo,
            sync_history_repo=sync_history_repo,
            document_meta_repo=document_meta_repo,
            sync_service=sync_service,
            search_service=search_service,
            rag_service=rag_service,
            example_service=example_service,
            candidate_search=candidate_search,
            endpoint_details_service=endpoint_details_service,
            schema_ref_resolver=schema_ref_resolver,
            tag_catalog_service=tag_catalog_service,
            document_search_service=document_search_service,
            document_index_service=document_index_service,
        )
    finally:
        session.close()
