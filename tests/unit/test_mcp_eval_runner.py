"""MCP 계층 평가 하네스(`tests/fixtures/mcp_eval/run_mcp_eval.py`) 순수 로직 단위 테스트.

`docs/architect-review/64_mcp_layer_eval_harness_design.md` §8 "필수 테스트" 목록을
그대로 옮긴 것이다. DB·MCP 서버가 필요한 setup/scoring 루프는 이 파일에서 다루지
않는다(다른 eval 스크립트와 마찬가지로 러너 본체는 수동 재실행 대상). 여기서는
분류기·집계기·게이트·strict 로더·placeholder 바인더·assertion DSL 만 검증한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HARNESS_DIR = Path(__file__).parents[1] / "fixtures" / "mcp_eval"
sys.path.insert(0, str(_HARNESS_DIR))

import run_mcp_eval as rme  # noqa: E402

# result 페이로드를 감싸는 최상위 output_schema 형태(정상 응답을 통과시키는 느슨한 스키마).
_LOOSE_SCHEMA = {"type": "object", "properties": {"result": {}}, "required": ["result"]}


def _scenario(
    *,
    scenario_id: str = "s1",
    tool: str = "list_documents",
    arguments: dict | None = None,
    outcome: str = "success",
    code: str | None = None,
    assertions: tuple = (),
) -> "rme.Scenario":
    """테스트용 Scenario 를 간결하게 만든다."""
    expected = rme.Expected(outcome=outcome, code=code, assertions=assertions)
    return rme.Scenario(id=scenario_id, tool=tool, arguments=arguments or {}, expected=expected)


def _observed(
    *,
    result: object = None,
    exception: BaseException | None = None,
    elapsed_s: float = 0.01,
) -> "rme.Observed":
    """result 페이로드를 최상위 {"result": ...} 로 감싼 Observed 를 만든다."""
    structured = None if result is None and exception is not None else {"result": result}
    return rme.Observed(structured_content=structured, exception=exception, elapsed_s=elapsed_s)


# --- §8: expected ErrorPayload 가 success + expected rejection 으로 집계 ---------


def test_expected_error_payload_is_success_with_expected_rejection() -> None:
    scenario = _scenario(tool="resolve_ref", outcome="error", code="validation_error")
    observed = _observed(result={"error": True, "code": "validation_error", "message": "bad"})

    verdict = rme.classify(scenario, observed, _LOOSE_SCHEMA)

    assert verdict.bucket == "success"
    assert verdict.expected_rejection is True


# --- §8: 잘못된 error code / outcome 역전 / assertion 실패가 error 로 집계 --------


def test_wrong_error_code_is_error() -> None:
    scenario = _scenario(tool="resolve_ref", outcome="error", code="schema_ref_not_found")
    observed = _observed(result={"error": True, "code": "validation_error", "message": "x"})

    assert rme.classify(scenario, observed, _LOOSE_SCHEMA).bucket == "error"


def test_expected_error_but_got_success_payload_is_error() -> None:
    scenario = _scenario(outcome="error", code="validation_error")
    observed = _observed(result={"items": []})

    assert rme.classify(scenario, observed, _LOOSE_SCHEMA).bucket == "error"


def test_expected_success_but_got_error_payload_is_error() -> None:
    scenario = _scenario(outcome="success")
    observed = _observed(result={"error": True, "code": "integration_error", "message": "x"})

    assert rme.classify(scenario, observed, _LOOSE_SCHEMA).bucket == "error"


def test_failed_assertion_is_error() -> None:
    scenario = _scenario(
        outcome="success",
        assertions=({"op": "equals", "path": "title", "value": "expected"},),
    )
    observed = _observed(result={"title": "actual"})

    verdict = rme.classify(scenario, observed, _LOOSE_SCHEMA)
    assert verdict.bucket == "error"
    assert "assertion" in verdict.detail


# --- §8: schema-invalid structured payload 가 error 로 집계 ----------------------


def test_schema_invalid_payload_is_error() -> None:
    strict_schema = {
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        },
        "required": ["result"],
    }
    scenario = _scenario(tool="resolve_ref", outcome="success")
    observed = _observed(result={"name": 123})  # name 은 string 이어야 함

    verdict = rme.classify(scenario, observed, strict_schema)
    assert verdict.bucket == "error"
    assert "schema" in verdict.detail.lower()


def test_output_schema_validates_full_structured_content_not_inner_payload() -> None:
    """최상위 {"result": ...} 래퍼까지 검증한다 — 내부 payload 만 검증하면 안 된다(§4.2)."""
    wrapper_schema = {
        "type": "object",
        "properties": {"result": {"type": "array"}},
        "required": ["result"],
    }
    # 내부 payload(빈 list)는 자체로는 유효하지만, result 키가 없으면 실패해야 한다.
    assert rme.validate_structured_content({"items": []}, wrapper_schema) is not None
    assert rme.validate_structured_content({"result": []}, wrapper_schema) is None
    assert rme.validate_structured_content(None, wrapper_schema) is not None
    assert rme.validate_structured_content({"result": []}, None) is not None


# --- §8: 임계와 같은 elapsed 는 timeout, 늦은 정상 payload 도 timeout 우선 --------


def test_elapsed_equal_to_threshold_is_timeout() -> None:
    scenario = _scenario(tool="list_documents")  # read tool -> 2.0s 임계
    observed = _observed(result={"items": []}, elapsed_s=2.0)

    assert rme.classify(scenario, observed, _LOOSE_SCHEMA).bucket == "timeout"


def test_late_valid_payload_is_classified_timeout_first() -> None:
    scenario = _scenario(
        tool="search_endpoints",  # search tool -> 5.0s 임계
        outcome="success",
        assertions=({"op": "path_exists", "path": "items"},),
    )
    observed = _observed(result={"items": [{"path": "/pet/{petId}"}]}, elapsed_s=5.5)

    assert rme.classify(scenario, observed, _LOOSE_SCHEMA).bucket == "timeout"


def test_timeout_threshold_is_tool_class_specific() -> None:
    fast = _observed(result={"items": []}, elapsed_s=3.0)
    assert rme.classify(_scenario(tool="list_tags"), fast, _LOOSE_SCHEMA).bucket == "timeout"
    assert rme.classify(_scenario(tool="search_documents"), fast, _LOOSE_SCHEMA).bucket == "success"


# --- §8: success + error + timeout == N, combined rate == 1 - success rate ------


def test_aggregate_buckets_sum_to_n_and_rates_are_complementary() -> None:
    verdicts = [
        _verdict("success"),
        _verdict("success"),
        _verdict("error"),
        _verdict("timeout"),
    ]
    agg = rme.aggregate(verdicts)

    assert agg.n == 4
    assert agg.success + agg.error + agg.timeout == agg.n
    combined_rate = (agg.error + agg.timeout) / agg.n
    inverse_success_rate = (agg.n - agg.success) / agg.n
    assert combined_rate == inverse_success_rate


def _verdict(bucket: str, *, tool: str = "list_documents", expected_rejection: bool = False):
    """집계 테스트용 Verdict."""
    return rme.Verdict(
        scenario_id="s",
        repeat=0,
        tool=tool,
        elapsed_ms=1.0,
        bucket=bucket,
        expected_rejection=expected_rejection,
        expected="success",
        observed="payload",
        detail="",
    )


# --- §8: 정확히 1% 실패는 strict <1% 게이트 FAIL ------------------------------


def test_exactly_one_percent_failure_fails_strict_gate() -> None:
    overall_pass, _rows = rme.gate(success=99, error=1, timeout=0)
    assert overall_pass is False


def test_all_success_passes_gate() -> None:
    overall_pass, _rows = rme.gate(success=105, error=0, timeout=0)
    assert overall_pass is True


def test_gate_uses_integer_cross_multiplication_not_rounded_strings() -> None:
    # 99.5% 성공: 반올림하면 "100%" 로 보이지만 정수 비교로는 >=99% 를 만족(첫 조건 PASS),
    # 실패율 0.5% 는 strict <1% 도 만족 -> 전체 PASS.
    overall_pass, _rows = rme.gate(success=199, error=1, timeout=0)
    assert overall_pass is True
    # 정확히 1% 는 실패
    assert rme.gate(success=198, error=2, timeout=0)[0] is False


# --- §8: get_raw_document 결과가 tool 분모를 바꾸지 않음 ------------------------


def test_resource_check_does_not_change_tool_denominator() -> None:
    tool_verdicts = [_verdict("success") for _ in range(105)]
    agg = rme.aggregate(tool_verdicts)

    passing_resource = [
        rme.ResourceCheck("raw.hit", "success", ""),
        rme.ResourceCheck("raw.miss", "success", ""),
    ]
    failing_resource = [rme.ResourceCheck("raw.hit", "error", "mismatch")]

    assert agg.n == 105
    # 리소스 게이트는 별도 함수이며 tool 집계 N 에 관여하지 않는다.
    assert rme.resource_gate(passing_resource) is True
    assert rme.resource_gate(failing_resource) is False
    assert rme.aggregate(tool_verdicts).n == 105


# --- §4.3: resource 체크도 timeout 우선(늦은 ResourceError 는 PASS 아님) --------


class _FakeResourceError(Exception):
    """이름에 'ResourceError' 를 포함해 fastmcp 예외를 흉내내는 테스트용 예외."""


def test_resource_missing_within_deadline_is_success() -> None:
    bucket, _ = rme.classify_resource(
        elapsed_s=0.01,
        exception=_FakeResourceError("not found"),
        content=None,
        expected_content="DOC",
        expect_missing=True,
    )
    assert bucket == "success"


def test_resource_missing_but_late_resource_error_is_timeout_not_success() -> None:
    bucket, detail = rme.classify_resource(
        elapsed_s=rme.RESOURCE_TIMEOUT_S,
        exception=_FakeResourceError("not found"),
        content=None,
        expected_content="DOC",
        expect_missing=True,
    )
    assert bucket == "timeout"
    assert "over_deadline" in detail
    # timeout 은 conformance 통과가 아니다.
    assert rme.ResourceCheck("raw.miss", bucket, detail).ok is False
    assert rme.resource_gate([rme.ResourceCheck("raw.miss", bucket, detail)]) is False


def test_resource_hit_but_late_valid_content_is_timeout_not_success() -> None:
    bucket, _ = rme.classify_resource(
        elapsed_s=rme.RESOURCE_TIMEOUT_S + 0.5,
        exception=None,
        content="DOC",
        expected_content="DOC",
        expect_missing=False,
    )
    assert bucket == "timeout"


def test_resource_hit_content_mismatch_within_deadline_is_error() -> None:
    bucket, _ = rme.classify_resource(
        elapsed_s=0.01,
        exception=None,
        content="OTHER",
        expected_content="DOC",
        expect_missing=False,
    )
    assert bucket == "error"


def test_resource_missing_but_got_content_within_deadline_is_error() -> None:
    bucket, _ = rme.classify_resource(
        elapsed_s=0.01,
        exception=None,
        content="DOC",
        expected_content="DOC",
        expect_missing=True,
    )
    assert bucket == "error"


# --- §8: setup 실패·미정의 placeholder·미등록 tool·100 미만 표본이 종료 코드 2 ---


def test_undefined_placeholder_raises_harness_error() -> None:
    with pytest.raises(rme.HarnessError):
        rme.bind_placeholders({"endpoint_id": "${missing_handle}"}, {})


def test_bind_placeholders_substitutes_known_handles_recursively() -> None:
    bound = rme.bind_placeholders(
        {"document_id": "${doc}", "nested": {"ref": "#/x", "id": "${ep}"}},
        {"doc": "doc-123", "ep": "ep-9"},
    )
    assert bound == {"document_id": "doc-123", "nested": {"ref": "#/x", "id": "ep-9"}}


def test_load_scenarios_rejects_unknown_tool() -> None:
    raw = {
        "schema_version": 1,
        "default_repeat": 5,
        "scenarios": [
            {"id": "x", "tool": "frobnicate", "arguments": {}, "expected": {"outcome": "success"}}
        ],
    }
    with pytest.raises(rme.HarnessError):
        rme.load_scenarios(raw)


def test_load_scenarios_rejects_wrong_schema_version() -> None:
    with pytest.raises(rme.HarnessError):
        rme.load_scenarios({"schema_version": 2, "scenarios": []})


def test_load_scenarios_rejects_duplicate_ids() -> None:
    raw = {
        "schema_version": 1,
        "scenarios": [
            {
                "id": "dup",
                "tool": "list_documents",
                "arguments": {},
                "expected": {"outcome": "success"},
            },
            {"id": "dup", "tool": "list_tags", "arguments": {}, "expected": {"outcome": "success"}},
        ],
    }
    with pytest.raises(rme.HarnessError):
        rme.load_scenarios(raw)


def test_load_scenarios_rejects_error_expectation_without_code() -> None:
    raw = {
        "schema_version": 1,
        "scenarios": [
            {"id": "x", "tool": "resolve_ref", "arguments": {}, "expected": {"outcome": "error"}}
        ],
    }
    with pytest.raises(rme.HarnessError):
        rme.load_scenarios(raw)


def test_load_scenarios_rejects_success_expectation_with_code() -> None:
    raw = {
        "schema_version": 1,
        "scenarios": [
            {
                "id": "x",
                "tool": "list_documents",
                "arguments": {},
                "expected": {"outcome": "success", "code": "nope"},
            }
        ],
    }
    with pytest.raises(rme.HarnessError):
        rme.load_scenarios(raw)


def test_load_scenarios_rejects_unknown_assertion_op() -> None:
    raw = {
        "schema_version": 1,
        "scenarios": [
            {
                "id": "x",
                "tool": "list_documents",
                "arguments": {},
                "expected": {
                    "outcome": "success",
                    "assertions": [{"op": "regex_match", "path": "x", "value": "y"}],
                },
            }
        ],
    }
    with pytest.raises(rme.HarnessError):
        rme.load_scenarios(raw)


def test_load_scenarios_rejects_unknown_top_level_key_in_expected() -> None:
    raw = {
        "schema_version": 1,
        "scenarios": [
            {
                "id": "x",
                "tool": "list_documents",
                "arguments": {},
                "expected": {"outcome": "success", "surprise": 1},
            }
        ],
    }
    with pytest.raises(rme.HarnessError):
        rme.load_scenarios(raw)


def test_sufficient_sample_gate_is_100() -> None:
    assert rme.sufficient_sample(100) is True
    assert rme.sufficient_sample(99) is False


# --- §8: runner 가 실제 외부 source builder / HTTP 를 호출하지 않음 -------------


def test_source_builders_are_offline_fakes() -> None:
    from tests.fixtures.document_sources import FakeDocumentSource

    seed = {
        "drive": {
            "folder_id": "eval-drive-folder",
            "documents": [
                {"external_id": "drv-1", "title": "로그인 설계서", "body": "OAuth 흐름", "url": "u"}
            ],
        },
        "notion": {
            "database_id": "eval-notion-db",
            "documents": [
                {"external_id": "ntn-1", "title": "배포 가이드", "body": "롤백 절차", "url": "u"}
            ],
        },
    }
    drive_builder, notion_builder, drive_fake, notion_fake = rme.make_source_builders(seed)

    assert isinstance(drive_builder("any-folder-id"), FakeDocumentSource)
    assert isinstance(notion_builder("any-db-id", "database"), FakeDocumentSource)
    # 시드 문서가 페이크에 적재됐고, fetch 는 네트워크 없이 메모리에서 반환된다.
    assert drive_fake.fetch("drv-1").text == "OAuth 흐름"
    assert notion_fake.fetch("ntn-1").text == "롤백 절차"


# --- assertion DSL 세부 -------------------------------------------------------


def test_check_assertion_contains_partial_dict_match_in_list() -> None:
    result = {
        "items": [{"path": "/pet", "method": "POST"}, {"path": "/pet/{petId}", "method": "GET"}]
    }
    ok, _ = rme.check_assertion(
        result, {"op": "contains", "path": "items", "value": {"path": "/pet/{petId}"}}
    )
    assert ok is True
    ok, _ = rme.check_assertion(
        result, {"op": "contains", "path": "items", "value": {"path": "/nope"}}
    )
    assert ok is False


def test_check_assertion_empty_path_targets_whole_payload() -> None:
    ok, _ = rme.check_assertion([], {"op": "equals", "path": "", "value": []})
    assert ok is True


def test_check_assertion_numeric_path_segment_indexes_list() -> None:
    result = {"items": [{"folder_id": "f-1"}]}
    ok, _ = rme.check_assertion(
        result, {"op": "equals", "path": "items.0.folder_id", "value": "f-1"}
    )
    assert ok is True


def test_check_assertion_length_gte() -> None:
    ok, _ = rme.check_assertion({"tags": [1, 2]}, {"op": "length_gte", "path": "tags", "value": 2})
    assert ok is True
    ok, _ = rme.check_assertion({"tags": [1]}, {"op": "length_gte", "path": "tags", "value": 2})
    assert ok is False


def test_exception_within_deadline_is_error() -> None:
    scenario = _scenario(outcome="success")
    observed = _observed(exception=RuntimeError("boom"), elapsed_s=0.5)

    verdict = rme.classify(scenario, observed, _LOOSE_SCHEMA)
    assert verdict.bucket == "error"
    assert "RuntimeError" in verdict.detail
