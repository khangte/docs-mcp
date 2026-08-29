# Text-primary Bounded Structured Augmentation Implementation Plan

> **For developer:** lead 승인 후 이 계획을 Task 순서대로 실행한다. developer는 커밋하지 않고 각 Task의 변경·테스트 결과를 보고하며, 커밋은 lead가 아래 경계와 메시지대로 수행한다.

**Goal:** 현행 text keyword + vector wide RRF 순서를 primary로 유지하면서, base-wide의 vector-only 문서만 A/B/C original-query 구조 점수로 최대 한 칸 승격하는 기본-OFF postprocessor를 구현한다.

**Architecture:** `EndpointCandidateSearch._search_rrf()`가 keyword/vector top-width 결과로 base-wide RRF를 먼저 완성한다. 그 뒤 text keyword-backed ref를 protected로 고정하고, base-wide에 이미 있는 vector-only ref만 SQL 1회로 구조 점수화해 서로 겹치지 않는 adjacent max-one-swap을 적용한 후 최종 `top_k`를 자른다. exact/fallback/document search와 RRF 점수·arm 기여는 변경하지 않는다.

**Tech Stack:** Python 3.12, dataclasses, SQLAlchemy 2.x, PostgreSQL `tsvector`/`ts_rank`, pytest, 기존 corpus-eval harness

---

## 0. 실행 전 고정 조건

lead는 Task 1 위임 전에 freeze 산출물과 verdict 86이 commit·push된 상태인지 확인한다. 구현은 같은 commit 위에서 시작하고 다음 identity를 바꾸지 않는다.

```text
product_source_sha = 961bccad9d7d7f169ea5ee17c81581782c441bec
rules_git_sha      = dbc29008aa9803fd708bf619d263f76925e4d2a6
query_sha256       = 1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf
split_sha256       = 701c43479425848c7af8f74360b88adb8f375d8bc801986ea2f684b5d45541e6
```

`git status --short`에 developer의 미보고 변경이 있으면 Task를 시작하지 않는다. fixture·manifest·threshold는 구현 Task의 수정 대상이 아니다.

## 1. 파일 구조 결정

### 신규 파일

- `app/services/search/structured_augmentation.py`
  - `MAX_STRUCTURED_PROMOTION=1`
  - pure adjacent-swap postprocessor
  - base/final rank와 immutable RRF 필드를 담는 trace DTO
- `tests/unit/test_structured_augmentation.py`
  - 설계 84 §4 안전계약과 tie/non-overlap pure unit tests
- `tests/unit/test_structured_augmentation_repository.py`
  - A/B/C-only SQL 점수, ref filter, 단일 round-trip tests
- `tests/unit/test_structured_augmentation_settings.py`
  - 기본-OFF env와 boolean parsing tests
- `tests/fixtures/corpus_eval/compare_v3_candidate.py`
  - 네 gate run을 비교해 freeze 85 HARD/EFFECTIVENESS와 boundary crossing을 판정
- `tests/unit/test_corpus_eval_v3_candidate_gates.py`
  - candidate-specific HARD 9항목과 threshold comparator unit tests

### 수정 파일

- `app/core/config.py`
  - `DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED` 기본-OFF setting 추가
- `app/composition.py`
  - `AppState` wiring과 `search_lexical_field=text` 배타 가드 입력 전달
- `app/repositories/chunk_repository.py`
  - base-wide vector-only ref 전용 A/B/C original-query batch scorer 추가
- `app/services/search/endpoint_candidate_search.py`
  - base-wide RRF 완성 후 postprocessor 삽입, request-scoped eval trace hook 추가
- `tests/unit/test_endpoint_candidate_search.py`
  - OFF parity, text-only guard, wide-before-cut, exact/fallback 우회 tests
- `tests/fixtures/corpus_eval/run_corpus_eval.py`
  - augmentation OFF/ON 실행 옵션, trace JSON, eval identity의 implementation SHA 기록

### 수정하지 않는 파일

- `app/services/search/rrf.py`: `RRF_K=60`, arm weight `1/1`, RRF 산식 불변
- `app/services/search/keyword_search.py`: text arm 후보·점수·순위와 variant filter 규약 불변
- `app/models/chunk.py`: `search_tsv` A/B/C/D `setweight` 배치 불변
- `app/services/indexer/endpoint_structure.py`: `OPERATION_ALIASES` 불변
- `tests/fixtures/corpus_eval/queries_gate_v3.json`
- `tests/fixtures/corpus_eval/gate_manifest_v3.json`
- 설계 85의 threshold

## 2. 고정 데이터 흐름과 인터페이스

`EndpointCandidateSearch._search_rrf()`의 순서는 다음과 같아야 한다.

```text
original query
  ├─ KeywordSearch(text_tsv, original+variants filter, original score) ── keyword top-width
  └─ VectorSearch(original+variants) ─────────────────────────────────── vector top-width
                                      ↓
                         reciprocal_rank_fuse(top_k=width)
                                      ↓
                      base-wide RRF 완성·trace 고정
                                      ↓
         protected = keyword top-width ref_id set
         eligible  = base-wide ref_id - protected (vector-only only)
                                      ↓
       search_tsv A/B/C original-query score, eligible ref_id IN (...) SQL 1회
                                      ↓
         strict score improvement + protected barrier + non-overlap adjacent swap
                                      ↓
                       final-wide[:requested top_k]
```

현행 `reciprocal_rank_fuse(..., top_k=top_k)` 호출을 `top_k=width`로 바꾸는 이유는 rank 11→10 crossing을 postprocessor가 볼 수 있어야 하기 때문이다. RRF 함수·상수·점수는 수정하지 않는다.

### Pure postprocessor 계약

`app/services/search/structured_augmentation.py`에 다음 public surface를 둔다.

```python
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from app.services.search.rrf import FusedResult

MAX_STRUCTURED_PROMOTION = 1


@dataclass(frozen=True)
class AugmentationTraceRow:
    ref_id: str
    base_rank: int
    final_rank: int
    augmentation_score: float
    protected: bool
    rrf_score: float
    contributing_arms: tuple[str, ...]


@dataclass(frozen=True)
class AugmentationOutcome:
    fused: tuple[FusedResult, ...]
    trace: tuple[AugmentationTraceRow, ...]


@dataclass(frozen=True)
class RrfSearchTrace:
    augmentation_enabled: bool
    keyword_hits: tuple[tuple[str, float], ...]
    vector_hits: tuple[tuple[str, float], ...]
    base_wide: tuple[FusedResult, ...]
    protected_ref_ids: frozenset[str]
    structured_scores: tuple[tuple[str, float], ...]
    final_wide: tuple[FusedResult, ...]


def apply_structured_augmentation(
    base_wide: Sequence[FusedResult],
    *,
    protected_ref_ids: frozenset[str],
    augmentation_scores: Mapping[str, float],
) -> AugmentationOutcome:
    ...
```

실제 구현은 base rank 2부터 아래 문서를 promotion candidate로 보는 top-down scan이다. lower score가 upper score보다 **엄격히 클 때만**, 두 ref 모두 protected가 아니고 이번 scan에서 아직 swap에 참여하지 않았을 때만 자리를 바꾼다. swap한 두 ref를 `used`에 넣어 이후 비교에서 제외한다. 동점은 no-op이다.

```python
ranked = list(base_wide)
used: set[str] = set()
for lower_index in range(1, len(ranked)):
    upper_index = lower_index - 1
    upper = ranked[upper_index]
    lower = ranked[lower_index]
    if upper.ref_id in used or lower.ref_id in used:
        continue
    if upper.ref_id in protected_ref_ids or lower.ref_id in protected_ref_ids:
        continue
    if augmentation_scores.get(lower.ref_id, 0.0) <= augmentation_scores.get(
        upper.ref_id, 0.0
    ):
        continue
    ranked[upper_index], ranked[lower_index] = lower, upper
    used.update((upper.ref_id, lower.ref_id))
```

반환 trace는 base/final rank를 ref_id로 다시 계산한다. `FusedResult` 객체를 복제하거나 score/match_type/contributing_arms를 바꾸지 않는다.

### SQL batch scorer 계약

`ChunkRepository`에 다음 메서드를 추가한다.

```python
def score_endpoint_structured_augmentation(
    self,
    terms: Sequence[str],
    ref_ids: Sequence[str],
) -> dict[str, float]:
    """Original-query A/B/C 점수를 ref_id 제한 SQL 1회로 반환한다."""
```

PostgreSQL weight array 순서는 `{D,C,B,A}`다. 기존 `_STRUCTURED_RANK_WEIGHTS`는 그대로 두고, augmentation 전용 배열은 D만 0으로 고정해 A/B/C 기존 배치를 보존한다.

```python
_STRUCTURED_AUGMENTATION_RANK_WEIGHTS = text(
    "'{0.0, 0.2, 0.4, 1.0}'::float4[]"
)
```

메서드는 `terms`와 `ref_ids`를 dedupe하고 어느 쪽이든 비면 SQL 없이 `{}`를 반환한다. 나머지는 `_build_tsquery_str(terms, [])`로 original-query tsquery를 한 번 만들고 다음 형태의 statement 한 번만 실행한다.

```python
rank = func.ts_rank(
    _STRUCTURED_AUGMENTATION_RANK_WEIGHTS,
    Chunk.search_tsv,
    tsq,
)
stmt = (
    select(Chunk.ref_id, func.max(rank).label("augmentation_score"))
    .where(Chunk.chunk_type == "endpoint")
    .where(Chunk.ref_id.in_(candidate_ref_ids))
    .group_by(Chunk.ref_id)
)
```

호출부는 반환에 없는 eligible ref를 `0.0`으로 채운다. variant와 alias-expanded query는 이 메서드에 넘기지 않는다. `search_tsv`의 D lexeme가 query에 맞더라도 D weight가 0이므로 augmentation score는 0이다.

### Setting과 배타 가드

env 이름은 다음으로 고정한다.

```text
DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED
```

기본값은 `false`다. `1/true/yes`만 True로 읽고 그 밖의 값은 False로 처리한다. `AppState` 필드는 `structured_augmentation_enabled: bool = False`다.

실제 적용 조건은 다음 conjunction 하나다.

```python
self._structured_augmentation_enabled = (
    structured_augmentation_enabled and search_lexical_field == "text"
)
```

`search_lexical_field="structured"`이면 env가 True여도 postprocessor와 score SQL을 모두 건너뛴다. fallback 전략은 `_search_rrf()`를 호출하지 않으므로 자동으로 제외된다. document search에는 setting을 전달하지 않는다.

## 3. Task별 구현·커밋 계획

### Task 1: 기본-OFF setting과 composition guard wiring

**Files:**

- Create: `tests/unit/test_structured_augmentation_settings.py`
- Modify: `app/core/config.py`
- Modify: `app/composition.py`
- Modify: `tests/unit/test_endpoint_candidate_search.py`

- [ ] **Step 1: setting 실패 테스트 작성**

```python
ENV_KEY = "DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED"


def test_structured_augmentation_defaults_off(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert Settings().structured_augmentation_enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes"])
def test_structured_augmentation_reads_explicit_true(monkeypatch, raw):
    monkeypatch.setenv(ENV_KEY, raw)
    assert Settings().structured_augmentation_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "garbage"])
def test_structured_augmentation_rejects_non_true_values(monkeypatch, raw):
    monkeypatch.setenv(ENV_KEY, raw)
    assert Settings().structured_augmentation_enabled is False
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_structured_augmentation_settings.py -q`

Expected: `AttributeError: 'Settings' object has no attribute 'structured_augmentation_enabled'`.

- [ ] **Step 3: 최소 setting/AppState wiring 구현**

`Settings`, `AppState`, `AppState.from_engine()`에 동일 이름의 bool을 추가한다. `build_services()`는 bool과 `state.search_lexical_field`를 `EndpointCandidateSearch` 생성자에 넘긴다. 아직 postprocessor는 호출하지 않는다.

- [ ] **Step 4: structured lexical 배타 가드 테스트 추가**

`EndpointCandidateSearch` 생성자에 두 값을 받아 위 conjunction으로 내부 플래그를 만드는 테스트를 추가한다. True+text만 True, True+structured와 False+text는 False여야 한다.

- [ ] **Step 5: 단위 회귀 실행**

Run: `uv run pytest tests/unit/test_structured_augmentation_settings.py tests/unit/test_search_lexical_field_settings.py tests/unit/test_search_strategy_settings.py tests/unit/test_endpoint_candidate_search.py -q`

Expected: 전부 PASS.

- [ ] **Step 6: developer 보고 후 lead commit**

lead가 다음 파일만 stage한다.

```bash
git add app/core/config.py app/composition.py \
  tests/unit/test_structured_augmentation_settings.py \
  tests/unit/test_endpoint_candidate_search.py
git commit -m "feat(search): add guarded augmentation setting"
```

### Task 2: A/B/C-only batch scorer

**Files:**

- Create: `tests/unit/test_structured_augmentation_repository.py`
- Modify: `app/repositories/chunk_repository.py`

- [ ] **Step 1: repository 실패 테스트 작성**

테스트 DB에 동일 query lexeme가 각각 leaf(A), intent(B), context(C), text(D)-only에만 있는 endpoint chunk와 ref filter 밖의 A-hit chunk를 넣는다. SQLAlchemy `before_cursor_execute` counter를 scorer 호출 구간에만 붙인다.

```python
scores = repo.score_endpoint_structured_augmentation(
    ["probe"], ["ref-a", "ref-b", "ref-c", "ref-d"]
)
assert statement_count == 1
assert scores["ref-a"] > scores["ref-b"] > scores["ref-c"] > 0.0
assert scores.get("ref-d", 0.0) == 0.0
assert "ref-outside" not in scores
```

빈 term/ref 입력은 `{}`이고 statement count가 0임을 별도 테스트한다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_structured_augmentation_repository.py -q`

Expected: `ChunkRepository`에 scorer가 없어 FAIL.

- [ ] **Step 3: SQL 1회 최소 구현**

2장의 signature와 SQL을 그대로 구현한다. `_STRUCTURED_RANK_WEIGHTS`와 `search_endpoint_by_text()`는 수정하지 않는다.

- [ ] **Step 4: SQL·기존 lexical 회귀 실행**

Run: `uv run pytest tests/unit/test_structured_augmentation_repository.py tests/unit/test_keyword_search.py -q`

Expected: 전부 PASS. A/B/C 순서, D-only 0, outside ref 부재, 1 round-trip을 모두 확인.

- [ ] **Step 5: developer 보고 후 lead commit**

```bash
git add app/repositories/chunk_repository.py \
  tests/unit/test_structured_augmentation_repository.py
git commit -m "feat(search): batch score structured candidates"
```

### Task 3: Pure bounded postprocessor

**Files:**

- Create: `app/services/search/structured_augmentation.py`
- Create: `tests/unit/test_structured_augmentation.py`

- [ ] **Step 1: 설계 84 §4 보장 5항목 실패 테스트 작성**

고정 `FusedResult` 리스트로 다음을 각각 검증한다.

```python
outcome = apply_structured_augmentation(
    base_wide,
    protected_ref_ids=frozenset({"keyword-a", "keyword-b"}),
    augmentation_scores=scores,
)
base_rank = {x.ref_id: i for i, x in enumerate(base_wide, 1)}
final_rank = {x.ref_id: i for i, x in enumerate(outcome.fused, 1)}

assert all(final_rank[r] == base_rank[r] for r in ("keyword-a", "keyword-b"))
assert all(abs(final_rank[r] - base_rank[r]) <= 1 for r in base_rank)
assert set(final_rank) == set(base_rank)
assert all(
    (after.score, after.match_type, after.contributing_arms)
    == (before.score, before.match_type, before.contributing_arms)
    for before, after in zip(
        sorted(base_wide, key=lambda x: x.ref_id),
        sorted(outcome.fused, key=lambda x: x.ref_id),
        strict=True,
    )
)
```

별도 테스트는 all-zero score에서 `outcome.fused == tuple(base_wide)`임을 확인한다.

- [ ] **Step 2: tie와 non-overlap 실패 테스트 작성**

동점 `[1.0, 1.0]`은 no-op이어야 한다. 전부 unprotected이고 score가 `[0, 3, 4, 5]`인 네 ref는 top-down non-overlap 결과가 `[r2, r1, r4, r3]`이어야 하며 어떤 ref도 두 번 이동하지 않아야 한다.

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/unit/test_structured_augmentation.py -q`

Expected: module import가 없어 FAIL.

- [ ] **Step 4: pure function과 trace DTO 최소 구현**

2장의 scan을 그대로 구현한다. 실행 뒤 `MAX_STRUCTURED_PROMOTION`을 넘는 displacement가 있으면 assertion으로 실패하게 한다. 결과 객체는 원래 `FusedResult` 인스턴스를 순서만 바꿔 보관한다.

- [ ] **Step 5: pure tests 실행**

Run: `uv run pytest tests/unit/test_structured_augmentation.py tests/unit/test_rrf.py -q`

Expected: 전부 PASS.

- [ ] **Step 6: developer 보고 후 lead commit**

```bash
git add app/services/search/structured_augmentation.py \
  tests/unit/test_structured_augmentation.py
git commit -m "feat(search): add bounded structured postprocessor"
```

### Task 4: base-wide RRF 뒤 postprocessor 통합

**Files:**

- Modify: `app/services/search/endpoint_candidate_search.py`
- Modify: `tests/unit/test_endpoint_candidate_search.py`

- [ ] **Step 1: wide-before-cut crossing 실패 테스트 작성**

keyword/vector stubs로 base RRF rank 10과 11이 모두 vector-only가 되게 만들고 rank 11 structured score만 높게 반환한다. requested `top_k=10`에서 augmentation OFF는 기존 rank 10을, ON은 기존 rank 11을 반환해야 한다. scorer가 받은 ref 집합은 base-wide vector-only ref와 정확히 같아야 한다.

- [ ] **Step 2: guard·우회 실패 테스트 작성**

다음을 spy scorer call count와 반환 ref 순서로 검증한다.

- setting OFF: scorer 0회, 기존 RRF 결과 exact parity
- setting ON + lexical `structured`: scorer 0회, augmentation no-op
- fallback: scorer 0회
- exact가 요청 `top_k`를 모두 채움: scorer 0회
- ON + text + RRF: eligible ref가 있을 때 scorer 정확히 1회
- query variant가 있어도 scorer terms는 original query tokenize 결과만

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/unit/test_endpoint_candidate_search.py -q`

Expected: wide-before-cut 및 scorer wiring 테스트 FAIL.

- [ ] **Step 4: `_search_rrf()` 통합 구현**

다음 순서를 지킨다.

```python
base_wide = reciprocal_rank_fuse(
    keyword_ref_ids,
    vector_ref_ids,
    top_k=width,
)
final_wide = base_wide
if self._structured_augmentation_enabled:
    protected = frozenset(keyword_ref_ids)
    eligible = [x.ref_id for x in base_wide if x.ref_id not in protected]
    raw_scores = self._chunk_repo.score_endpoint_structured_augmentation(
        tokenize_terms(query), eligible
    )
    scores = {ref_id: raw_scores.get(ref_id, 0.0) for ref_id in eligible}
    final_wide = list(
        apply_structured_augmentation(
            base_wide,
            protected_ref_ids=protected,
            augmentation_scores=scores,
        ).fused
    )
return self._to_candidates_from_fused(final_wide[:top_k])
```

`query_variants`는 keyword/vector arm에는 현행대로 전달하지만 scorer에는 전달하지 않는다. protected는 keyword top-width ref 전체다.

- [ ] **Step 5: request-scoped trace 추가**

`CandidateSearchOptions`에 repr/compare 제외 optional callback을 추가한다.

```python
rrf_trace_sink: Callable[[RrfSearchTrace], None] | None = field(
    default=None, repr=False, compare=False
)
```

`RrfSearchTrace`는 keyword `(ref_id, score)` 순서, vector `(ref_id, score)` 순서, `base_wide`, protected refs, eligible 전량의 structured scores, `final_wide`, 실제 augmentation enabled 여부를 담는다. callback은 첫 결과를 자르기 전 한 번만 호출한다. callback이 None인 제품 경로의 동작·비용은 trace tuple 생성 외 추가 I/O가 없어야 한다.

현행 `_search_vector_with_variants()`는 ref_id만 반환하므로 ordering을 바꾸지 않는 범위에서 `(ref_id, score)`를 반환하게 좁혀 수정한다. ref별 기존 `best_rank` 선택 규칙은 유지하고, 같은 best rank가 여러 variant에서 나오면 큰 score를 trace 값으로 택한다. RRF 입력은 이 결과에서 ref_id만 추출한다. score는 trace 비교에만 쓰며 RRF 계산과 정렬에는 넣지 않는다.

- [ ] **Step 6: 안전계약·회귀 실행**

Run: `uv run pytest tests/unit/test_endpoint_candidate_search.py tests/unit/test_structured_augmentation.py tests/unit/test_keyword_search.py tests/integration/test_mcp_server.py -q`

Expected: 전부 PASS. exact/fallback/document API 결과에 변경 없음.

- [ ] **Step 7: developer 보고 후 lead commit**

```bash
git add app/services/search/endpoint_candidate_search.py \
  tests/unit/test_endpoint_candidate_search.py
git commit -m "feat(search): augment after wide RRF"
```

### Task 5: v3 gate trace와 판정기

**Files:**

- Modify: `tests/fixtures/corpus_eval/run_corpus_eval.py`
- Create: `tests/fixtures/corpus_eval/compare_v3_candidate.py`
- Create: `tests/unit/test_corpus_eval_v3_candidate_gates.py`

- [ ] **Step 1: trace serialization 실패 테스트 작성**

runner JSON 한 query가 다음 필드를 갖도록 테스트한다.

```json
{
  "id": "v3g001",
  "keyword": [{"ref_id": "...", "score": 0.0, "rank": 1}],
  "vector": [{"ref_id": "...", "score": 0.0, "rank": 1}],
  "base_wide": [{"ref_id": "...", "rank": 1, "rrf_score": 0.0, "arms": ["keyword"]}],
  "protected_ref_ids": ["..."],
  "structured_scores": [{"ref_id": "...", "score": 0.0}],
  "final_wide": [{"ref_id": "...", "rank": 1, "rrf_score": 0.0, "arms": ["keyword"]]
}
```

rank/score의 `0.0`은 schema 예시이며 실제 값을 하드코딩하지 않는다. ref 순서는 rank 순, protected는 ref_id 오름차순으로 직렬화한다.

- [ ] **Step 2: eval identity 실패 테스트 작성**

report root가 product/rules/query/split/corpus/candidate contract와 `implementation_git_sha` full 40자를 포함하지 않으면 comparator가 즉시 FAIL해야 한다. 네 run의 implementation SHA와 shared-index fingerprint가 다르면 비교를 거부한다.

- [ ] **Step 3: candidate-specific HARD 9항목 테스트 작성**

synthetic baseline/candidate traces를 한 항목씩 변조해 다음 각각이 FAIL하는지 검증한다.

1. text arm ref/score/rank 차이
2. vector arm ref/score/rank 차이
3. base-wide RRF ref/rank/score/arm contribution 차이
4. protected absolute slot 이동
5. unprotected `|Δrank| > 1`
6. max A/B/C score 0인데 final-wide 변화
7. base-wide multiset 변화 또는 outside injection
8. exact/fallback/document parity 변화
9. gate route pair가 10/10 미만

- [ ] **Step 4: boundary/effectiveness 테스트 작성**

synthetic rank 배열로 OFF/ON 각각 다음 identity를 검사한다.

```python
crossing_net = count(base_rank == 11 and final_rank == 10) - count(
    base_rank == 10 and final_rank == 11
)
recall_net = count(base_rank > 10 and final_rank <= 10) - count(
    base_rank <= 10 and final_rank > 10
)
assert crossing_net == recall_net
```

protected/no-op query 수를 gain에 더한 synthetic report는 FAIL해야 한다.

- [ ] **Step 5: 실패 확인**

Run: `uv run pytest tests/unit/test_corpus_eval_v3_candidate_gates.py -q`

Expected: runner/comparator 기능이 없어 FAIL.

- [ ] **Step 6: runner와 comparator 최소 구현**

runner에 다음 CLI를 추가한다.

```text
--structured-augmentation {off,on}  # default off
--report-json PATH                  # scratchpad 결과만 허용
```

`on`은 `AppState.from_engine(..., structured_augmentation_enabled=True, search_lexical_field="text")`로 주입한다. v3에서 `--lexical-field structured`와 augmentation on 조합은 parser error로 거부한다. 정확도 1회차만 trace sink를 넘기고 latency 반복에는 sink를 넘기지 않는다.

comparator는 네 JSON을 `baseline_off`, `candidate_off`, `baseline_on`, `candidate_on`으로 받아 gate/final 모드를 분리한다. gate mode는 split이 정확히 gate96인지 확인하고 holdout row가 있으면 거부한다.

- [ ] **Step 7: evaluator 정적 테스트 실행**

Run: `uv run pytest tests/unit/test_corpus_eval_v3_candidate_gates.py tests/unit/test_corpus_eval_v3_novelty.py tests/unit/test_corpus_eval_v2_novelty.py -q`

Expected: 전부 PASS. DB search/eval은 이 Task에서 실행하지 않는다.

- [ ] **Step 8: developer 보고 후 lead commit**

```bash
git add tests/fixtures/corpus_eval/run_corpus_eval.py \
  tests/fixtures/corpus_eval/compare_v3_candidate.py \
  tests/unit/test_corpus_eval_v3_candidate_gates.py
git commit -m "test(eval): enforce v3 augmentation gates"
```

Task 5 lead commit의 full SHA가 실제 `implementation_git_sha`다. 이후 코드나 evaluator가 바뀌면 이전 SHA로 평가하지 않는다.

### Task 6: 구현 전체 회귀와 handoff

**Files:** 변경 없음

- [ ] **Step 1: 전체 정적·단위·integration 회귀**

Run:

```bash
uv run pytest tests/unit/test_structured_augmentation_settings.py \
  tests/unit/test_structured_augmentation_repository.py \
  tests/unit/test_structured_augmentation.py \
  tests/unit/test_endpoint_candidate_search.py \
  tests/unit/test_keyword_search.py \
  tests/unit/test_rrf.py \
  tests/unit/test_corpus_eval_v3_candidate_gates.py \
  tests/unit/test_corpus_eval_v3_novelty.py \
  tests/integration/test_mcp_server.py -q
```

Expected: 전부 PASS, skip/xfail 0.

- [ ] **Step 2: frozen file 무변경 확인**

```bash
sha256sum tests/fixtures/corpus_eval/queries_gate_v3.json
```

Expected:

```text
1da41901a225990492ead8215eb6a5bfde8afde987cafb60f0f74d03cbd84fdf
```

`git diff -- tests/fixtures/corpus_eval/queries_gate_v3.json tests/fixtures/corpus_eval/gate_manifest_v3.json` 출력은 비어 있어야 한다.

- [ ] **Step 3: developer가 lead에 handoff**

보고에는 Task별 lead commit SHA, 최종 implementation full SHA, 전체 테스트 결과, 변경 파일, frozen SHA 확인, search/eval 미실행을 포함한다. 이 Task는 파일 변경과 새 commit을 만들지 않는다.

## 4. 설계 84 §4 단위 보장 매핑

| 보장 | 직접 검증 위치 | 판정 |
|---|---|---|
| protected absolute rank 불변 | `test_structured_augmentation.py`, endpoint integration | protected ref의 base/final rank 전부 동일 |
| unprotected `|Δrank|≤1` | pure property assertion + crafted swaps | 위반 0 |
| max score 0 complete no-op | pure all-zero + endpoint original-query-only test | ref·순서·FusedResult 동일 |
| base-wide 밖 유입 0 | multiset assertion + scorer ref spy | base/final ref multiset 동일, SQL filter 동일 |
| RRF contribution 0 | `score`, `match_type`, `contributing_arms` ref별 비교 | 전부 exact 동일 |
| tie no-op | equal score fixture | 순서 exact 동일 |
| non-overlap swap | `[0,3,4,5]` fixture | `[r2,r1,r4,r3]`, ref당 참여 최대 1회 |

## 5. v3 평가 실행 골격

이 절은 구현 Task가 아니라 lead 승인 뒤 실행자가 따라야 할 순서다. Task 1~6 완료와 최종 implementation full SHA 확정 전에는 실행하지 않는다.

### 5.1 Identity와 네 gate run

같은 shared DB, 같은 final implementation SHA, `--lexical-field text`, `--strategy rrf`, `--split gate`로 네 run을 만든다.

| Run | augmentation | variants |
|---|---|---|
| baseline_off | off | OFF |
| candidate_off | on | OFF |
| baseline_on | off | ON |
| candidate_on | on | ON |

결과는 commit하지 않는 `scratchpad/v3_gate_*.json`에 둔다. 각 JSON은 product source, rules, fixture/split/corpus SHA, candidate contract, shared-index fingerprint, implementation full SHA를 포함한다.

```bash
uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --mode eval --db-url "$V3_EVAL_DB_URL" --strategy rrf \
  --queries-file tests/fixtures/corpus_eval/queries_gate_v3.json \
  --split gate --lexical-field text --structured-augmentation off \
  --report-json scratchpad/v3_gate_baseline_off.json

uv run python tests/fixtures/corpus_eval/run_corpus_eval.py \
  --mode eval --db-url "$V3_EVAL_DB_URL" --strategy rrf \
  --queries-file tests/fixtures/corpus_eval/queries_gate_v3.json \
  --split gate --lexical-field text --structured-augmentation on \
  --report-json scratchpad/v3_gate_candidate_off.json
```

ON 두 run은 같은 명령에 `--with-variants`를 추가하고 각각 `v3_gate_baseline_on.json`, `v3_gate_candidate_on.json`에 쓴다. `$V3_EVAL_DB_URL`은 preflight가 출력한 v3 전용 shared DB URL을 그대로 설정하며 다른 eval DB를 재사용하지 않는다.

### 5.2 Gate HARD 우선순위

comparator는 effectiveness를 계산하기 전에 다음을 모두 PASS해야 한다.

- integrity·execution identity·fallback exactness
- C1 loss 0
- category별 hit 순손실 최대 1, MRR 하락 최대 0.02
- C6 coverage/complete baseline 이상
- route pair 10/10
- empty result 증가 0
- candidate-specific 9항목 전부 PASS

candidate-specific 항목 중 protected preservation은 safety이며 gain이 아니다. base-wide vector-only 후보의 structured score만 존재해야 하고, RRF contribution은 baseline/candidate가 exact 같아야 한다.

### 5.3 Gate EFFECTIVENESS

HARD 전항 PASS 뒤에만 다음을 판정한다.

- Recall@10 OFF/ON 각각 `+3pp` 이상이면서 hit 순증 `+3` 이상
- MRR OFF/ON baseline 이상, 한 activation은 `+0.02` 이상
- nDCG@10 OFF/ON non-decline
- C2+C3+C5: 한 activation `+3`, 다른 activation 손실 0 이상
- Korean gate47 ON 순증 `+2`
- effective route pair 최소 2
- OFF/ON boundary crossing net 각각 `+3`
- 각 crossing net이 해당 Recall@10 paired win-loss와 exact 일치

crossing gain은 base rank 11→final rank 10만, loss는 base rank 10→final rank 11만 센다. protected/no-op/non-regression count를 effectiveness에 합산하면 comparator가 FAIL해야 한다.

### 5.4 Holdout 봉인

gate HARD 전항과 gate EFFECTIVENESS 전항이 모두 PASS하기 전에는 `--split holdout` 또는 `--split all`을 실행하지 않는다. 어느 항목이든 FAIL이면 같은 v3에서 bound, score, implementation, fixture, threshold를 바꿔 재시험하지 않는다.

gate PASS 후에도 holdout 개봉은 lead의 명시적 지시가 있어야 한다. final 120 판정은 freeze 85 §8의 다음 추가 조건을 그대로 사용한다.

- pair gate10/holdout2/all12 전부 safety PASS
- holdout Recall@10 OFF/ON baseline 이상, holdout MRR 하락 각각 최대 0.01
- final Recall@10 OFF/ON 각각 `+3pp`, hit 순증 `+4`
- effective pairs gate2/holdout1/all3 이상
- holdout combined win > loss, win 최소 1
- boundary crossing net OFF/ON 각각 `+4`이며 Recall paired net과 exact 일치

## 6. 명시적 범위 밖

- `_STRUCTURED_RANK_WEIGHTS`, A/B/C setweight 배치, `OPERATION_ALIASES`, `RRF_K`, arm weight 조정
- `MAX_STRUCTURED_PROMOTION`의 env화 또는 1 이외 값
- score normalization, threshold, bonus 계수, top-k preservation의 추가 휴리스틱
- query variant나 alias expansion을 augmentation score에 사용
- base-wide 밖 candidate 조회·주입
- protected text result의 하락 허용
- structured `search_tsv` full swap을 lexical arm으로 다시 사용하는 경로
- fallback/exact/document search에 augmentation 적용
- v3 query, accepted endpoint, split, manifest, threshold 변경
- gate FAIL 뒤 동일 v3 재시험
- 이 계획 승인 전에 developer Task 착수 또는 search/eval 실행

## 7. Lead 승인 포인트

- **I1:** env 이름을 `DOCS_MCP_STRUCTURED_AUGMENTATION_ENABLED`, 기본값 False로 고정한다.
- **I2:** postprocessor 위치를 base-wide RRF 완성 후·final top_k 절단 전으로 고정한다.
- **I3:** protected 집합은 text keyword top-width ref 전체다.
- **I4:** eligible은 base-wide vector-only ref뿐이며 SQL `IN` 1회로만 점수화한다.
- **I5:** augmentation weights는 `{D,C,B,A}={0.0,0.2,0.4,1.0}`으로 A/B/C 기존 비율만 사용한다.
- **I6:** strict-greater, top-down, non-overlap adjacent swap과 `MAX_STRUCTURED_PROMOTION=1`을 고정한다.
- **I7:** setting ON이어도 lexical field가 text가 아니면 완전 no-op이다.
- **I8:** request-scoped trace와 comparator로 freeze 85 HARD/EFFECTIVENESS를 기계 판정한다.
- **I9:** Task 1~5마다 developer 보고 후 lead가 지정 메시지로 커밋한다.
- **I10:** 구현 완료 후 별도 실행 승인 전에는 v3 gate·holdout search를 실행하지 않는다.
