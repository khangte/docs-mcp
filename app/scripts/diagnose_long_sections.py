"""긴 섹션(512토큰 초과) 진단 — 읽기 전용, 프로덕션 무변경.

`docs/architect-review/16-long-section-chunking-blindspot.md` Phase 0. 현재 DB의 `api_chunk`
중 `chunk_type='section'` 행을 훑어, 임베딩 토크나이저 기준 최대
시퀀스 길이(실측 512, `docs/16` §1-2) 초과 건수·최대 토큰·초과 문서를
doc_type 별로 집계한다.

DB 스키마를 만들지 않는다(`create_all` 호출 없음) — `api_chunk` 테이블이
아직 없으면 진단할 데이터가 없다는 뜻이므로 빈 결과를 그대로 반환한다.

실행:
    uv run python -m app.scripts.diagnose_long_sections
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import inspect, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.db import create_db_engine
from app.models import SCHEMA, ApiChunk, ApiDocument

logger = logging.getLogger(__name__)

#: `intfloat/multilingual-e5-small` 실측 max_seq_length(docs/16 §1-2).
DEFAULT_THRESHOLD = 512


@dataclass
class SectionOverflow:
    """512토큰 초과 섹션 청크 한 건."""

    document_id: str
    doc_type: str
    ref_id: str
    token_count: int


def _make_token_counter(model_name: str) -> Callable[[str], int]:
    """실제 임베딩 토크나이저로 텍스트 하나의 토큰수를 세는 함수를 만든다."""
    from sentence_transformers import SentenceTransformer

    tokenizer = SentenceTransformer(model_name, device="cpu").tokenizer

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=True))

    return count


def diagnose(
    database_url: str,
    count_tokens: Callable[[str], int],
    threshold: int = DEFAULT_THRESHOLD,
) -> list[SectionOverflow]:
    """`chunk_type='section'` 텍스트를 토큰화해 threshold 초과 건을 찾는다.

    `api_chunk` 테이블이 없으면(아직 문서가 색인된 적 없으면) 스키마를
    만들지 않고 빈 리스트를 반환한다 — 읽기 전용 보장.
    """
    engine = create_db_engine(database_url)
    if not inspect(engine).has_table(ApiChunk.__tablename__, schema=SCHEMA):
        return []

    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        rows = session.execute(
            select(ApiChunk.document_id, ApiChunk.ref_id, ApiChunk.text, ApiDocument.doc_type)
            .join(ApiDocument, ApiDocument.id == ApiChunk.document_id)
            .where(ApiChunk.chunk_type == "section")
        ).all()

    overflows: list[SectionOverflow] = []
    for document_id, ref_id, text, doc_type in rows:
        token_count = count_tokens(text)
        if token_count > threshold:
            overflows.append(SectionOverflow(document_id, doc_type, ref_id, token_count))
    return overflows


def main() -> None:
    """설정을 읽어 실제 DB를 진단하고 결과를 doc_type별로 출력한다."""
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    count_tokens = _make_token_counter(settings.embedding_model)
    overflows = diagnose(settings.database_url, count_tokens)

    if not overflows:
        print(
            f"512토큰 초과 섹션: 0건 (측정 데이터 없음 또는 전부 {DEFAULT_THRESHOLD}토큰 이내) "
            f"— api_chunk 테이블이 없으면 아직 색인된 문서가 없다는 뜻이다."
        )
        return

    by_doc_type: dict[str, list[SectionOverflow]] = defaultdict(list)
    for overflow in overflows:
        by_doc_type[overflow.doc_type].append(overflow)

    print(f"512토큰 초과 섹션: {len(overflows)}건")
    for doc_type, items in sorted(by_doc_type.items()):
        max_tokens = max(item.token_count for item in items)
        print(f"- {doc_type}: {len(items)}건, 최대 {max_tokens}토큰")
        for item in items:
            print(f"    {item.document_id} / {item.ref_id}: {item.token_count}토큰")


if __name__ == "__main__":
    main()
