"""OpenAPI/Swagger 스키마 dict 정규화 헬퍼.

`$ref` 추출, dict 캐스팅, swagger2 `#/definitions/` 참조를 OpenAPI 3
`#/components/schemas/` 형식으로 치환하는 순수 함수들을 모은다.
"""

from __future__ import annotations

from typing import Any


def _ensure_dict(value: Any) -> dict[str, Any]:
    """값이 dict 면 그대로, 아니면 빈 dict 를 반환한다."""
    if isinstance(value, dict):
        return value
    return {}


def _extract_ref(schema: dict[str, Any]) -> str | None:
    """스키마 dict 의 $ref 문자열을 반환하고, 없으면 None 을 반환한다."""
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref:
        return ref
    return None


def _rewrite_inline_ref(schema: dict[str, Any]) -> None:
    """스키마 dict 안의 $ref 가 swagger2 형식이면 OpenAPI 3 형식으로 치환한다."""
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/definitions/"):
        schema["$ref"] = ref.replace("#/definitions/", "#/components/schemas/")
