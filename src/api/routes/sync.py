"""문서 재색인/동기화."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import ServiceBundle
from src.api.dependency_providers import get_services
from src.schemas.documents import SyncRequest, SyncResponse

router = APIRouter()


@router.post("/sync/{document_id}", response_model=SyncResponse)
def resync_document(
    document_id: str,
    body: SyncRequest | None = None,
    services: ServiceBundle = Depends(get_services),
) -> SyncResponse:
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
