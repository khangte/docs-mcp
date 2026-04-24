"""$ref 해소 헬퍼.

`#/components/schemas/<Name>` 만 지원한다 (Swagger 2.0 의 `#/definitions/<Name>` 는 파서에서 치환).
"""

from __future__ import annotations

from typing import Any

from src.services.parser.openapi_parser import ParsedDocument, ParsedSchema


_COMPONENT_PREFIX = "#/components/schemas/"


def resolve_ref(ref: str | None, schemas: list[ParsedSchema]) -> dict[str, Any] | None:
    """로컬 컴포넌트 ref 를 해소한다. 없으면 None."""
    if not ref or not ref.startswith(_COMPONENT_PREFIX):
        return None
    name = ref[len(_COMPONENT_PREFIX):]
    for s in schemas:
        if s.name == name:
            return s.json_schema
    return None


def schema_name_from_ref(ref: str | None) -> str | None:
    if not ref or not ref.startswith(_COMPONENT_PREFIX):
        return None
    return ref[len(_COMPONENT_PREFIX):]


def collect_referenced_schemas(
    refs: list[str | None], document: ParsedDocument
) -> list[ParsedSchema]:
    """주어진 ref 리스트에서 실제 존재하는 스키마 모아서 반환."""
    names: set[str] = set()
    for ref in refs:
        name = schema_name_from_ref(ref)
        if name:
            names.add(name)
    return [s for s in document.schemas if s.name in names]
