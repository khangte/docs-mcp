"""문서 등록/목록/상세/삭제."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import ServiceBundle
from src.api.dependency_providers import get_services
from src.schemas.documents import (
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
    from src.core.errors import DocumentNotFoundError

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
    services.sync_service.delete(document_id)
    return DocumentDeleteResponse(deleted=True, document_id=document_id)
