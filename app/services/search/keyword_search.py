"""키워드 검색 (Postgres FTS `to_tsquery` OR 매칭 + `ts_rank`)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.repositories.chunk_repository import ChunkRepository

_TERM_RE = re.compile(r"[0-9A-Za-z_]+|[가-힣]+")


def tokenize_terms(query: str) -> list[str]:
    """질의를 영숫자/언더스코어 또는 한글 덩어리 단위 소문자 term 으로 자른다.

    `chunk.text_tsv` 생성 컬럼이 `to_tsvector('simple', text)` 로 한글
    단어 토큰까지 인덱싱하므로, 질의 쪽 term 화도 한글을 포함해 맞춘다.
    """
    return [t.lower() for t in _TERM_RE.findall(query or "")]


@dataclass
class KeywordHit:
    """키워드 검색 결과 한 건(청크 ID + 엔드포인트 ref_id + ts_rank 점수)."""

    chunk_id: str
    ref_id: str
    score: float


class KeywordSearch:
    """`chunk.text_tsv` GIN 인덱스로 endpoint 청크를 키워드 검색한다."""

    def __init__(self, chunk_repo: ChunkRepository, lexical_field: str = "text") -> None:
        """청크 저장소와 lexical 벡터 선택값을 보관한다.

        `lexical_field` 는 `"structured"` 일 때만 가중 `search_tsv` 경로를
        쓰고, 그 외 값은 전부 기존 `text_tsv` 로 degrade 한다
        (`docs/architect-review/78` §6, 롤백 스위치 규약).
        """
        self._chunk_repo = chunk_repo
        self._lexical_field = "structured" if lexical_field == "structured" else "text"

    def search(
        self,
        query: str,
        top_k: int,
        *,
        document_id: str | None = None,
        project: str | None = None,
        query_variants: list[str] | None = None,
    ) -> list[KeywordHit]:
        """질의 term 을 OR 로 결합해 top_k 결과를 `ts_rank` 내림차순으로 반환한다.

        스코프(document_id/project)는 SQL 로 필터링한다 — 후보 청크를
        Python 메모리에 미리 적재하지 않는다.

        `query_variants`(호출자가 넘긴 동의어/유사 표현)는 FTS OR 후보
        필터만 넓히는 데 쓴다 — `ts_rank` 점수는 항상 원본 질의 term 만으로
        계산한다(협업문서 검색과 동일 규약, `docs/search_flow.md` §3.1).
        """
        terms = tokenize_terms(query)
        if not terms:
            return []
        variant_terms = tokenize_terms(" ".join(query_variants)) if query_variants else []
        filter_terms = list(dict.fromkeys([*terms, *variant_terms]))
        hits = self._chunk_repo.search_endpoint_by_text(
            filter_terms,
            top_k=top_k,
            document_id=document_id,
            project=project,
            score_terms=terms,
            lexical_field=self._lexical_field,
        )
        return [KeywordHit(chunk_id=h.chunk_id, ref_id=h.ref_id, score=h.score) for h in hits]
