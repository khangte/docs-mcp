"""평가 지표 순수 함수(MRR/nDCG/Recall@k) 단위 테스트(DB·모델 의존 없음)."""

from __future__ import annotations

from tests.fixtures.rrf_eval.metrics import dcg_at, recall_at, reciprocal_rank


def test_reciprocal_rank_of_top_result_is_one() -> None:
    """정답이 1위면 역수순위는 1.0이다."""
    # Arrange
    rank = 1

    # Act
    result = reciprocal_rank(rank)

    # Assert
    assert result == 1.0


def test_reciprocal_rank_of_second_result_is_half() -> None:
    """정답이 2위면 역수순위는 0.5다."""
    # Arrange
    rank = 2

    # Act
    result = reciprocal_rank(rank)

    # Assert
    assert result == 0.5


def test_reciprocal_rank_of_undetected_answer_is_zero() -> None:
    """정답을 못 찾았으면(rank=None) 역수순위는 0.0이다."""
    # Arrange
    rank = None

    # Act
    result = reciprocal_rank(rank)

    # Assert
    assert result == 0.0


def test_dcg_at_top_result_within_k_is_one() -> None:
    """정답이 1위고 k 안에 있으면 DCG는 1.0이다(1/log2(2))."""
    # Arrange
    rank, k = 1, 10

    # Act
    result = dcg_at(rank, k)

    # Assert
    assert result == 1.0


def test_dcg_at_third_result_within_k_is_half() -> None:
    """정답이 3위고 k 안에 있으면 DCG는 1/log2(4) = 0.5다."""
    # Arrange
    rank, k = 3, 10

    # Act
    result = dcg_at(rank, k)

    # Assert
    assert result == 0.5


def test_dcg_at_rank_beyond_k_is_zero() -> None:
    """정답 순위가 k를 벗어나면 DCG는 0.0이다."""
    # Arrange
    rank, k = 11, 10

    # Act
    result = dcg_at(rank, k)

    # Assert
    assert result == 0.0


def test_dcg_at_undetected_answer_is_zero() -> None:
    """정답을 못 찾았으면(rank=None) DCG는 0.0이다."""
    # Arrange
    rank, k = None, 10

    # Act
    result = dcg_at(rank, k)

    # Assert
    assert result == 0.0


def test_recall_at_answer_within_k_is_one() -> None:
    """정답 순위가 k 이내면 recall은 1이다."""
    # Arrange
    rank, k = 3, 3

    # Act
    result = recall_at(rank, k)

    # Assert
    assert result == 1


def test_recall_at_answer_beyond_k_is_zero() -> None:
    """정답 순위가 k를 벗어나면 recall은 0이다."""
    # Arrange
    rank, k = 4, 3

    # Act
    result = recall_at(rank, k)

    # Assert
    assert result == 0


def test_recall_at_undetected_answer_is_zero() -> None:
    """정답을 못 찾았으면(rank=None) recall은 0이다."""
    # Arrange
    rank, k = None, 10

    # Act
    result = recall_at(rank, k)

    # Assert
    assert result == 0
