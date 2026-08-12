"""임베딩 모델/차원 교체 후 저장된 청크 텍스트를 재임베딩하는 배치.

alembic 마이그레이션(`ff8aa8f36266`)은 embedding 컬럼 차원만 바꾸고 값은
전부 NULL 로 만든다 — ML 모델 로드/추론은 마이그레이션이 아니라 여기서
전담한다. 이 스크립트는 이미 저장된 `Chunk.text` 만 순회해 임베딩을 다시
채우며, 문서 재파싱이나 네트워크 호출은 하지 않는다.

실행 순서:
    uv run alembic upgrade head
    uv run python -m app.scripts.reembed
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.bootstrap import bootstrap_app_state
from app.composition import AppState
from app.models import Chunk

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 200


def reembed_all(state: AppState, batch_size: int = _DEFAULT_BATCH_SIZE) -> int:
    """전체 `Chunk` 를 배치 단위로 순회하며 embedding 을 다시 채운다.

    문서/엔드포인트는 건드리지 않고 `chunk.text` → 새 임베딩 벡터 갱신만
    수행한다. 배치마다 커밋해 대량 데이터에서도 트랜잭션이 과도하게
    커지지 않게 한다. 반환: 갱신한 청크 수.
    """
    provider = state.embedding_provider
    with state.session_factory() as session:
        chunk_ids = list(session.execute(select(Chunk.id).order_by(Chunk.id)).scalars())

    total = 0
    for start in range(0, len(chunk_ids), batch_size):
        batch_ids = chunk_ids[start : start + batch_size]
        with state.session_factory() as session:
            chunks = list(
                session.execute(
                    select(Chunk).where(Chunk.id.in_(batch_ids)).order_by(Chunk.id)
                ).scalars()
            )
            vectors = provider.embed_documents(
                [chunk.text for chunk in chunks],
                labels=[chunk.id for chunk in chunks],
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk.embedding = vector
            session.commit()
        total += len(chunks)
        logger.info("재임베딩 진행: %d/%d", total, len(chunk_ids))

    logger.info("재임베딩 완료: 총 %d개 청크", total)
    return total


def main() -> None:
    """설정을 로드해 AppState 를 구성하고 전체 청크를 재임베딩한다."""
    logging.basicConfig(level=logging.INFO)
    state = bootstrap_app_state()
    reembed_all(state)


if __name__ == "__main__":
    main()
