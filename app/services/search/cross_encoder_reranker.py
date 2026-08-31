"""P3 local cross-encoder rerank (`docs/architect-review/96`).

RRF 가 만든 상위 `RERANK_WIDTH` 후보를 query-endpoint joint relevance 로 재정렬한다.
후보를 생성·주입하지 않고, baseline final 안의 ``both`` 후보는 원래 slot 에 HARD lock
한다(§3). flag(`DOCS_MCP_SEARCH_CROSS_ENCODER_ENABLED`) 가 꺼져 있으면 composition 이
reranker 를 만들지 않으므로 이 모듈의 어떤 코드도 실행되지 않는다.

`LocalCrossEncoderReranker` 는 `LocalEmbeddingProvider` 와 같은 계층의 provider 로,
composition 에서 주입 가능하고 테스트 fake(`scorer`) 를 허용한다. 모델 asset 은 배포
전 로컬 cache/image 에 존재해야 하며 load 는 오프라인 전용이다(startup·request 중
network fetch 금지). asset 부재·load 실패는 `CrossEncoderUnavailableError` 로 올려
호출측이 baseline 순서로 fail-closed 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from app.core.logging import get_logger
from app.services.search.rrf import FusedResult

_LOG = get_logger("docs_mcp.search.cross_encoder")

#: pinned model identity — 운영 중 임의 변경 금지(`docs/architect-review/96` §4.1).
#: 이름/`main` tag 가 아니라 repo revision 으로 고정한다. asset licence/digest 를
#: build manifest 에 함께 동결하는 것은 lead 별도 승인 사항이다(§8.1).
CROSS_ENCODER_MODEL = "BAAI/bge-reranker-v2-m3"
CROSS_ENCODER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"

#: candidate identity 의 일부(§2.2). trace 에 함께 기록한다.
CROSS_ENCODER_QUERY_TOKEN_BUDGET = 64
CROSS_ENCODER_MAX_LENGTH = 512
CROSS_ENCODER_BATCH_SIZE = 50
CROSS_ENCODER_DEVICE = "cpu"
RERANK_DOCUMENT_FORMAT_VERSION = "v1"

#: 재점수 폭 N. 항상 ``min(RERANK_WIDTH, len(base_wide))`` 만 비교한다(§2.1) —
#: production wide 폭 50 과 같은 좌표이며 `top_k` 가 더 작아도 50 개를 본다.
RERANK_WIDTH = 50

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


def cross_encoder_enabled(raw: str | bool | None) -> bool:
    """원시 flag 값을 bool 로 좁힌다(opt-in — 미인식·미설정 값은 전부 False)."""
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in _TRUE_TOKENS


class CrossEncoderUnavailableError(RuntimeError):
    """모델 asset 부재·load 실패·inference 실패. 호출측은 baseline 순서로 degrade 한다."""


class CrossEncoderReranker(Protocol):
    """query 와 endpoint 문서를 함께 넣어 relevance score 만 돌려주는 최소 계약."""

    def score_pairs(self, query: str, documents: list[str]) -> list[float]:
        """`documents` 각 원소에 대한 query-document relevance score(높을수록 관련)."""
        ...


def rerank_document(endpoint: object) -> str:
    """endpoint 를 format v1 의 고정 순서 `rerank_document` 문자열로 직렬화한다(§2.2).

    존재하는 필드만 쓰고 누락 필드는 빈 행을 만들지 않고 생략한다. route/summary/
    field name 을 앞에 둬 긴 description 뒤에서 parameter 정보가 조용히 잘리지 않게
    한다 — 모든 후보에 같은 직렬화 순서를 적용하는 입력 계약이지 structured
    augmentation 이 아니다. 관계(parameters/request_body/responses) 는 lazy-load 될
    수 있고 접근 실패해도 그 행만 생략한다.
    """
    method = str(getattr(endpoint, "method", "") or "").upper()
    path = str(getattr(endpoint, "path", "") or "")
    lines = [f"{method} {path}".strip()]

    summary = str(getattr(endpoint, "summary", "") or "").strip()
    if summary:
        lines.append(f"summary: {summary}")
    operation_id = str(getattr(endpoint, "operation_id", "") or "").strip()
    if operation_id:
        lines.append(f"operation_id: {operation_id}")
    description = str(getattr(endpoint, "description", "") or "").strip()
    if description:
        lines.append(f"description: {description}")

    params = _param_tokens(endpoint)
    if params:
        lines.append(f"parameters: {' '.join(params)}")
    body_fields = _schema_property_names(_safe(lambda: getattr(endpoint, "request_body", None)))
    if body_fields:
        lines.append(f"request_body_fields: {' '.join(body_fields)}")
    response_fields = _response_property_names(endpoint)
    if response_fields:
        lines.append(f"response_fields: {' '.join(response_fields)}")
    return "\n".join(lines)


def apply_slot_lock(
    base_wide: Sequence[FusedResult],
    k: int,
    scores: Mapping[str, float],
) -> list[FusedResult]:
    """§3 both-arm subset HARD slot lock 을 적용해 RRF-return 앞 `k` 개를 만든다.

    1. `base_final = base_wide[:k]` snapshot.
    2. 그 안 `match_type == "both"` 인 ref 의 0-based slot 을 lock.
    3. top `N = min(RERANK_WIDTH, len(base_wide))` 을 score 하되 locked ref 는 점수와
       무관하게 snapshot slot 유지.
    4. `base_wide[:N]` 에서 locked 를 뺀 후보를 score 내림차순(동점: 원 base_wide
       rank 오름차순, 그다음 ref_id 오름차순)으로 정렬해 빈 slot 을 앞에서부터 채운다.

    `scores` 는 `base_wide[:N]` 의 ref_id 를 모두 덮어야 한다. baseline final `both`
    ref 의 id·존재·slot·상대 순서는 불변이다.
    """
    if k <= 0:
        return []
    n = min(RERANK_WIDTH, len(base_wide))
    pool = list(base_wide[:n])
    original_rank = {f.ref_id: i for i, f in enumerate(base_wide)}

    locked: dict[int, FusedResult] = {
        slot: f
        for slot, f in enumerate(base_wide[:k])
        if f.match_type == "both"
    }
    locked_ids = {f.ref_id for f in locked.values()}

    non_locked = sorted(
        (f for f in pool if f.ref_id not in locked_ids),
        key=lambda f: (-scores[f.ref_id], original_rank[f.ref_id], f.ref_id),
    )

    result: list[FusedResult | None] = [None] * k
    for slot, f in locked.items():
        if 0 <= slot < k:
            result[slot] = f
    fill = iter(non_locked)
    for slot in range(k):
        if result[slot] is None:
            result[slot] = next(fill, None)
    return [f for f in result if f is not None]


class LocalCrossEncoderReranker:
    """pinned local cross-encoder 를 CPU 에서 오프라인 load 해 pair score 를 낸다.

    `scorer` 를 주입하면 그대로 쓴다(테스트 fake). 주입하지 않으면 배포 전 반입된
    asset 을 `local_files_only=True` 로 load 한다 — 실패 시 `CrossEncoderUnavailableError`.
    """

    def __init__(
        self,
        model_name: str = CROSS_ENCODER_MODEL,
        revision: str = CROSS_ENCODER_REVISION,
        *,
        scorer: Callable[[str, list[str]], list[float]] | None = None,
    ) -> None:
        """모델 identity 를 보관하고, scorer 가 없으면 오프라인 asset 을 load 한다."""
        self.model_name = model_name
        self.revision = revision
        self._scorer = scorer if scorer is not None else _load_offline_scorer(model_name, revision)

    def score_pairs(self, query: str, documents: list[str]) -> list[float]:
        """query 와 각 document 의 relevance score 리스트(입력 순서 유지)."""
        if not documents:
            return []
        scores = self._scorer(query, documents)
        if len(scores) != len(documents):
            raise CrossEncoderUnavailableError(
                f"score 개수 불일치: {len(scores)} != {len(documents)}"
            )
        return [float(s) for s in scores]


def _load_offline_scorer(
    model_name: str, revision: str
) -> Callable[[str, list[str]], list[float]]:
    """`transformers`/`torch`(sentence-transformers 전이 의존성) 로 오프라인 scorer 를 만든다.

    새 PyPI runtime 의존성을 추가하지 않는다. 패키지 부재·asset 부재·load 실패는
    전부 `CrossEncoderUnavailableError` 로 올린다. startup·request 중 network fetch 는
    하지 않는다(`local_files_only=True`).
    """
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - 의존성 부재 경로
        raise CrossEncoderUnavailableError("transformers/torch 미설치") from exc

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model_name, revision=revision, local_files_only=True
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            model_name, revision=revision, local_files_only=True
        )
    except Exception as exc:  # pragma: no cover - asset 부재/손상 경로
        raise CrossEncoderUnavailableError(f"cross-encoder asset load 실패: {model_name}") from exc

    model.to(CROSS_ENCODER_DEVICE)
    model.eval()

    def score(query: str, documents: list[str]) -> list[float]:
        query_ids = tokenizer.encode(query, add_special_tokens=False)[
            :CROSS_ENCODER_QUERY_TOKEN_BUDGET
        ]
        query_capped = tokenizer.decode(query_ids)
        # ponytail: 고정 50-pair 단일 batch. CPU OOM 관측되면 고정 sub-batch 로만 쪼갠다.
        encoded = tokenizer(
            [query_capped] * len(documents),
            documents,
            padding=True,
            truncation="only_second",
            max_length=CROSS_ENCODER_MAX_LENGTH,
            return_tensors="pt",
        ).to(CROSS_ENCODER_DEVICE)
        with torch.inference_mode():
            logits = model(**encoded).logits
        return logits.reshape(-1).tolist()

    return score


def _param_tokens(endpoint: object) -> list[str]:
    """`parameters:` 행 토큰(`name` 또는 `name: 짧은 설명`)."""
    params = _safe(lambda: list(getattr(endpoint, "parameters", []) or []))
    tokens: list[str] = []
    for param in params or []:
        name = str(getattr(param, "name", "") or "").strip()
        if not name:
            continue
        desc = " ".join(str(getattr(param, "description", "") or "").split())
        tokens.append(f"{name}: {desc}" if desc else name)
    return tokens


def _schema_property_names(container: object) -> list[str]:
    """`container.schema['properties']` 의 key 목록(첫 등장 순서)."""
    if container is None:
        return []
    schema = _safe(lambda: getattr(container, "schema", None))
    if not isinstance(schema, Mapping):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return []
    return [str(name) for name in properties]


def _response_property_names(endpoint: object) -> list[str]:
    """모든 response schema 의 property name 합집합(첫 등장 순서 유지)."""
    responses = _safe(lambda: list(getattr(endpoint, "responses", []) or []))
    seen: list[str] = []
    for response in responses or []:
        for name in _schema_property_names(response):
            if name not in seen:
                seen.append(name)
    return seen


def _safe(getter: Callable[[], object]) -> object:
    """관계 lazy-load 등에서 튀는 예외를 삼키고 None 을 준다(그 행만 생략)."""
    try:
        return getter()
    except Exception:  # 직렬화 실패(lazy-load 등)는 해당 행 생략으로만 처리
        return None
