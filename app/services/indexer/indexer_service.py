"""문서 색인/재색인 오케스트레이션.

입력: ParsedDocument + Document (DB 엔티티)
출력: (endpoints_count, chunks_count)

임베딩은 Chunk.embedding(pgvector 컬럼)에 직접 저장되며, 호출자의
session.commit() 과 함께 영속화된다.
"""

from __future__ import annotations

import hashlib
import json


from app.models import (
    ApiEndpoint,
    ApiParameter,
    ApiRequestBody,
    ApiResponse,
    ApiSchema,
    Chunk,
    Document,
    DocumentSection,
)
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.indexer.chunk_builder import build_chunks
from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.parser.openapi_parser import (
    ParsedDocument,
    ParsedEndpoint,
    ParsedParameter,
    ParsedRequestBody,
    ParsedResponse,
)


class IndexerService:
    """ParsedDocument → DB 엔티티 + 청크 적재."""

    def __init__(
        self,
        endpoint_repo: EndpointRepository,
        chunk_repo: ChunkRepository,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        """저장소·임베딩 의존성을 보관한다."""
        self._endpoint_repo = endpoint_repo
        self._chunk_repo = chunk_repo
        self._embedding_provider = embedding_provider

    def index_document(
        self, document: Document, parsed: ParsedDocument
    ) -> tuple[int, int]:
        """엔드포인트/스키마/청크를 DB 에 저장한다.

        반환: (endpoints_count, chunks_count)
        재색인일 경우 호출자가 이전 청크/엔드포인트/스키마를 먼저 지워야 한다.
        """
        endpoint_ids: dict[tuple[str, str], str] = {}
        for idx, parsed_ep in enumerate(parsed.endpoints):
            endpoint_id = _make_endpoint_id(document.id, parsed_ep, idx)
            endpoint_ids[(parsed_ep.method, parsed_ep.path)] = endpoint_id
            endpoint = _to_endpoint_entity(document.id, endpoint_id, parsed_ep)
            self._endpoint_repo.add(endpoint)
            for param in parsed_ep.parameters:
                self._endpoint_repo.add(
                    _to_parameter_entity(endpoint_id, param)
                )
            if parsed_ep.request_body is not None:
                self._endpoint_repo.add(
                    _to_request_body_entity(endpoint_id, parsed_ep.request_body)
                )
            for response in parsed_ep.responses:
                self._endpoint_repo.add(
                    _to_response_entity(endpoint_id, response)
                )
        for idx, parsed_schema in enumerate(parsed.schemas):
            schema_entity = ApiSchema(
                id=f"{document.id}:schema:{idx}",
                document_id=document.id,
                name=parsed_schema.name,
                json_schema=_json_dumps_safe(parsed_schema.json_schema),
                description=parsed_schema.description,
            )
            self._endpoint_repo.add_schema(schema_entity)

        section_ids: dict[int, str] = {}
        for idx, parsed_section in enumerate(parsed.sections):
            section_id = f"{document.id}:section:{idx}"
            section_ids[idx] = section_id
            section_entity = DocumentSection(
                id=section_id,
                document_id=document.id,
                title=parsed_section.title,
                content=parsed_section.content,
                order_index=idx,
            )
            self._endpoint_repo.add_section(section_entity)

        built_chunks = build_chunks(parsed, endpoint_ids, section_ids)
        texts = [c.text for c in built_chunks]
        labels = [f"{document.id}:{c.chunk_type}:{c.ref_id}" for c in built_chunks]
        embeddings = (
            self._embedding_provider.embed_documents(texts, labels=labels) if texts else []
        )
        for idx, (built, vector) in enumerate(zip(built_chunks, embeddings, strict=True)):
            chunk = Chunk(
                id=f"{document.id}:chunk:{idx}",
                document_id=document.id,
                chunk_type=built.chunk_type,
                ref_id=built.ref_id,
                text=built.text,
                embedding=vector,
            )
            self._chunk_repo.add(chunk)

        return len(parsed.endpoints), len(built_chunks)


def _to_endpoint_entity(
    document_id: str, endpoint_id: str, parsed: ParsedEndpoint
) -> ApiEndpoint:
    """ParsedEndpoint 를 ApiEndpoint ORM 엔티티로 변환한다."""
    entity = ApiEndpoint(
        id=endpoint_id,
        document_id=document_id,
        method=parsed.method,
        path=parsed.path,
        summary=parsed.summary,
        description=parsed.description,
    )
    entity.tags = list(parsed.tags)
    return entity


def _to_parameter_entity(endpoint_id: str, parsed: object) -> ApiParameter:
    """ParsedParameter 를 ApiParameter ORM 엔티티로 변환한다."""
    assert isinstance(parsed, ParsedParameter)
    entity = ApiParameter(
        endpoint_id=endpoint_id,
        name=parsed.name,
        location=parsed.location,
        required=1 if parsed.required else 0,
        description=parsed.description,
        schema_ref=parsed.schema_ref,
    )
    entity.schema = parsed.schema
    return entity


def _to_request_body_entity(endpoint_id: str, parsed: object) -> ApiRequestBody:
    """ParsedRequestBody 를 ApiRequestBody ORM 엔티티로 변환한다."""
    assert isinstance(parsed, ParsedRequestBody)
    entity = ApiRequestBody(
        endpoint_id=endpoint_id,
        content_type=parsed.content_type,
        required=1 if parsed.required else 0,
        schema_ref=parsed.schema_ref,
    )
    entity.schema = parsed.schema
    entity.example = parsed.example
    return entity


def _to_response_entity(endpoint_id: str, parsed: object) -> ApiResponse:
    """ParsedResponse 를 ApiResponse ORM 엔티티로 변환한다."""
    assert isinstance(parsed, ParsedResponse)
    entity = ApiResponse(
        endpoint_id=endpoint_id,
        status_code=parsed.status_code,
        content_type=parsed.content_type,
        description=parsed.description,
        schema_ref=parsed.schema_ref,
    )
    entity.schema = parsed.schema
    return entity


def _make_endpoint_id(document_id: str, parsed: ParsedEndpoint, idx: int) -> str:
    """문서ID·메서드·경로·인덱스를 해시해 결정적인 endpoint_id 를 만든다."""
    key = f"{document_id}:{parsed.method}:{parsed.path}:{idx}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{document_id}:ep:{digest}"


def _json_dumps_safe(obj: dict[str, object]) -> str:
    """객체를 JSON 으로 직렬화하되 실패 시 빈 객체 문자열을 반환한다."""
    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        return "{}"
