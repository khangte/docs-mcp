"""FastAPI 의존성 컨테이너.

앱 시작 시 만들어진 엔진/프로바이더를 요청 범위 객체와 합쳐 서비스 인스턴스를 만든다.
테스트에서는 `app.dependency_overrides` 로 교체할 수 있게 함수 인터페이스로 제공.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import create_session_factory
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.repositories.sync_history_repository import SyncHistoryRepository
from app.services.examples.request_example_service import RequestExampleService
from app.services.indexer.embedding_provider import EmbeddingProvider, HashEmbeddingProvider
from app.services.indexer.indexer_service import IndexerService
from app.services.ingestor.openapi_fetcher import OpenAPIFetcher
from app.services.ingestor.sync_service import SyncService
from app.services.rag.llm_provider import LLMProvider, TemplateLLMProvider
from app.services.rag.rag_service import RAGService
from app.services.search.keyword_search import KeywordSearch
from app.services.search.search_service import SearchService
from app.services.search.vector_search import VectorSearch


@dataclass
class AppState:
    """앱 전역 상태(엔진/프로바이더 등)를 보관하는 컨테이너."""

    engine: Engine
    session_factory: sessionmaker[Session]
    embedding_provider: EmbeddingProvider
    llm_provider: LLMProvider
    fetcher: OpenAPIFetcher
    hybrid_alpha: float = 0.4

    @classmethod
    def from_engine(
        cls,
        engine: Engine,
        fetcher: OpenAPIFetcher,
        embedding_dim: int = 256,
        hybrid_alpha: float = 0.4,
    ) -> "AppState":
        """엔진과 fetcher 를 받아 기본 의존성(세션 팩토리·프로바이더)을 채운 AppState 를 만든다."""
        return cls(
            engine=engine,
            session_factory=create_session_factory(engine),
            embedding_provider=HashEmbeddingProvider(dim=embedding_dim),
            llm_provider=TemplateLLMProvider(),
            fetcher=fetcher,
            hybrid_alpha=hybrid_alpha,
        )


@dataclass
class ServiceBundle:
    """요청 스코프 서비스 컨테이너."""

    session: Session
    document_repo: DocumentRepository
    endpoint_repo: EndpointRepository
    chunk_repo: ChunkRepository
    sync_history_repo: SyncHistoryRepository
    sync_service: SyncService
    search_service: SearchService
    rag_service: RAGService
    example_service: RequestExampleService


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
        yield ServiceBundle(
            session=session,
            document_repo=document_repo,
            endpoint_repo=endpoint_repo,
            chunk_repo=chunk_repo,
            sync_history_repo=sync_history_repo,
            sync_service=sync_service,
            search_service=search_service,
            rag_service=rag_service,
            example_service=example_service,
        )
    finally:
        session.close()
