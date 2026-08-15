"""URL 기반 Document(등록 문서) 재동기화 서비스.

`refresh_index` MCP 도구의 `include_registered=True` 경로와, 서버 밖에서
도는 배치 진입점(`app/scripts/refresh_documents.py`)이 공유하는 로직이다.
원래 MCP 계층(`app/mcp/tools/sources.py`)에 있었으나, 배치 스크립트가 MCP
도구 모듈을 import하면 스크립트 → MCP 도구 → 서비스로 계층이 역전되므로
서비스 계층으로 내렸다(`docs/architect-review/32-refresh-index-batch-automation.md` §4).

`app.mcp.types.RegisteredResyncResult` 임포트는 응답 스키마(`RefreshIndexResult.registered`)를
공유하기 위한 타입 전용 결합이며, 의도적으로 유지하는 계층 경계 예외다
(`docs/architect-review/47_layer_boundary_exceptions_verdict.md` §2-b).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import DomainError, IntegrationError
from app.core.logging import get_logger
from app.mcp.types import RegisteredResyncResult
from app.repositories.document_repository import DocumentRepository
from app.services.ingestor.sync_service import SyncService

_LOG = get_logger("docs_mcp.services", level=get_settings().log_level)


def resync_registered_documents(
    session: Session,
    document_repo: DocumentRepository,
    sync_service: SyncService,
    *,
    project: str | None,
    force: bool,
) -> RegisteredResyncResult:
    """URL 기반 Document 를 순회하며 개별 resync 하고 결과를 집계한다.

    문서 하나가 실패해도 나머지는 계속 진행한다(부분 실패 허용). resync 는
    문서마다 자체 커밋하므로 한 문서의 실패가 다른 문서 결과를 롤백하지 않는다.
    """
    documents = document_repo.list_resyncable(project)
    reindexed = 0
    skipped = 0
    failed: list[str] = []
    for document in documents:
        try:
            result = sync_service.resync(document.id, force=force)
        except (DomainError, IntegrationError) as e:
            _LOG.error("registered resync failed: document_id=%s", document.id, exc_info=e)
            session.rollback()
            failed.append(document.id)
            continue
        if result.status == "reindexed":
            reindexed += 1
        else:
            skipped += 1
    return {
        "total": len(documents),
        "reindexed": reindexed,
        "skipped": skipped,
        "failed": failed,
    }
