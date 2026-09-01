"""`endpoint_repr` arm — canonical projection 을 검색하는 독립 세 번째 RRF arm.

`docs/architect-review/101` 설계.

내부적으로 두 lookup(canonical FTS, canonical dense vector)을 각각 width 50 으로
돌린 뒤 endpoint id 별 최소 rank 로 접어 **하나의** rank list 를 만든다. 이
merged list 만 바깥 RRF 에 세 번째 arm 으로 들어간다(두 내부 lookup 을 두 arm 으로
세지 않는다).

이 arm 은 keyword/vector arm 의 입력·순위·`match_type` 을 건드리지 않고,
`query_variants` 를 받지 않는다 — 검색 시 사용자가 준 원 query 한 개만 FTS 와
local embedding 에 넣는다(§2.2, client-LLM 위임 원칙).

semantic embedding 이 비가용(`is_semantic == false`)이면 이 arm 은 두 lookup 을
모두 건너뛰고 strict empty 를 반환한다(`docs/architect-review/102`). FTS-only
merged arm 은 허용하지 않는다 — 그러면 바깥 RRF 는 keyword/vector-only 로 돌아가
base-wide·final 이 flag OFF 와 byte-identical 하다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.repositories.endpoint_projection_repository import EndpointProjectionRepository
from app.services.indexer.embedding_provider import EmbeddingProvider
from app.services.search.keyword_search import tokenize_terms

#: 내부 두 lookup 과 merged list 의 폭. code constant — env tuning 대상이 아니다(§3.2).
REPR_ARM_WIDTH = 50

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


def endpoint_representation_enabled(raw: str | bool | None) -> bool:
    """원시 flag 값을 bool 로 좁힌다(opt-in — 미인식·미설정 값은 전부 False)."""
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in _TRUE_TOKENS


@dataclass(frozen=True)
class ReprArmTraceRow:
    """merged list 1건의 좌표 — attribution gate 재현용(§6 attribution)."""

    endpoint_id: str
    fts_rank: int | None
    vector_rank: int | None
    merged_rank: int
    winning_source: str  # "repr_vector" | "repr_fts"


@dataclass(frozen=True)
class EndpointRepresentationResult:
    """`endpoint_repr` arm 산출물."""

    #: 바깥 RRF 에 넣을 endpoint id 순서(merged best-rank).
    ordered_endpoint_ids: list[str]
    trace: list[ReprArmTraceRow]
    fts_hit_ids: list[str]
    vector_hit_ids: list[str]
    #: dense lookup 을 돌렸는지. 비의미 프로바이더면 arm 전체가 empty 이고 False.
    dense_enabled: bool


class EndpointRepresentationSearch:
    """canonical projection FTS + dense 를 endpoint 단위 best-rank 로 병합하는 arm."""

    def __init__(
        self,
        projection_repo: EndpointProjectionRepository,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        """projection 저장소와 임베딩 프로바이더를 보관한다."""
        self._projection_repo = projection_repo
        self._embedding_provider = embedding_provider

    def search(
        self,
        query: str,
        *,
        document_id: str | None = None,
        project: str | None = None,
    ) -> EndpointRepresentationResult:
        """원 query 하나로 canonical FTS/dense 를 조회해 merged rank list 를 만든다.

        merge 규칙(§3.1): endpoint id 별 두 내부 rank 의 최솟값으로 접는다.
        동점은 `repr_vector` 우선, 그 다음 endpoint id 오름차순.

        `is_semantic == false` 면 FTS·vector lookup 을 모두 호출하지 않고 strict
        empty 를 돌려준다(`docs/architect-review/102`).
        """
        if not self._embedding_provider.is_semantic:
            return EndpointRepresentationResult(
                ordered_endpoint_ids=[],
                trace=[],
                fts_hit_ids=[],
                vector_hit_ids=[],
                dense_enabled=False,
            )

        terms = tokenize_terms(query)
        fts_hits = (
            self._projection_repo.search_projection_by_text(
                terms, REPR_ARM_WIDTH, document_id=document_id, project=project
            )
            if terms
            else []
        )

        dense_enabled = True
        query_vector = self._embedding_provider.embed_query(query)
        vector_hits = self._projection_repo.search_projection_by_vector(
            query_vector, REPR_ARM_WIDTH, document_id=document_id, project=project
        )

        fts_rank = {hit.endpoint_id: rank for rank, hit in enumerate(fts_hits, start=1)}
        vector_rank = {
            hit.endpoint_id: rank for rank, hit in enumerate(vector_hits, start=1)
        }

        merged: list[tuple[int, int, str, int | None, int | None, bool]] = []
        for endpoint_id in fts_rank.keys() | vector_rank.keys():
            f_rank = fts_rank.get(endpoint_id)
            v_rank = vector_rank.get(endpoint_id)
            best = min(r for r in (f_rank, v_rank) if r is not None)
            from_vector = v_rank is not None and (f_rank is None or v_rank <= f_rank)
            # 정렬 키: (best rank, vector 우선=0, endpoint id 오름차순)
            merged.append(
                (best, 0 if from_vector else 1, endpoint_id, f_rank, v_rank, from_vector)
            )
        merged.sort(key=lambda row: (row[0], row[1], row[2]))
        merged = merged[:REPR_ARM_WIDTH]

        ordered = [row[2] for row in merged]
        trace = [
            ReprArmTraceRow(
                endpoint_id=row[2],
                fts_rank=row[3],
                vector_rank=row[4],
                merged_rank=merged_rank,
                winning_source="repr_vector" if row[5] else "repr_fts",
            )
            for merged_rank, row in enumerate(merged, start=1)
        ]
        return EndpointRepresentationResult(
            ordered_endpoint_ids=ordered,
            trace=trace,
            fts_hit_ids=list(fts_rank),
            vector_hit_ids=list(vector_rank),
            dense_enabled=dense_enabled,
        )
