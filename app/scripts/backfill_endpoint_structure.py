"""기존 색인의 엔드포인트 구조 신호 3필드를 채우는 배치.

alembic 마이그레이션(`c4d9e1f70a2b`)은 컬럼만 만들고 값은 빈 문자열로 둔다.
이 스크립트가 `api_endpoint` 의 method/path/summary/tags/operationId 로부터
`chunk.leaf_text`/`intent_text`/`context_text` 를 채운다.

`chunk.text` 와 `chunk.embedding` 은 건드리지 않는다 — 재임베딩 0 이
`docs/architect-review/78` §3.2 의 전제이고, 전체 재색인 대신 이 스크립트를
쓰는 이유이기도 하다(재색인은 verdict 70 이 기록한 `api_endpoint.id` 재해시
비결정성을 다시 건드린다).

실행 순서:
    uv run alembic upgrade head
    uv run python -m app.scripts.backfill_endpoint_structure
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bootstrap import bootstrap_app_state
from app.models import ApiEndpoint, Chunk
from app.services.indexer.endpoint_structure import derive_endpoint_structure

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500


def backfill_endpoint_structure(
    session_factory: sessionmaker[Session],
    *,
    document_id: str | None = None,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> int:
    """endpoint 청크의 구조 신호 3필드를 다시 계산해 채운다.

    Args:
        session_factory: 배치마다 새 세션을 여는 팩토리.
        document_id: 주어지면 그 문서의 청크만 갱신한다.
        batch_size: 한 트랜잭션에서 갱신할 청크 수.

    Returns:
        갱신한 endpoint 청크 수. 참조하는 엔드포인트 행이 없는 청크는 건너뛴다.
    """
    with session_factory() as session:
        stmt = select(Chunk.id).where(Chunk.chunk_type == "endpoint")
        if document_id is not None:
            stmt = stmt.where(Chunk.document_id == document_id)
        chunk_ids = list(session.execute(stmt.order_by(Chunk.id)).scalars())

    total = 0
    for start in range(0, len(chunk_ids), batch_size):
        batch_ids = chunk_ids[start : start + batch_size]
        with session_factory() as session:
            chunks = list(
                session.execute(
                    select(Chunk).where(Chunk.id.in_(batch_ids)).order_by(Chunk.id)
                ).scalars()
            )
            endpoints = {
                endpoint.id: endpoint
                for endpoint in session.execute(
                    select(ApiEndpoint).where(
                        ApiEndpoint.id.in_({chunk.ref_id for chunk in chunks})
                    )
                ).scalars()
            }
            for chunk in chunks:
                endpoint = endpoints.get(chunk.ref_id)
                if endpoint is None:
                    logger.warning("청크가 참조하는 엔드포인트 없음: %s", chunk.id)
                    continue
                structure = derive_endpoint_structure(
                    method=endpoint.method,
                    path=endpoint.path,
                    summary=endpoint.summary or "",
                    tags=endpoint.tags,
                    operation_id=endpoint.operation_id,
                )
                chunk.leaf_text = structure.leaf_text
                chunk.intent_text = structure.intent_text
                chunk.context_text = structure.context_text
                total += 1
            session.commit()
        logger.info("구조 신호 백필 진행: %d/%d", total, len(chunk_ids))

    logger.info("구조 신호 백필 완료: 총 %d개 청크", total)
    return total


def _parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", default=None, help="이 문서의 청크만 갱신한다.")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH_SIZE)
    return parser.parse_args()


def main() -> None:
    """설정을 로드해 AppState 를 구성하고 구조 신호를 백필한다."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    state = bootstrap_app_state()
    backfill_endpoint_structure(
        state.session_factory, document_id=args.document_id, batch_size=args.batch_size
    )


if __name__ == "__main__":
    main()
