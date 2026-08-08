"""재임베딩 배치(`app/scripts/reembed.py`) 테스트.

alembic 마이그레이션 직후 embedding 컬럼이 전부 NULL 인 상태를 시뮬레이션해,
저장된 청크 텍스트만으로(재파싱·네트워크 없이) 다시 채워지는지 확인한다.
"""

from __future__ import annotations

import pytest

from app.composition import build_services
from app.models.openapi import EMBEDDING_DIM
from app.scripts.reembed import reembed_all
from app.services.indexer.embedding_provider import HashEmbeddingProvider


def _register(app_state, raw: str) -> None:
    services = next(build_services(app_state))
    services.sync_service.register(project="default", source_url=None, raw_document=raw)


def test_reembed_all_matches_provider_output(app_state, sample_openapi_3: str) -> None:
    """재임베딩 후 각 청크의 embedding 은 현재 프로바이더로 그 텍스트를 임베딩한 값과 같다."""
    _register(app_state, sample_openapi_3)
    services = next(build_services(app_state))
    chunk_ids = [c.id for c in services.chunk_repo.list_all()]
    assert chunk_ids  # 공허 참 방지

    total = reembed_all(app_state, batch_size=2)

    assert total == len(chunk_ids)
    expected_provider = HashEmbeddingProvider(dim=EMBEDDING_DIM)
    verify = next(build_services(app_state))
    for chunk in verify.chunk_repo.list_all():
        expected = expected_provider.embed_documents([chunk.text])[0]
        # pgvector 는 float4(단정도)로 저장하므로 float64 정확 비교 대신 근사 비교한다.
        assert chunk.embedding == pytest.approx(expected, abs=1e-6)


def test_reembed_all_fills_null_embeddings(app_state, sample_openapi_3: str) -> None:
    """마이그레이션 직후(NULL) 상태를 시뮬레이션해도 재임베딩 후 NULL 이 남지 않는다."""
    _register(app_state, sample_openapi_3)
    services = next(build_services(app_state))
    for chunk in services.chunk_repo.list_all():
        chunk.embedding = None
    services.session.commit()

    total = reembed_all(app_state, batch_size=3)

    verify = next(build_services(app_state))
    chunks = verify.chunk_repo.list_all()
    assert total == len(chunks)
    assert chunks  # 공허 참 방지
    assert all(chunk.embedding is not None for chunk in chunks)


def test_reembed_all_returns_zero_when_no_chunks(app_state) -> None:
    """청크가 하나도 없으면 아무 것도 하지 않고 0 을 반환한다."""
    assert reembed_all(app_state) == 0
