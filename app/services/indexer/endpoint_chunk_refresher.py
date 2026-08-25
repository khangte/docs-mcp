"""엔드포인트 청크 1건만 재조립·재임베딩하는 증분 갱신기.

docs/architect-review/56 §4.4: write-back 으로 저장한 메타데이터가 다음 전체
재색인까지 검색에 반영되지 않으면 "힌트는 사라졌는데 품질은 그대로"인 조용한
구멍이 생긴다. 청크 텍스트 생성은 색인 경로와 같은
`build_endpoint_chunk_text` 를 쓰고, `ParsedEndpoint` 는 ORM 역조립이 아니라
`Document.raw_text` 재파싱으로 얻어 포맷 드리프트를 원천 차단한다.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.models import ApiEndpoint, Document, EndpointBusinessMetadata
from app.repositories.chunk_repository import ChunkRepository
from app.services.indexer.chunk_builder import build_endpoint_chunk_text
from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.parser.document_router import parse_document

_LOG = get_logger("docs_mcp.indexer.chunk_refresh")


def refresh_endpoint_chunk(
    *,
    document: Document,
    endpoint: ApiEndpoint,
    metadata: EndpointBusinessMetadata | None,
    chunk_repo: ChunkRepository,
    embedding_provider: EmbeddingProvider,
) -> bool:
    """엔드포인트 1건의 청크 텍스트/임베딩을 다시 만들어 갱신한다.

    Args:
        document: 엔드포인트가 속한 문서(원문 `raw_text` 를 재파싱한다).
        endpoint: 갱신 대상 엔드포인트 ORM 행.
        metadata: 청크에 주입할 비즈니스 메타데이터(없으면 주입 없이 재조립).
        chunk_repo: 청크 갱신용 저장소.
        embedding_provider: 재임베딩에 쓸 프로바이더(로컬 모델이라 과금 없음).

    Returns:
        갱신에 성공하면 True. 원문에서 해당 `(method, path)` 를 못 찾거나
        청크 행이 없으면 False(호출자가 응답의 `reindexed` 로 알린다).
    """
    parsed = parse_document(document.raw_text, document.doc_type)
    target = next(
        (
            candidate
            for candidate in parsed.endpoints
            if candidate.method == endpoint.method and candidate.path == endpoint.path
        ),
        None,
    )
    if target is None:
        _LOG.warning(
            "재조립 대상 엔드포인트를 원문에서 찾지 못함: %s %s %s",
            document.id,
            endpoint.method,
            endpoint.path,
        )
        return False

    text = build_endpoint_chunk_text(target, metadata=metadata)
    label = f"{document.id}:endpoint:{endpoint.id}"
    vectors = embedding_provider.embed_documents([text], labels=[label])
    return chunk_repo.update_endpoint_chunk(
        document_id=document.id,
        ref_id=endpoint.id,
        text=text,
        embedding=list(vectors[0]),
    )
