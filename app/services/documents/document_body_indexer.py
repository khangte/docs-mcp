"""Drive/Notion 문서 1건 본문 색인 (설계 §1 Phase2, 문서 7·8번).

`document_meta` 는 메타데이터 캐시, `document`/`chunk` 는 등록형 파이프라인이
이미 쓰는 본문·청크 저장소다. Drive/Notion 문서를 등록형과 같은 저장소에
태우되, `Document.id` 를 project/source/external_id 로부터 결정적으로
고정해 매번 같은 행을 찾는다(생성식은 `deterministic_document_id` 참조,
`docs/architect-review/38_drive_notion_document_id_length_verdict.md`).

문서 집합(`document_meta`)은 diff upsert 를 유지하고(`document_index_service`),
문서 1건의 파생물(청크)만 `content_hash` 게이트를 통과했을 때
delete-and-insert 한다 — `sync_service.resync` 와 동일한 패턴이다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from app.models import ApiSchema, Document, DocumentSection
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository
from app.services.indexer.indexer_service import IndexerService
from app.services.parser.markdown_parser import parse_document


def deterministic_document_id(project: str, source: str, external_id: str) -> str:
    """project/source/external_id 로 Drive/Notion 문서의 결정적 `Document.id` 를 만든다.

    `Document.id` 는 `String(64)` 이고 하위 엔티티 ID가 여기서 파생되므로
    (`{doc_id}:ep:{16-hex}` 가 20자를 더 쓴다), 입력 길이와 무관하게 고정
    길이를 낸다. 등록형 `_new_id()`(uuid4 16-hex)와 같은 폭이다
    (`docs/architect-review/38_drive_notion_document_id_length_verdict.md`).
    """
    key = f"{project}\x00{source}\x00{external_id}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


@dataclass(frozen=True)
class BodyIndexResult:
    """문서 1건 본문 색인 결과.

    Attributes:
        reindexed: True 면 청크를 새로 만들었다. False 면 content_hash 가
            같아 건드리지 않았다(skip).
    """

    reindexed: bool


def index_document_body(
    session: Session,
    document_repo: DocumentRepository,
    endpoint_repo: EndpointRepository,
    chunk_repo: ChunkRepository,
    indexer: IndexerService,
    *,
    project: str,
    source_name: str,
    external_id: str,
    title: str,
    raw: str,
) -> BodyIndexResult:
    """문서 1건의 본문을 색인한다.

    `parse_document`(markdown 고정, 어댑터가 이미 평문을 준다)로 파싱한 뒤
    `content_hash` 가 이전과 같으면 아무 것도 하지 않는다. 다르면 기존
    청크/엔드포인트/스키마/섹션을 지우고 재색인한다
    (`sync_service.resync` 패턴 복제).
    """
    document_id = deterministic_document_id(project, source_name, external_id)
    new_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    document = document_repo.get(document_id)
    if document is not None and document.content_hash == new_hash:
        return BodyIndexResult(reindexed=False)

    parsed = parse_document(raw, title_hint=title)
    now = datetime.now(timezone.utc)

    if document is None:
        document = Document(
            id=document_id,
            project=project,
            source_url=None,
            title=parsed.title,
            version="unknown",
            doc_type=source_name,
            content_hash=new_hash,
            raw_text=raw,
            indexed_at=now,
        )
        document_repo.add(document)
        session.flush()
    else:
        chunk_repo.delete_by_document(document_id)
        for endpoint in list(endpoint_repo.list_by_document(document_id)):
            session.delete(endpoint)
        session.execute(sa_delete(ApiSchema).where(ApiSchema.document_id == document_id))
        session.execute(sa_delete(DocumentSection).where(DocumentSection.document_id == document_id))
        session.flush()
        document.content_hash = new_hash
        document.raw_text = raw
        document.title = parsed.title
        document.indexed_at = now

    indexer.index_document(document=document, parsed=parsed)
    return BodyIndexResult(reindexed=True)
