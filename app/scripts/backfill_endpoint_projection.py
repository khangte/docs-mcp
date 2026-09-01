"""기존 색인에 canonical endpoint projection 행을 채우는 배치(`docs/architect-review/101` §5.1).

alembic 마이그레이션(`d5f1a3c8b920`)은 `endpoint_search_projection` 테이블만
만든다. 이 스크립트가 문서별로 `Document.raw_text` 를 색인 경로와 **같은**
`parse_document` 로 재파싱해 `build_endpoint_projection` 결과를 upsert 한다.

문서 단위로 커밋한다 — 한 문서가 실패하면 그 문서만 롤백되고, 재실행하면
자연키 `(document_id, method, path)` upsert 라 이미 채워진 문서는 같은 값으로
덮어써 idempotent 하다(§6 결정성).

비의미(hash) 임베딩 프로바이더에서는 dense vector 를 만들지 않고 embedding 을
NULL 로 둔다(§5.2).

audit 모드는 저장된 projection 의 개수·version 분포·집계 해시를 찍는다.
백필 후 다시 audit 해서 해시가 바뀌면 낡은 행이 있었다는 뜻이다.

실행 순서:
    uv run alembic upgrade head
    uv run python -m app.scripts.backfill_endpoint_projection
    uv run python -m app.scripts.backfill_endpoint_projection --audit
"""

from __future__ import annotations

import argparse
import hashlib
import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bootstrap import bootstrap_app_state
from app.models import ApiEndpoint, Document
from app.repositories.endpoint_projection_repository import EndpointProjectionRepository
from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.indexer.endpoint_projection import (
    REPRESENTATION_VERSION,
    build_endpoint_projection,
)
from app.services.indexer.indexer_service import _make_projection_id
from app.services.parser.document_router import parse_document

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionAuditReport:
    """projection 감사 결과."""

    count: int
    digest: str
    versions: dict[str, int]
    current_version: str = REPRESENTATION_VERSION


def backfill_endpoint_projection(
    session_factory: sessionmaker[Session],
    embedding_provider: EmbeddingProvider,
    *,
    document_id: str | None = None,
) -> int:
    """문서별로 canonical projection 을 다시 만들어 upsert 한다.

    Args:
        session_factory: 문서마다 새 세션을 여는 팩토리(문서 단위 트랜잭션).
        embedding_provider: dense projection 벡터용 프로바이더. `is_semantic`
            이 False 면 벡터를 만들지 않는다.
        document_id: 주어지면 그 문서만 백필한다.

    Returns:
        upsert 한 projection 행 수. 엔드포인트가 없는 문서는 건너뛴다.
    """
    with session_factory() as session:
        stmt = select(Document.id).order_by(Document.id)
        if document_id is not None:
            stmt = stmt.where(Document.id == document_id)
        doc_ids = list(session.execute(stmt).scalars())

    total = 0
    for doc_id in doc_ids:
        with session_factory() as session:
            written = _backfill_one_document(session, embedding_provider, doc_id)
            session.commit()
        total += written
        if written:
            logger.info("projection 백필: 문서 %s — %d행", doc_id, written)

    logger.info("projection 백필 완료: 총 %d행 / 문서 %d건", total, len(doc_ids))
    return total


def _backfill_one_document(
    session: Session, embedding_provider: EmbeddingProvider, doc_id: str
) -> int:
    """문서 1건의 projection 을 재생성해 upsert 한다(호출자가 커밋)."""
    document = session.get(Document, doc_id)
    if document is None:
        return 0
    endpoint_ids = {
        (ep.method, ep.path): ep.id
        for ep in session.execute(
            select(ApiEndpoint).where(ApiEndpoint.document_id == doc_id)
        ).scalars()
    }
    if not endpoint_ids:
        return 0

    parsed = parse_document(document.raw_text, document.doc_type)
    targets = [ep for ep in parsed.endpoints if (ep.method, ep.path) in endpoint_ids]
    if not targets:
        logger.warning("문서 %s: 재파싱 결과가 색인된 엔드포인트와 겹치지 않음", doc_id)
        return 0

    projections = [build_endpoint_projection(ep) for ep in targets]
    if embedding_provider.is_semantic:
        labels = [
            f"{doc_id}:projection:{endpoint_ids[(ep.method, ep.path)]}" for ep in targets
        ]
        vectors: list[list[float] | None] = [
            list(v)
            for v in embedding_provider.embed_documents(
                [p.canonical_text for p in projections], labels=labels
            )
        ]
    else:
        vectors = [None] * len(projections)

    repo = EndpointProjectionRepository(session)
    for ep, projection, vector in zip(targets, projections, vectors, strict=True):
        repo.upsert(
            id=_make_projection_id(doc_id, ep.method, ep.path),
            endpoint_id=endpoint_ids[(ep.method, ep.path)],
            document_id=doc_id,
            method=ep.method,
            path=ep.path,
            canonical_text=projection.canonical_text,
            embedding=vector,
            representation_version=projection.representation_version,
            source_hash=projection.source_hash,
        )
    return len(targets)


def audit_endpoint_projection(
    session_factory: sessionmaker[Session],
    *,
    document_id: str | None = None,
    project: str | None = None,
) -> ProjectionAuditReport:
    """저장된 projection 의 개수·version 분포·집계 해시를 낸다(§5.1 감사)."""
    with session_factory() as session:
        repo = EndpointProjectionRepository(session)
        rows = repo.list_audit_rows(document_id=document_id, project=project)

    agg = hashlib.sha256()
    for row in rows:
        agg.update(
            f"{row.document_id}\t{row.method}\t{row.path}\t"
            f"{row.representation_version}\t{row.source_hash}\n".encode()
        )
    return ProjectionAuditReport(
        count=len(rows),
        digest=agg.hexdigest(),
        versions=dict(Counter(row.representation_version for row in rows)),
    )


def _parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", default=None, help="이 문서만 처리한다.")
    parser.add_argument("--project", default=None, help="audit 시 이 프로젝트만 집계한다.")
    parser.add_argument(
        "--audit", action="store_true", help="백필 대신 개수/해시 감사만 수행한다."
    )
    return parser.parse_args()


def main() -> None:
    """설정을 로드해 AppState 를 구성하고 백필 또는 감사를 수행한다."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    state = bootstrap_app_state()
    if args.audit:
        report = audit_endpoint_projection(
            state.session_factory, document_id=args.document_id, project=args.project
        )
        logger.info(
            "projection 감사: %d행, version=%s, digest=%s (현재 format %s)",
            report.count,
            report.versions,
            report.digest,
            report.current_version,
        )
        return
    backfill_endpoint_projection(
        state.session_factory, state.embedding_provider, document_id=args.document_id
    )


if __name__ == "__main__":
    main()
