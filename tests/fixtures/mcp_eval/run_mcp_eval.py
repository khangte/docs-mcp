"""MCP 계층 평가 하네스(B 트랙) 러너.

`docs/architect-review/64_mcp_layer_eval_harness_design.md` §7 계약의 구현이다.
서버가 관측·통제하는 **도구 실행 계약**만 측정한다 — 클라이언트 LLM 의 도구 선택·
인자 선택·답변 생성은 범위 밖이다.

인프로세스 `FastMCP.call_tool()` 디스패치로 read-only tool 9개 × 시나리오 21개를
기본 5회 반복(총 105회) 채점하고, 두 상보 지표를 산출한다.

    Tool Success Rate      >= 99%
    MCP Error / Timeout Rate < 1%

pytest 로 수집되지 않는 독립 스크립트다. 임시 DB + 최소 결정론 시드(Petstore
샘플 + FakeDocumentSource + HashEmbeddingProvider)를 쓰며 외부 HTTP·자격증명은
사용하지 않는다.

사용법(로컬 postgres 필요, `docker compose up -d postgres`):
    uv run python tests/fixtures/mcp_eval/run_mcp_eval.py [--repeat 5]

옵션:
    --repeat N       시나리오별 반복 수(기본 scenarios.json.default_repeat=5, 양의 정수).
                     총 호출이 100 미만이면 목표치 판정을 INSUFFICIENT 로 내고 종료 코드 2.
    --scenarios PATH scenarios.json 경로 override(로컬 진단용).
    --seed PATH      seed.json 경로 override(로컬 진단용).

timeout override CLI 는 없다 — 목표치 의미가 실행자마다 바뀌면 비교가 불가능하다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # 번들 stub 없음, types-jsonschema 미도입

_DIR = Path(__file__).parent
_REPO_ROOT = _DIR.parents[2]

# 스크립트로 직접 실행하면 repo 루트가 sys.path 에 없어 `tests.fixtures.*`,
# `app.*` import 가 실패한다(pytest 로 돌 때는 rootdir 이 자동 추가된다).
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# --- 상수 계약(§3.2, §4.4) --------------------------------------------------

#: Tool Success Rate 분모에 들어가는 read-only tool 9개.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "list_documents",
        "search_endpoints",
        "get_endpoint_details",
        "resolve_ref",
        "list_tags",
        "search_documents",
        "get_document",
        "list_drive_sources",
        "list_notion_sources",
    }
)
#: timeout class "search" — 나머지는 "read".
SEARCH_TOOLS: frozenset[str] = frozenset({"search_endpoints", "search_documents"})
SEARCH_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 2.0
RESOURCE_TIMEOUT_S = 2.0

#: assertion DSL 에서 허용하는 연산자(임의 Python/eval 금지).
_ASSERTION_OPS: frozenset[str] = frozenset({"equals", "contains", "length_gte", "path_exists"})

#: PASS/FAIL 을 내려면 필요한 최소 총 호출 수(§5.2).
MIN_SAMPLE = 100


class HarnessError(Exception):
    """설정·seed·preflight·setup 결함. 종료 코드 2 로 이어진다.

    제품 실행 오류(tool error)와 구분하기 위해 별도 예외로 둔다 — 준비 실패를
    분모에 섞으면 fixture 결함과 서버 회귀를 구별할 수 없다.
    """


def timeout_for(tool: str) -> float:
    """tool 의 timeout 임계(초)를 §4.4 매핑에서 돌려준다."""
    return SEARCH_TIMEOUT_S if tool in SEARCH_TOOLS else READ_TIMEOUT_S


# --- 시나리오 모델과 strict 로더(§5.1) -------------------------------------


@dataclass(frozen=True)
class Expected:
    """시나리오 기대값. outcome 은 "success" | "error"."""

    outcome: str
    code: str | None = None
    assertions: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Scenario:
    """채점 단위 시나리오 한 건."""

    id: str
    tool: str
    arguments: Mapping[str, Any]
    expected: Expected


def _require_keys(
    obj: Mapping[str, Any], *, required: set[str], allowed: set[str], where: str
) -> None:
    """필수 key 존재와 미지 key 부재를 엄격히 검사한다(§5.1: 알 수 없는 key 묵살 금지)."""
    keys = set(obj)
    missing = required - keys
    if missing:
        raise HarnessError(f"{where}: 필수 key 누락 {sorted(missing)}")
    unknown = keys - allowed
    if unknown:
        raise HarnessError(f"{where}: 알 수 없는 key {sorted(unknown)}")


def _parse_expected(raw: Mapping[str, Any], *, where: str) -> Expected:
    """expected 블록을 검증하며 Expected 로 변환한다."""
    if not isinstance(raw, Mapping):
        raise HarnessError(f"{where}: expected 는 object 여야 한다")
    outcome = raw.get("outcome")
    if outcome == "error":
        _require_keys(raw, required={"outcome", "code"}, allowed={"outcome", "code"}, where=where)
        code = raw["code"]
        if not isinstance(code, str) or not code:
            raise HarnessError(f"{where}: error 기대에는 비어 있지 않은 code(str)가 필수다")
        return Expected(outcome="error", code=code)
    if outcome == "success":
        _require_keys(raw, required={"outcome"}, allowed={"outcome", "assertions"}, where=where)
        assertions = raw.get("assertions", [])
        if not isinstance(assertions, list):
            raise HarnessError(f"{where}: assertions 는 list 여야 한다")
        for i, assertion in enumerate(assertions):
            _validate_assertion(assertion, where=f"{where}.assertions[{i}]")
        return Expected(outcome="success", code=None, assertions=tuple(assertions))
    raise HarnessError(f"{where}: outcome 은 'success' 또는 'error' 여야 한다 (got {outcome!r})")


def _validate_assertion(assertion: Any, *, where: str) -> None:
    """assertion DSL 한 건의 구조를 검증한다."""
    if not isinstance(assertion, Mapping):
        raise HarnessError(f"{where}: assertion 은 object 여야 한다")
    op = assertion.get("op")
    if op not in _ASSERTION_OPS:
        raise HarnessError(f"{where}: 허용되지 않는 op {op!r} (허용: {sorted(_ASSERTION_OPS)})")
    allowed = {"op", "path"} if op == "path_exists" else {"op", "path", "value"}
    required = {"op"} if op == "path_exists" else {"op", "value"}
    _require_keys(assertion, required=required, allowed=allowed, where=where)
    if "path" in assertion and not isinstance(assertion["path"], str):
        raise HarnessError(f"{where}: path 는 str 여야 한다")


def load_scenarios(raw: Mapping[str, Any]) -> tuple[int, list[Scenario]]:
    """scenarios.json dict 를 strict 검증하며 (default_repeat, scenarios) 로 만든다.

    Raises:
        HarnessError: schema_version 불일치, id 중복, 미등록 tool, expected 구조
            위반, 알 수 없는 key 등 preflight 불변식 위반(§7.3).
    """
    if raw.get("schema_version") != 1:
        raise HarnessError(
            f"scenarios: schema_version 은 1 이어야 한다 (got {raw.get('schema_version')!r})"
        )
    default_repeat = raw.get("default_repeat", 5)
    if (
        not isinstance(default_repeat, int)
        or isinstance(default_repeat, bool)
        or default_repeat <= 0
    ):
        raise HarnessError(
            f"scenarios: default_repeat 은 양의 정수여야 한다 (got {default_repeat!r})"
        )
    items = raw.get("scenarios")
    if not isinstance(items, list):
        raise HarnessError("scenarios: 'scenarios' 는 list 여야 한다")

    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"scenarios[{i}]"
        if not isinstance(item, Mapping):
            raise HarnessError(f"{where}: object 여야 한다")
        _require_keys(
            item,
            required={"id", "tool", "arguments", "expected"},
            allowed={"id", "tool", "arguments", "expected"},
            where=where,
        )
        scenario_id = item["id"]
        if not isinstance(scenario_id, str) or not scenario_id:
            raise HarnessError(f"{where}: id 는 비어 있지 않은 str 여야 한다")
        if scenario_id in seen:
            raise HarnessError(f"{where}: id 중복 {scenario_id!r}")
        seen.add(scenario_id)
        tool = item["tool"]
        if tool not in READ_ONLY_TOOLS:
            raise HarnessError(f"{where}: 고정 9개 tool 외 이름 {tool!r}")
        arguments = item["arguments"]
        if not isinstance(arguments, Mapping):
            raise HarnessError(f"{where}: arguments 는 object 여야 한다")
        expected = _parse_expected(item["expected"], where=f"{where}.expected")
        scenarios.append(
            Scenario(id=scenario_id, tool=tool, arguments=arguments, expected=expected)
        )
    return default_repeat, scenarios


# --- placeholder 바인더(§5.1) --------------------------------------------

_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def bind_placeholders(value: Any, handles: Mapping[str, str]) -> Any:
    """문자열 값의 `${name}` 를 setup handle map 으로 치환한다(재귀).

    Raises:
        HarnessError: handle map 에 없는 placeholder 를 만나면(§7.3 불변식 5).
    """
    if isinstance(value, str):

        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in handles:
                raise HarnessError(f"미정의 placeholder: ${{{name}}}")
            return str(handles[name])

        return _PLACEHOLDER.sub(_sub, value)
    if isinstance(value, Mapping):
        return {k: bind_placeholders(v, handles) for k, v in value.items()}
    if isinstance(value, list):
        return [bind_placeholders(v, handles) for v in value]
    return value


# --- 관측 결과와 분류(§4) -----------------------------------------------


@dataclass(frozen=True)
class Observed:
    """한 번의 call_tool 관측 결과.

    structured_content: 예외 없이 반환된 `ToolResult.structured_content`(최상위
        `{"result": ...}`). 예외가 났으면 None.
    exception: 호출이 raise 한 예외(없으면 None).
    elapsed_s: call_tool 직전부터 반환/예외 직후까지의 perf_counter 벽시계.
    """

    structured_content: Mapping[str, Any] | None
    exception: BaseException | None
    elapsed_s: float


@dataclass(frozen=True)
class Verdict:
    """시나리오 한 번 실행의 최종 판정."""

    scenario_id: str
    repeat: int
    tool: str
    elapsed_ms: float
    bucket: str  # "success" | "error" | "timeout"
    expected_rejection: bool
    expected: str
    observed: str
    detail: str


def is_error_payload(result: Any) -> bool:
    """`payload.get("error") is True` 로만 ErrorPayload 판정(§4.2: key 존재만으로 금지)."""
    return isinstance(result, Mapping) and result.get("error") is True


def validate_structured_content(
    structured_content: Mapping[str, Any] | None, output_schema: Mapping[str, Any] | None
) -> str | None:
    """최상위 `structured_content` 를 tool 의 output_schema 로 검증한다.

    내부 payload 만이 아니라 `{"result": ...}` 래퍼까지 검증한다(§4.2).

    Returns:
        오류 사유 문자열, 검증 통과 시 None.
    """
    if output_schema is None:
        return "output_schema 없음"
    if structured_content is None:
        return "structured_content 가 None"
    if "result" not in structured_content:
        return "structured_content 에 'result' 키 없음"
    try:
        jsonschema.Draft202012Validator.check_schema(output_schema)
    except jsonschema.SchemaError as exc:
        return f"output_schema 자체 오류: {exc.message}"
    validator = jsonschema.Draft202012Validator(output_schema)
    errors = sorted(validator.iter_errors(structured_content), key=str)
    if errors:
        return f"schema 불일치: {errors[0].message}"
    return None


def _resolve_path(payload: Any, path: str) -> tuple[bool, Any]:
    """dot-path 로 payload 하위를 찾는다. 빈 path 는 payload 전체.

    list 는 정수 segment 로 인덱싱한다. 찾지 못하면 (False, None).
    """
    if not path:
        return True, payload
    node = payload
    for seg in path.split("."):
        if isinstance(node, Mapping):
            if seg not in node:
                return False, None
            node = node[seg]
        elif isinstance(node, (list, tuple)):
            if not seg.lstrip("-").isdigit():
                return False, None
            idx = int(seg)
            if idx >= len(node) or idx < -len(node):
                return False, None
            node = node[idx]
        else:
            return False, None
    return True, node


def _is_subset(container: Mapping[str, Any], sub: Mapping[str, Any]) -> bool:
    """container 가 sub 의 모든 key/value 를 그대로 포함하는지(부분 일치)."""
    return all(k in container and container[k] == v for k, v in sub.items())


def check_assertion(result: Any, assertion: Mapping[str, Any]) -> tuple[bool, str]:
    """assertion DSL 한 건을 payload 에 적용한다.

    Returns:
        (통과 여부, 실패 사유). 통과 시 사유는 빈 문자열.
    """
    op = assertion["op"]
    path = assertion.get("path", "")
    found, node = _resolve_path(result, path)
    if op == "path_exists":
        return found, "" if found else f"path {path!r} 없음"
    if not found:
        return False, f"path {path!r} 없음 (op={op})"
    if op == "equals":
        value = assertion["value"]
        return node == value, f"{node!r} != {value!r}"
    if op == "length_gte":
        threshold = assertion["value"]
        try:
            length = len(node)
        except TypeError:
            return False, f"path {path!r} 값에 length 없음"
        return length >= threshold, f"len {length} < {threshold}"
    if op == "contains":
        value = assertion["value"]
        if isinstance(node, str):
            return (value in node), f"{value!r} 가 문자열에 없음"
        if isinstance(node, (list, tuple)):
            if isinstance(value, Mapping):
                ok = any(isinstance(el, Mapping) and _is_subset(el, value) for el in node)
            else:
                ok = value in node
            return ok, f"{value!r} 가 list 에 없음"
        if isinstance(node, Mapping) and isinstance(value, Mapping):
            return _is_subset(node, value), f"{value!r} 가 dict 의 부분집합이 아님"
        return False, f"contains 를 {type(node).__name__} 에 적용할 수 없음"
    raise HarnessError(f"알 수 없는 assertion op: {op!r}")  # 로더가 이미 막지만 방어적


def classify(
    scenario: Scenario,
    observed: Observed,
    output_schema: Mapping[str, Any] | None,
    repeat: int = 0,
) -> Verdict:
    """관측 결과를 §4.3 우선순위로 정확히 하나의 bucket 에 넣는다.

    1. 경과시간 >= timeout 임계 → timeout(늦은 유효 응답도 timeout 우선).
    2. 임계 안에서 예외 / 스키마 불일치 / 기대 outcome·code·assertion 불일치 → error.
    3. 그 외 → success. expected error 를 code 까지 맞춰 재현하면 success + expected rejection.
    """
    elapsed_ms = observed.elapsed_s * 1000.0
    threshold = timeout_for(scenario.tool)
    exp = scenario.expected
    expected_desc = f"error:{exp.code}" if exp.outcome == "error" else "success"

    def _mk(
        bucket: str, observed_desc: str, detail: str, *, expected_rejection: bool = False
    ) -> Verdict:
        return Verdict(
            scenario_id=scenario.id,
            repeat=repeat,
            tool=scenario.tool,
            elapsed_ms=elapsed_ms,
            bucket=bucket,
            expected_rejection=expected_rejection,
            expected=expected_desc,
            observed=observed_desc,
            detail=detail,
        )

    if observed.elapsed_s >= threshold:
        return _mk(
            "timeout", "over_deadline", f"elapsed {elapsed_ms:.1f}ms >= {threshold * 1000:.0f}ms"
        )

    if observed.exception is not None:
        exc = observed.exception
        return _mk("error", "exception", f"{type(exc).__name__}: {exc}")

    schema_error = validate_structured_content(observed.structured_content, output_schema)
    if schema_error is not None:
        return _mk("error", "payload", schema_error)

    assert observed.structured_content is not None  # validate 가 이미 보장
    payload = observed.structured_content["result"]
    payload_is_error = is_error_payload(payload)

    if exp.outcome == "error":
        if not payload_is_error:
            return _mk("error", "payload", "error 를 기대했으나 정상 payload 반환")
        actual_code = payload.get("code") if isinstance(payload, Mapping) else None
        if actual_code != exp.code:
            return _mk(
                "error", "payload", f"error code 불일치: 기대 {exp.code!r}, 관측 {actual_code!r}"
            )
        return _mk("success", "payload", "expected rejection", expected_rejection=True)

    # expected success
    if payload_is_error:
        actual_code = payload.get("code") if isinstance(payload, Mapping) else None
        return _mk("error", "payload", f"success 를 기대했으나 error payload(code={actual_code!r})")
    for assertion in exp.assertions:
        ok, why = check_assertion(payload, assertion)
        if not ok:
            return _mk("error", "payload", f"assertion 실패({assertion.get('op')}): {why}")
    return _mk("success", "payload", "")


# --- 집계·게이트(§4.5) -------------------------------------------------


@dataclass
class Aggregate:
    """tool 105회 실행의 집계."""

    n: int
    success: int
    error: int
    timeout: int
    expected_rejection: int
    per_tool: dict[str, dict[str, int]] = field(default_factory=dict)


def aggregate(verdicts: Iterable[Verdict]) -> Aggregate:
    """Verdict 목록을 bucket/도구별로 집계한다."""
    agg = Aggregate(n=0, success=0, error=0, timeout=0, expected_rejection=0)
    for verdict in verdicts:
        agg.n += 1
        setattr(agg, verdict.bucket, getattr(agg, verdict.bucket) + 1)
        if verdict.expected_rejection:
            agg.expected_rejection += 1
        tool_row = agg.per_tool.setdefault(
            verdict.tool,
            {"n": 0, "success": 0, "expected_rejection": 0, "error": 0, "timeout": 0},
        )
        tool_row["n"] += 1
        tool_row[verdict.bucket] += 1
        if verdict.expected_rejection:
            tool_row["expected_rejection"] += 1
    return agg


def gate(success: int, error: int, timeout: int) -> tuple[bool, list[tuple[str, str, str, str]]]:
    """정수 카운트의 교차곱으로 두 목표치를 판정한다(반올림 문자열 비교 금지, §4.5).

        Tool Success Rate      >= 0.99   <=>  success * 100 >= 99 * N
        MCP Error / Timeout Rate < 0.01  <=>  (error + timeout) * 100 < N

    Returns:
        (전체 PASS 여부, [(지표명, 측정값, 목표치, 판정), ...]).
    """
    n = success + error + timeout
    fails = error + timeout
    success_ok = n > 0 and success * 100 >= 99 * n
    fail_ok = n > 0 and fails * 100 < n

    def _pct(num: int) -> str:
        return f"{num}/{n} ({(num / n * 100) if n else 0:.2f}%)"

    rows = [
        ("Tool Success Rate", _pct(success), ">= 99%", "PASS" if success_ok else "FAIL"),
        ("MCP Error / Timeout Rate", _pct(fails), "< 1%", "PASS" if fail_ok else "FAIL"),
        ("- Error Rate", _pct(error), "분해", "INFO"),
        ("- Timeout Rate", _pct(timeout), "분해", "INFO"),
    ]
    return (success_ok and fail_ok), rows


def sufficient_sample(total_calls: int) -> bool:
    """PASS/FAIL 을 낼 수 있는 표본(100회 이상)인지."""
    return total_calls >= MIN_SAMPLE


# --- 보조 resource 게이트(§3.3) --------------------------------------


@dataclass(frozen=True)
class ResourceCheck:
    """get_raw_document resource 게이트 한 건의 결과(분모 밖).

    tool 채점과 같은 §4.3 우선순위를 적용한다 — bucket 은 "success" | "error" |
    "timeout" 중 정확히 하나이며, elapsed 가 RESOURCE_TIMEOUT_S 이상이면 결과
    내용과 무관하게 timeout 이 우선한다.
    """

    name: str
    bucket: str
    detail: str

    @property
    def ok(self) -> bool:
        """conformance 통과(= bucket 이 success)인지."""
        return self.bucket == "success"


def classify_resource(
    *,
    elapsed_s: float,
    exception: BaseException | None,
    content: str | None,
    expected_content: str | None,
    expect_missing: bool,
) -> tuple[str, str]:
    """resource read 한 건을 §4.3 우선순위로 (bucket, detail) 로 분류한다.

    elapsed 가 RESOURCE_TIMEOUT_S 이상이면 늦게 도착한 ResourceError·정상 응답과
    무관하게 timeout 으로 판정한다(tool bucket 규칙과 동일).
    """
    if elapsed_s >= RESOURCE_TIMEOUT_S:
        return (
            "timeout",
            f"over_deadline {elapsed_s * 1000:.0f}ms >= {RESOURCE_TIMEOUT_S * 1000:.0f}ms",
        )
    if expect_missing:
        if exception is None:
            return "error", "ResourceError 를 기대했으나 정상 반환"
        name = type(exception).__name__
        if "ResourceError" in name or "NotFound" in name:
            return "success", ""
        return "error", f"예상 밖 예외 {name}: {exception}"
    if exception is not None:
        return "error", f"{type(exception).__name__}: {exception}"
    if content == expected_content:
        return "success", ""
    return "error", "원문 불일치"


def resource_gate(checks: Iterable[ResourceCheck]) -> bool:
    """모든 resource conformance 체크가 통과했는지. tool 분모에는 관여하지 않는다."""
    checks = list(checks)
    return bool(checks) and all(c.ok for c in checks)


# --- 최소 결정론 시드(§6) --------------------------------------------


def make_source_builders(seed: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    """seed 의 Drive/Notion 리터럴을 적재한 FakeDocumentSource 페이크와 그 builder 를 만든다.

    실제 Google/Notion builder·네트워크 fetch·환경 자격증명을 쓰지 않는다(§6.3).
    builder 는 folder_id/database_id 값과 무관하게 항상 같은 페이크를 돌려준다.

    Returns:
        (drive_builder, notion_builder, drive_fake, notion_fake).
    """
    from app.models.document_meta import SOURCE_DRIVE, SOURCE_NOTION
    from tests.fixtures.document_sources import FakeDocumentSource

    drive_fake = FakeDocumentSource(SOURCE_DRIVE)
    for doc in seed["drive"]["documents"]:
        drive_fake.put(doc["external_id"], doc["title"], doc["body"], url=doc.get("url"))
    notion_fake = FakeDocumentSource(SOURCE_NOTION)
    for doc in seed["notion"]["documents"]:
        notion_fake.put(doc["external_id"], doc["title"], doc["body"], url=doc.get("url"))

    return (
        (lambda _folder_id: drive_fake),
        (lambda _notion_id, _kind: notion_fake),
        drive_fake,
        notion_fake,
    )


def _load_seed(path: Path) -> tuple[Mapping[str, Any], str]:
    """seed.json 을 읽고 (dict, sha256-hex) 를 돌려준다."""
    raw_bytes = path.read_bytes()
    seed = json.loads(raw_bytes)
    if seed.get("schema_version") != 1:
        raise HarnessError(
            f"seed: schema_version 은 1 이어야 한다 (got {seed.get('schema_version')!r})"
        )
    return seed, hashlib.sha256(raw_bytes).hexdigest()


# --- setup + 채점 루프(§6.3, §7) ------------------------------------


async def _run_eval(args: argparse.Namespace) -> int:
    """임시 DB 를 열어 시드를 심고 21개 시나리오를 채점한 뒤 종료 코드를 돌려준다."""
    # rrf_eval 하네스의 임시 DB helper 를 그대로 재사용한다(§7.2).
    sys.path.insert(0, str(_DIR.parent / "rrf_eval"))
    from compare_strategies import _drop_temp_db, _make_temp_db  # type: ignore[import-not-found]

    from app.composition import AppState
    from app.core.db import create_db_engine
    from app.mcp.server import create_mcp_server
    from app.models import EMBEDDING_DIM, create_all
    from app.services.indexer.embedding_provider import HashEmbeddingProvider
    from app.services.ingestor.openapi_fetcher import InMemoryFetcher

    sys.path.insert(0, str(_DIR.parent))
    from samples import openapi_3_json  # type: ignore[import-not-found]

    scenarios_path = Path(args.scenarios) if args.scenarios else _DIR / "scenarios.json"
    seed_path = Path(args.seed) if args.seed else _DIR / "seed.json"

    default_repeat, scenarios = load_scenarios(json.loads(scenarios_path.read_text()))
    repeat = args.repeat if args.repeat is not None else default_repeat
    if repeat <= 0:
        raise HarnessError(f"--repeat 은 양의 정수여야 한다 (got {repeat})")
    seed, seed_sha = _load_seed(seed_path)

    drive_builder, notion_builder, _drive_fake, _notion_fake = make_source_builders(seed)

    admin_url, test_url = _make_temp_db()
    dbname = test_url.rsplit("/", 1)[1]
    try:
        engine = create_db_engine(test_url)
        create_all(engine)
        state = AppState.from_engine(
            engine=engine,
            fetcher=InMemoryFetcher(),
            embedding_provider=HashEmbeddingProvider(dim=EMBEDDING_DIM),
            vector_fallback_enabled=True,
            drive_source_builder=drive_builder,
            notion_source_builder=notion_builder,
        )
        mcp = create_mcp_server(state)
        is_semantic = state.embedding_provider.is_semantic
        if is_semantic:
            raise HarnessError(
                "embedding provider 가 semantic 이다 — HashEmbeddingProvider 여야 한다"
            )

        handles = await _setup_seed(mcp, seed, openapi_3_json())
        output_schemas = await _preflight(mcp, scenarios, handles)

        verdicts: list[Verdict] = []
        for repeat_idx in range(repeat):
            for scenario in scenarios:
                bound_args = bind_placeholders(scenario.arguments, handles)
                observed = await _call_once(mcp, scenario.tool, bound_args)
                verdicts.append(
                    classify(scenario, observed, output_schemas[scenario.tool], repeat=repeat_idx)
                )

        resource_checks = await _resource_gate_checks(
            mcp, handles["openapi_document_id"], openapi_3_json()
        )
    finally:
        _drop_temp_db(admin_url, dbname)

    agg = aggregate(verdicts)
    enough = sufficient_sample(agg.n)
    overall_pass, gate_rows = gate(agg.success, agg.error, agg.timeout)
    resource_ok = resource_gate(resource_checks)

    meta = {
        "commit": _git_commit(),
        "seed_sha256": seed_sha,
        "is_semantic": is_semantic,
        "scenarios": len(scenarios),
        "repeat": repeat,
        "measured_calls": agg.n,
    }
    print(_format_report(meta, agg, gate_rows, verdicts, resource_checks, enough))

    if not enough:
        return 2
    if not overall_pass or not resource_ok:
        return 1
    return 0


async def _call_once(mcp: Any, tool: str, arguments: Mapping[str, Any]) -> Observed:
    """call_tool 을 한 번 실행하고 벽시계·예외·구조화 결과를 관측한다."""
    start = time.perf_counter()
    try:
        result = await mcp.call_tool(tool, arguments=dict(arguments))
        elapsed = time.perf_counter() - start
        return Observed(
            structured_content=result.structured_content, exception=None, elapsed_s=elapsed
        )
    except Exception as exc:  # noqa: BLE001 — 관측이 목적. 분류는 classify 가 한다.
        elapsed = time.perf_counter() - start
        return Observed(structured_content=None, exception=exc, elapsed_s=elapsed)


async def _setup_seed(mcp: Any, seed: Mapping[str, Any], openapi_doc: str) -> dict[str, str]:
    """Petstore 등록 + Drive/Notion 매핑 등록 + 본문 색인. handle map 을 돌려준다.

    setup 용 mutation 은 분모·latency 에 넣지 않는다(§6.3).
    """
    openapi_project = seed["openapi_project"]
    collab_project = seed["collab_project"]

    reg = await mcp.call_tool(
        "register_document",
        arguments={"project": openapi_project, "raw_document": openapi_doc, "doc_type": "openapi"},
    )
    reg_payload = reg.structured_content["result"]
    if is_error_payload(reg_payload):
        raise HarnessError(f"setup: Petstore 등록 실패 {reg_payload}")
    document_id = reg_payload["document_id"]

    search = await mcp.call_tool(
        "search_endpoints",
        arguments={"query": "find pet by id", "project": openapi_project, "top_k": 10},
    )
    search_payload = search.structured_content["result"]
    if is_error_payload(search_payload):
        raise HarnessError(f"setup: seed endpoint 조회 실패 {search_payload}")
    endpoint_id = next(
        (
            item["endpoint_id"]
            for item in search_payload["items"]
            if item["method"] == "GET" and item["path"] == "/pet/{petId}"
        ),
        None,
    )
    if endpoint_id is None:
        raise HarnessError("setup: seed 에서 GET /pet/{petId} 엔드포인트를 찾지 못함")

    drive_reg = await mcp.call_tool(
        "register_drive_source",
        arguments={"project": collab_project, "folder_id": seed["drive"]["folder_id"]},
    )
    drive_reg_payload = drive_reg.structured_content["result"]
    if is_error_payload(drive_reg_payload):
        raise HarnessError(f"setup: Drive 소스 등록 실패 {drive_reg_payload}")
    notion_reg = await mcp.call_tool(
        "register_notion_source",
        arguments={"project": collab_project, "database_id": seed["notion"]["database_id"]},
    )
    notion_reg_payload = notion_reg.structured_content["result"]
    if is_error_payload(notion_reg_payload):
        raise HarnessError(f"setup: Notion 소스 등록 실패 {notion_reg_payload}")
    refresh = await mcp.call_tool(
        "refresh_index", arguments={"project": collab_project, "index_bodies": True}
    )
    refresh_payload = refresh.structured_content["result"]
    if is_error_payload(refresh_payload):
        raise HarnessError(f"setup: 협업 문서 색인 실패 {refresh_payload}")

    return {"openapi_document_id": document_id, "pet_endpoint_id": endpoint_id}


async def _preflight(
    mcp: Any, scenarios: list[Scenario], handles: Mapping[str, str]
) -> dict[str, Mapping[str, Any]]:
    """서버 tool 목록·output_schema·placeholder 를 검사하고 tool→output_schema 맵을 만든다(§7.3)."""
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    missing = READ_ONLY_TOOLS - set(tools)
    if missing:
        raise HarnessError(f"preflight: 서버에 없는 tool {sorted(missing)}")

    output_schemas: dict[str, Mapping[str, Any]] = {}
    for name in READ_ONLY_TOOLS:
        schema = tools[name].output_schema
        if schema is None:
            raise HarnessError(f"preflight: {name} 의 output_schema 가 없음")
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise HarnessError(
                f"preflight: {name} 의 output_schema 가 유효한 JSON Schema 가 아님: {exc.message}"
            ) from exc
        output_schemas[name] = schema

    for scenario in scenarios:
        bind_placeholders(scenario.arguments, handles)  # 미정의 placeholder 면 여기서 HarnessError
    return output_schemas


async def _resource_gate_checks(
    mcp: Any, document_id: str, openapi_doc: str
) -> list[ResourceCheck]:
    """정상 document URI 1건 + 미존재 URI 1건을 read_resource 로 실행한다(§3.3).

    각 호출을 §4.3 우선순위(timeout 우선)로 bucket 분류한다. 미존재 케이스가
    2.0s 를 넘겨서야 ResourceError 를 던지면 conformance PASS 가 아니라
    timeout FAIL 이다.
    """
    checks: list[ResourceCheck] = []
    cases = (
        ("get_raw_document.hit", f"document://{document_id}/raw", False),
        ("get_raw_document.missing", "document://mcp-eval-missing-doc/raw", True),
    )
    for name, uri, expect_missing in cases:
        exc: BaseException | None = None
        content: str | None = None
        start = time.perf_counter()
        try:
            result = await mcp.read_resource(uri)
            content = result.contents[0].content if result.contents else None
        except Exception as caught:  # noqa: BLE001 — 관측이 목적. 분류는 classify_resource.
            exc = caught
        elapsed = time.perf_counter() - start
        bucket, detail = classify_resource(
            elapsed_s=elapsed,
            exception=exc,
            content=content,
            expected_content=openapi_doc,
            expect_missing=expect_missing,
        )
        checks.append(ResourceCheck(name, bucket, detail))

    return checks


# --- 출력(§7.4) -----------------------------------------------------


def _format_report(
    meta: Mapping[str, Any],
    agg: Aggregate,
    gate_rows: list[tuple[str, str, str, str]],
    verdicts: list[Verdict],
    resource_checks: list[ResourceCheck],
    enough: bool,
) -> str:
    """stdout 을 그대로 docs/eval-results 기록에 붙일 수 있는 Markdown 으로 만든다."""
    from datetime import date

    lines: list[str] = []
    lines.append(f"# MCP 계층 평가 {date.today().isoformat()}")
    lines.append("")
    lines.append(f"- commit SHA: {meta['commit']}")
    lines.append("- layer: in-process FastMCP.call_tool (no stdio)")
    lines.append(f"- seed_sha256: {meta['seed_sha256']}")
    lines.append(f"- is_semantic: {str(meta['is_semantic']).lower()}")
    lines.append(f"- scenarios: {meta['scenarios']}")
    lines.append(f"- repeat: {meta['repeat']}")
    lines.append(f"- measured calls: {meta['measured_calls']}")
    lines.append(f"- timeout: search={SEARCH_TIMEOUT_S}s, read={READ_TIMEOUT_S}s")
    if not enough:
        lines.append(f"- 판정: INSUFFICIENT (측정 {agg.n}회 < {MIN_SAMPLE}회, 종료 코드 2)")
    lines.append("")

    lines.append("## 도구별 결과")
    lines.append("| tool | n | success | expected rejection | error | timeout |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for tool in sorted(agg.per_tool):
        row = agg.per_tool[tool]
        lines.append(
            f"| {tool} | {row['n']} | {row['success']} | {row['expected_rejection']} "
            f"| {row['error']} | {row['timeout']} |"
        )
    lines.append("")

    lines.append("## MCP 계층")
    lines.append("| 지표 | 측정값 | 목표치 | 판정 |")
    lines.append("|---|---|---|---|")
    for name, measured, target, verdict in gate_rows:
        lines.append(f"| {name} | {measured} | {target} | {verdict} |")
    lines.append("")

    lines.append("## 실패 상세")
    lines.append("| scenario | repeat | tool | elapsed_ms | class | expected | observed | detail |")
    lines.append("|---|---:|---|---:|---|---|---|---|")
    failures = [v for v in verdicts if v.bucket != "success"]
    if not failures:
        lines.append("| (없음) | | | | | | | |")
    else:
        for v in failures:
            lines.append(
                f"| {v.scenario_id} | {v.repeat} | {v.tool} | {v.elapsed_ms:.1f} | {v.bucket} "
                f"| {v.expected} | {v.observed} | {v.detail} |"
            )
    lines.append("")

    lines.append("## MCP resource conformance (분모 밖)")
    lines.append("| resource | n | success | error | timeout | 판정 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for check in resource_checks:
        lines.append(
            f"| {check.name} | 1 "
            f"| {1 if check.bucket == 'success' else 0} "
            f"| {1 if check.bucket == 'error' else 0} "
            f"| {1 if check.bucket == 'timeout' else 0} "
            f"| {'PASS' if check.ok else 'FAIL'} |"
        )
    total = len(resource_checks)
    passed = sum(1 for c in resource_checks if c.ok)
    lines.append(
        f"| (합계) | {total} | {passed} "
        f"| {sum(1 for c in resource_checks if c.bucket == 'error')} "
        f"| {sum(1 for c in resource_checks if c.bucket == 'timeout')} "
        f"| {'PASS' if resource_gate(resource_checks) else 'FAIL'} |"
    )
    failed_detail = [f"{c.name}: {c.detail}" for c in resource_checks if not c.ok and c.detail]
    if failed_detail:
        lines.append("")
        lines.append("실패 사유: " + "; ".join(failed_detail))
    lines.append("")

    return "\n".join(lines)


def _git_commit() -> str:
    """현재 커밋 SHA(짧은). git 이 없으면 'unknown'."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=_DIR,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=None,
        help="시나리오별 반복 수(기본 scenarios.json.default_repeat).",
    )
    parser.add_argument("--scenarios", default=None, help="scenarios.json 경로 override.")
    parser.add_argument("--seed", default=None, help="seed.json 경로 override.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 종료 코드 계약은 §7.5."""
    args = _parse_args(argv)
    try:
        import anyio

        return anyio.run(_run_eval, args)
    except HarnessError as exc:
        print(f"SETUP_ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
