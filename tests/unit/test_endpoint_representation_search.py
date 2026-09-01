"""`endpoint_repr` arm — merge/tie 규칙, 비의미 degrade, query_variants 비수용 검증.

`docs/architect-review/101` §3.

merge/tie 로직은 순수하므로 stub 저장소로 테스트하고, repo SQL 은 실제 색인
1건으로 결정성만 확인한다.
"""

from __future__ import annotations

import inspect
import json

from app.models import EMBEDDING_DIM
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.endpoint_projection_repository import (
    EndpointProjectionRepository,
    ProjectionHit,
)
from app.repositories.endpoint_repository import EndpointRepository
from app.services.indexer.embedding_provider import HashEmbeddingProvider
from app.services.indexer.indexer_service import IndexerService
from app.services.parser.document_router import parse_document
from app.services.search.endpoint_representation_search import (
    REPR_ARM_WIDTH,
    EndpointRepresentationSearch,
)
from tests.fixtures.samples import OPENAPI_3_DOC


class _StubRepo:
    """canned FTS/vector hit 를 돌려주는 stub(merge 로직 단위 테스트용)."""

    def __init__(
        self, fts: list[ProjectionHit], vector: list[ProjectionHit]
    ) -> None:
        self._fts = fts
        self._vector = vector
        self.text_calls: list[list[str]] = []
        self.vector_calls = 0

    def search_projection_by_text(self, terms, top_k, **_kw) -> list[ProjectionHit]:
        self.text_calls.append(list(terms))
        return self._fts[:top_k]

    def search_projection_by_vector(self, query_vector, top_k, **_kw) -> list[ProjectionHit]:
        self.vector_calls += 1
        return self._vector[:top_k]


class _Provider:
    """is_semantic 만 제어하는 임베딩 프로바이더 페이크."""

    def __init__(self, semantic: bool) -> None:
        self._semantic = semantic
        self.embed_query_calls = 0

    @property
    def is_semantic(self) -> bool:
        return self._semantic

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls += 1
        return [0.1] * EMBEDDING_DIM


def _hit(endpoint_id: str) -> ProjectionHit:
    return ProjectionHit(endpoint_id=endpoint_id, method="GET", path="/x", score=1.0)


def test_folds_two_lookups_to_endpoint_best_rank() -> None:
    """endpoint id 별 두 내부 rank 의 최솟값으로 접고, 동점은 repr_vector 우선."""
    repo = _StubRepo(
        fts=[_hit("e1"), _hit("e2")],  # e1 rank1, e2 rank2
        vector=[_hit("e2"), _hit("e3"), _hit("e1")],  # e2 rank1, e3 rank2, e1 rank3
    )
    arm = EndpointRepresentationSearch(repo, _Provider(semantic=True))

    result = arm.search("some query")

    # best: e1=1(fts), e2=1(vec), e3=2(vec) → 동점 e1/e2 는 vec 우선(e2) 먼저
    assert result.ordered_endpoint_ids == ["e2", "e1", "e3"]
    by_id = {row.endpoint_id: row for row in result.trace}
    assert (by_id["e2"].merged_rank, by_id["e2"].winning_source) == (1, "repr_vector")
    assert (by_id["e1"].merged_rank, by_id["e1"].winning_source) == (2, "repr_fts")
    assert by_id["e1"].fts_rank == 1 and by_id["e1"].vector_rank == 3
    assert by_id["e3"].vector_rank == 2 and by_id["e3"].fts_rank is None


def test_tie_breaks_repr_vector_then_endpoint_id() -> None:
    """같은 best rank 면 vector 로 들어온 쪽이 먼저, 그 다음 endpoint id 오름차순."""
    repo = _StubRepo(fts=[_hit("a")], vector=[_hit("z")])  # a fts rank1, z vec rank1
    arm = EndpointRepresentationSearch(repo, _Provider(semantic=True))

    result = arm.search("q")

    assert result.ordered_endpoint_ids == ["z", "a"]  # id 순이면 a 먼저지만 vec 우선


def test_non_semantic_provider_returns_strict_empty() -> None:
    """비의미 프로바이더면 FTS·vector lookup 을 모두 안 부르고 빈 결과(`docs/102`)."""
    provider = _Provider(semantic=False)
    repo = _StubRepo(fts=[_hit("e1"), _hit("e2")], vector=[_hit("e9")])
    arm = EndpointRepresentationSearch(repo, provider)

    result = arm.search("q")

    assert provider.embed_query_calls == 0
    assert repo.vector_calls == 0
    assert repo.text_calls == []
    assert result.dense_enabled is False
    assert result.ordered_endpoint_ids == []
    assert result.trace == []
    assert result.fts_hit_ids == []
    assert result.vector_hit_ids == []


def test_blank_query_yields_no_fts_terms() -> None:
    """토큰이 없으면 FTS 를 조회하지 않는다(semantic 이라 dense 만 돌고 비면 결과 전무)."""
    repo = _StubRepo(fts=[_hit("e1")], vector=[])
    arm = EndpointRepresentationSearch(repo, _Provider(semantic=True))

    result = arm.search("   !!!   ")

    assert repo.text_calls == []
    assert result.ordered_endpoint_ids == []


def test_merged_list_capped_at_arm_width() -> None:
    """merged list 는 REPR_ARM_WIDTH 로 자른다."""
    repo = _StubRepo(
        fts=[_hit(f"e{i:03d}") for i in range(REPR_ARM_WIDTH + 10)],
        vector=[],
    )
    arm = EndpointRepresentationSearch(repo, _Provider(semantic=True))

    result = arm.search("q")

    assert len(result.ordered_endpoint_ids) == REPR_ARM_WIDTH


def test_search_does_not_accept_query_variants() -> None:
    """§2.2: 이 arm 은 원 query 하나만 본다 — variant 라우팅 인자가 없다."""
    params = list(inspect.signature(EndpointRepresentationSearch.search).parameters)
    assert params == ["self", "query", "document_id", "project"]
    src = inspect.getsource(EndpointRepresentationSearch)
    assert "query_variants" not in src
    assert "KeywordSearch" not in src and "VectorSearch" not in src


def test_is_deterministic() -> None:
    """같은 stub 입력은 항상 같은 순서/trace."""
    repo = _StubRepo(
        fts=[_hit("e2"), _hit("e1")], vector=[_hit("e3"), _hit("e1"), _hit("e2")]
    )
    arm = EndpointRepresentationSearch(repo, _Provider(semantic=True))
    first = arm.search("q")
    second = arm.search("q")
    assert first == second


class _SemanticHash:
    """HashEmbeddingProvider 를 is_semantic=True 로만 바꾼 래퍼."""

    def __init__(self) -> None:
        self._d = HashEmbeddingProvider(dim=EMBEDDING_DIM)

    @property
    def dim(self) -> int:
        return self._d.dim

    @property
    def is_semantic(self) -> bool:
        return True

    def embed_documents(self, texts, labels=None):
        return self._d.embed_documents(texts, labels=labels)

    def embed_query(self, text):
        return self._d.embed_query(text)


def _index(session, provider):
    from app.models import Document

    raw = json.dumps(OPENAPI_3_DOC)
    document = Document(
        id="doc-repr",
        project="default",
        source_url=None,
        title="t",
        version="1",
        doc_type="openapi",
        content_hash="h",
        raw_text=raw,
    )
    session.add(document)
    session.flush()
    IndexerService(
        endpoint_repo=EndpointRepository(session),
        chunk_repo=ChunkRepository(session),
        embedding_provider=provider,
        projection_repo=EndpointProjectionRepository(session),
    ).index_document(document=document, parsed=parse_document(raw, "openapi"))
    session.commit()


def test_repo_lookup_is_deterministic_end_to_end(db_session) -> None:
    """실색인 1건에 arm.search 3회 반복 → 순서 동일, FTS 가 관련 endpoint 를 올린다."""
    _index(db_session, _SemanticHash())
    arm = EndpointRepresentationSearch(
        EndpointProjectionRepository(db_session), _SemanticHash()
    )

    runs = [arm.search("add a new pet").ordered_endpoint_ids for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert runs[0]  # 후보가 비지 않는다

    endpoints = {e.id: e for e in EndpointRepository(db_session).list_by_document("doc-repr")}
    top = endpoints[runs[0][0]]
    assert (top.method, top.path) == ("POST", "/pet")


def test_repo_lookup_non_semantic_returns_empty(db_session) -> None:
    """비의미 프로바이더면 색인이 돼 있어도 arm 은 strict empty 를 낸다(`docs/102`)."""
    _index(db_session, HashEmbeddingProvider(dim=EMBEDDING_DIM))
    arm = EndpointRepresentationSearch(
        EndpointProjectionRepository(db_session), HashEmbeddingProvider(dim=EMBEDDING_DIM)
    )

    result = arm.search("delete pet")
    assert result.dense_enabled is False
    assert result.ordered_endpoint_ids == []
    assert result.trace == []
    assert result.fts_hit_ids == []
    assert result.vector_hit_ids == []
