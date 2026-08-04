"""문서 재색인/동기화."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.composition import ServiceBundle
from app.web.dependency_providers import get_services
from app.schemas.documents import SyncRequest, SyncResponse

router = APIRouter()


@router.post("/sync/{document_id}", response_model=SyncResponse)
def resync_document(
    document_id: str,
    body: SyncRequest | None = None,
    services: ServiceBundle = Depends(get_services),
) -> SyncResponse:
    """기존 문서를 재수집·재색인하고 변경 결과를 반환한다."""
    payload = body or SyncRequest()
    result = services.sync_service.resync(
        document_id,
        force=payload.force,
        raw_override=payload.raw_override,
    )
    return SyncResponse(
        document_id=document_id,
        status=result.status,
        previous_hash=result.previous_hash,
        new_hash=result.content_hash,
        endpoints_count=result.endpoints_count,
        chunks_count=result.chunks_count,
    )
