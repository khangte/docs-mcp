"""문서 등록/목록/상세/삭제."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import ServiceBundle
from app.api.dependency_providers import get_services
from app.schemas.documents import (
    DocumentDeleteResponse,
    DocumentDetail,
    DocumentSummary,
    RegisterDocumentRequest,
    RegisterDocumentResponse,
    SyncHistoryItem,
)

router = APIRouter()


@router.post("/documents", response_model=RegisterDocumentResponse)
def register_document(
    body: RegisterDocumentRequest,
    services: ServiceBundle = Depends(get_services),
) -> RegisterDocumentResponse:
    """OpenAPI 문서를 신규 등록하고 등록 결과 메타를 반환한다."""
    result = services.sync_service.register(
        source_url=body.source_url,
        raw_document=body.raw_document,
        title_override=body.title_override,
    )
    doc = result.document
    return RegisterDocumentResponse(
        document_id=doc.id,
        title=doc.title,
        version=doc.version,
        source_url=doc.source_url,
        endpoints_count=result.endpoints_count,
        schemas_count=result.schemas_count,
        chunks_count=result.chunks_count,
        content_hash=result.content_hash,
        indexed_at=doc.indexed_at,
    )


@router.get("/documents", response_model=list[DocumentSummary])
def list_documents(
    services: ServiceBundle = Depends(get_services),
) -> list[DocumentSummary]:
    """등록된 모든 문서의 요약 목록을 반환한다."""
    docs = services.document_repo.list_all()
    return [
        DocumentSummary(
            document_id=d.id,
            title=d.title,
            version=d.version,
            source_url=d.source_url,
            endpoints_count=len(d.endpoints),
            indexed_at=d.indexed_at,
        )
        for d in docs
    ]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    services: ServiceBundle = Depends(get_services),
) -> DocumentDetail:
    """문서 상세 정보와 최근 동기화 이력을 반환한다."""
    from app.core.errors import DocumentNotFoundError

    doc = services.document_repo.get(document_id)
    if doc is None:
        raise DocumentNotFoundError(document_id)
    history = services.sync_history_repo.list_by_document(document_id, limit=10)
    return DocumentDetail(
        document_id=doc.id,
        title=doc.title,
        version=doc.version,
        source_url=doc.source_url,
        endpoints_count=len(doc.endpoints),
        indexed_at=doc.indexed_at,
        sync_history=[
            SyncHistoryItem(
                status=h.status,
                content_hash=h.content_hash,
                error=h.error,
                created_at=h.created_at,
            )
            for h in history
        ],
    )


@router.delete("/documents/{document_id}", response_model=DocumentDeleteResponse)
def delete_document(
    document_id: str,
    services: ServiceBundle = Depends(get_services),
) -> DocumentDeleteResponse:
    """문서 및 관련 인덱스를 삭제한다."""
    services.sync_service.delete(document_id)
    return DocumentDeleteResponse(deleted=True, document_id=document_id)
