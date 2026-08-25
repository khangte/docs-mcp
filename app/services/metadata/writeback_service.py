"""호출 LLM write-back: 검증 → 중복판정 → upsert → 청크 갱신.

docs/architect-review/56: 서버는 판단하지 않는다(요약·키워드·표현 생성은
호출 LLM 몫). 여기서 하는 일은 정규화·중복판정·저장·색인 반영뿐이며 외부 LLM
API 를 호출하는 경로는 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import (
    DomainError,
    EndpointNotFoundError,
    IntegrationError,
    WritebackDisabledError,
)
from app.core.logging import get_logger
from app.models import ApiEndpoint, EndpointBusinessMetadata
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.indexer.endpoint_chunk_refresher import refresh_endpoint_chunk
from app.services.metadata.spec_payload import (
    build_endpoint_input,
    build_payload_json,
    compute_source_hash,
)
from app.services.metadata.validation import is_empty, sanitize_and_clip

_LOG = get_logger("docs_mcp.metadata.writeback")

#: write-back 으로 저장된 행임을 표시하는 `model` 컬럼 상수(56 §4.2).
#: 호출 LLM 의 자기 신고 모델명을 받지 않는다 — 검증 불가능하고, 55 §3 의
#: "model 불일치 시 재생성" 규칙과 물리면 클라 모델 교체마다 전량 재생성이 돈다.
CLIENT_WRITEBACK_MODEL = "client-writeback"

#: `get_endpoint_details` 힌트에 실리는 지시문(56 §3.2). 55 SYSTEM_PROMPT 규칙의
#: 축약본이다 — 두 생산자가 같은 규칙을 따라야 청크 포맷이 일관된다. 문안을
#: 바꾸면 `spec_payload.METADATA_INSTRUCTION_VERSION` 을 올린다.
WRITEBACK_INSTRUCTION = (
    "이 엔드포인트에는 검색용 비즈니스 메타데이터가 없거나 스펙 변경으로 낡았다. "
    "위 상세 정보를 근거로 다음을 만들어 submit_endpoint_metadata 로 보내면 이후 검색 품질이 "
    "개선된다. business_description: 한국어 1문장 최대 120자. "
    "user_phrases: 한국어 2개 + 영어 2개, 각 최대 40자, summary 의 동사와 다른 표현을 최소 "
    "1개 포함(cancel<->delete/remove, create<->add/register, list<->get all/fetch). "
    "keywords: 영어와 한국어를 섞어 최대 5개, 각 최대 30자. "
    "상세에 없는 사실은 만들지 않는다."
)


@dataclass(frozen=True)
class MetadataRequestHint:
    """`get_endpoint_details` 응답에 실리는 기여 요청 힌트."""

    reason: str
    instruction: str


@dataclass(frozen=True)
class WritebackResult:
    """`submit_endpoint_metadata` 처리 결과."""

    status: str
    endpoint_id: str
    reindexed: bool
    truncated: bool
    reason: str | None = None


class MetadataWritebackService:
    """호출 LLM 이 되돌려준 메타데이터를 검증·저장하고 청크에 반영한다."""

    def __init__(
        self,
        session: Session,
        endpoint_repo: EndpointRepository,
        document_repo: DocumentRepository,
        chunk_repo: ChunkRepository,
        embedding_provider: EmbeddingProvider,
        enabled: bool,
    ) -> None:
        """세션·저장소·임베딩 의존성과 활성화 여부를 보관한다."""
        self._session = session
        self._endpoint_repo = endpoint_repo
        self._document_repo = document_repo
        self._chunk_repo = chunk_repo
        self._embedding_provider = embedding_provider
        self._enabled = enabled

    def build_request_hint(self, endpoint_id: str) -> MetadataRequestHint | None:
        """메타데이터가 없거나 낡았으면 기여 요청 힌트를 만든다(아니면 None).

        write-back 이 꺼져 있으면 호출할 수 없는 도구를 광고하지 않도록
        항상 None 을 돌려준다.
        """
        if not self._enabled:
            return None
        endpoint = self._endpoint_repo.get(endpoint_id)
        if endpoint is None:
            return None
        row = self._endpoint_repo.get_business_metadata(
            endpoint.document_id, endpoint.method, endpoint.path
        )
        if row is None:
            return MetadataRequestHint(reason="missing", instruction=WRITEBACK_INSTRUCTION)
        if row.source_hash != self._source_hash(endpoint):
            return MetadataRequestHint(reason="stale", instruction=WRITEBACK_INSTRUCTION)
        return None

    def submit(
        self,
        endpoint_id: str,
        business_description: str,
        keywords: list[str],
        user_phrases: list[str],
    ) -> WritebackResult:
        """정규화·중복판정 후 저장하고, 해당 엔드포인트 청크를 갱신한다.

        Raises:
            WritebackDisabledError: 설정으로 write-back 이 꺼져 있는 경우.
            EndpointNotFoundError: `endpoint_id` 가 없는 경우.
        """
        if not self._enabled:
            raise WritebackDisabledError()
        endpoint = self._endpoint_repo.get(endpoint_id)
        if endpoint is None:
            raise EndpointNotFoundError(endpoint_id)

        sanitized = sanitize_and_clip(business_description, keywords, user_phrases)
        if is_empty(sanitized):
            return WritebackResult(
                status="rejected",
                endpoint_id=endpoint_id,
                reindexed=False,
                truncated=sanitized.truncated,
                reason="empty_after_sanitize",
            )

        source_hash = self._source_hash(endpoint)
        row = self._endpoint_repo.get_business_metadata(
            endpoint.document_id, endpoint.method, endpoint.path
        )
        if row is not None and row.source_hash == source_hash:
            # 56 §4.3: 해시가 같은데 덮어쓰면 세션마다 문구가 흔들리고 그때마다
            # 재임베딩이 돌아 검색 결과가 비결정적으로 움직인다. 잘못 들어간 값의
            # 수정은 운영자 경로(CLI --force / 행 삭제)로만 연다.
            return WritebackResult(
                status="already_current",
                endpoint_id=endpoint_id,
                reindexed=False,
                truncated=sanitized.truncated,
                reason="hash_unchanged",
            )

        if row is None:
            row = EndpointBusinessMetadata(
                document_id=endpoint.document_id,
                method=endpoint.method,
                path=endpoint.path,
            )
            self._session.add(row)
        row.business_description = sanitized.business_description
        row.keywords = sanitized.keywords
        row.user_phrases = sanitized.user_phrases
        row.source_hash = source_hash
        row.model = CLIENT_WRITEBACK_MODEL
        row.generated_at = datetime.now(UTC)
        # 56 §4.4: 메타데이터를 먼저 확정한다 — 청크 갱신이 실패해도 저장은
        # 살아남아야 다음 전체 재색인에서 반영된다.
        self._session.commit()

        reindexed = self._refresh_chunk(endpoint, row)
        return WritebackResult(
            status="stored",
            endpoint_id=endpoint_id,
            reindexed=reindexed,
            truncated=sanitized.truncated,
        )

    def _refresh_chunk(self, endpoint: ApiEndpoint, row: EndpointBusinessMetadata) -> bool:
        """해당 엔드포인트 청크만 재조립한다. 실패해도 메타데이터는 롤백하지 않는다."""
        document = self._document_repo.get(endpoint.document_id)
        if document is None:
            return False
        try:
            updated = refresh_endpoint_chunk(
                document=document,
                endpoint=endpoint,
                metadata=row,
                chunk_repo=self._chunk_repo,
                embedding_provider=self._embedding_provider,
            )
            self._session.commit()
            return updated
        except (DomainError, IntegrationError, SQLAlchemyError) as exc:
            self._session.rollback()
            _LOG.warning(
                "청크 갱신 실패(메타데이터는 저장됨): %s %s: %s",
                endpoint.method,
                endpoint.path,
                exc,
            )
            return False

    def _source_hash(self, endpoint: ApiEndpoint) -> str:
        """현재 스펙 기준 `source_hash` 를 계산한다(56 §4.2 — 서버가 계산한다)."""
        return compute_source_hash(build_payload_json(build_endpoint_input(endpoint)))
