"""검색 평가 지표(MRR/nDCG/Recall@k) 순수 함수.

`_rank_of_answer`가 주는 "정답 최초 1-based 순위"(없으면 None) 하나만으로
전부 계산 가능하다. DB·모델 의존이 없어 pytest 단위테스트로 바로 검증된다.
"""

from __future__ import annotations

import math


def reciprocal_rank(rank: int | None) -> float:
    """정답 순위의 역수(MRR 구성요소). 미검출(None)이면 0.0."""
    if rank is None:
        return 0.0
    return 1.0 / rank


def dcg_at(rank: int | None, k: int) -> float:
    """이진·단일 관련성 기준 DCG@k. rank가 k 밖이거나 미검출이면 0.0.

    IDCG(이상적 순위 r=1)는 1.0이므로 이 값이 곧 nDCG@k다(단일-관련 근사).
    """
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def recall_at(rank: int | None, k: int) -> int:
    """정답이 top-k 안에 있으면 1, 아니면 0."""
    if rank is None or rank > k:
        return 0
    return 1
