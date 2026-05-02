"""요청 예시 코드 생성.

저장된 정규화 스키마만 보고 결정적으로 생성한다. 외부 호출 없음.
지원 포맷: curl / fetch / axios / python(requests).
"""

from __future__ import annotations

import json
from typing import Any

from app.core.errors import EndpointNotFoundError, ValidationError
from app.models.openapi import (
    ApiEndpoint,
    ApiParameter,
    ApiRequestBody,
)
from app.repositories.endpoint_repository import EndpointRepository

_SUPPORTED_FORMATS = {"curl", "fetch", "axios", "python"}


class RequestExampleService:
    """저장된 엔드포인트 정보로부터 호출 예시 코드를 생성한다."""

    def __init__(self, endpoint_repo: EndpointRepository) -> None:
        """엔드포인트 저장소 의존성을 보관한다."""
        self._endpoint_repo = endpoint_repo

    def generate(self, endpoint_id: str, fmt: str) -> dict[str, Any]:
        """지정 포맷(curl/fetch/axios/python)으로 호출 예시 코드 페이로드를 만들어 반환한다."""
        if fmt not in _SUPPORTED_FORMATS:
            raise ValidationError(f"unsupported format: {fmt}")
        endpoint = self._endpoint_repo.get(endpoint_id)
        if endpoint is None:
            raise EndpointNotFoundError(endpoint_id)

        path = _resolve_path(endpoint)
        query_pairs = _required_query_pairs(endpoint)
        header_pairs = _required_header_pairs(endpoint)
        body_sample = _body_sample(endpoint.request_body) if endpoint.request_body else None

        base_url = "https://api.example.com"
        full_url = base_url + path
        if query_pairs:
            full_url += "?" + "&".join(f"{k}={v}" for k, v in query_pairs)

        if fmt == "curl":
            code = _render_curl(endpoint.method, full_url, header_pairs, body_sample)
        elif fmt == "fetch":
            code = _render_fetch(endpoint.method, full_url, header_pairs, body_sample)
        elif fmt == "axios":
            code = _render_axios(endpoint.method, full_url, header_pairs, body_sample)
        else:
            code = _render_python(endpoint.method, full_url, header_pairs, body_sample)
        return {"format": fmt, "code": code, "notes": None}


def _resolve_path(endpoint: ApiEndpoint) -> str:
    """경로 파라미터를 샘플 값으로 치환한 URL 경로를 반환한다."""
    path = endpoint.path
    for param in endpoint.parameters:
        if param.location != "path":
            continue
        sample = _sample_from_schema(param.schema, param.name)
        path = path.replace("{" + param.name + "}", str(sample))
    return path


def _required_query_pairs(endpoint: ApiEndpoint) -> list[tuple[str, str]]:
    """필수 쿼리 파라미터의 (이름, 샘플값) 쌍을 모아 반환한다."""
    pairs: list[tuple[str, str]] = []
    for param in endpoint.parameters:
        if param.location != "query" or not param.required:
            continue
        sample = _sample_from_schema(param.schema, param.name)
        pairs.append((param.name, str(sample)))
    return pairs


def _required_header_pairs(endpoint: ApiEndpoint) -> list[tuple[str, str]]:
    """필수 헤더 파라미터의 (이름, 샘플값) 쌍을 모아 반환한다."""
    pairs: list[tuple[str, str]] = []
    for param in endpoint.parameters:
        if param.location != "header" or not param.required:
            continue
        sample = _sample_from_schema(param.schema, param.name)
        pairs.append((param.name, str(sample)))
    return pairs


def _body_sample(body: ApiRequestBody) -> Any | None:
    """요청 바디의 예시값(있으면) 또는 스키마 기반 샘플을 만들어 반환한다."""
    if body.example is not None:
        return body.example
    if body.schema:
        return _sample_from_schema(body.schema, "body")
    return None


def _sample_from_schema(schema: dict[str, Any], name: str) -> Any:
    """JSON Schema 의 type/example 정보로부터 결정적 샘플 값을 만들어 반환한다."""
    if not isinstance(schema, dict):
        return "example"
    if "example" in schema and schema["example"] is not None:
        return schema["example"]
    schema_type = schema.get("type")
    if schema_type == "integer" or schema_type == "number":
        return 0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        return []
    if schema_type == "object":
        props = schema.get("properties") or {}
        if isinstance(props, dict):
            return {
                str(k): _sample_from_schema(v if isinstance(v, dict) else {}, str(k))
                for k, v in list(props.items())[:5]
            }
        return {}
    if schema_type == "string":
        return "string"
    return f"<{name}>"


def _render_curl(
    method: str, url: str, headers: list[tuple[str, str]], body: Any | None
) -> str:
    """curl 명령 형식의 예시 코드를 문자열로 렌더링한다."""
    parts = [f"curl -X {method.upper()} '{url}'"]
    for k, v in headers:
        parts.append(f"  -H '{k}: {v}'")
    if body is not None:
        parts.append("  -H 'Content-Type: application/json'")
        parts.append("  -d '" + json.dumps(body) + "'")
    return " \\\n".join(parts)


def _render_fetch(
    method: str, url: str, headers: list[tuple[str, str]], body: Any | None
) -> str:
    """JS fetch() 호출 형식의 예시 코드를 렌더링한다."""
    options: dict[str, Any] = {"method": method.upper()}
    hdr_obj: dict[str, str] = {k: v for k, v in headers}
    if body is not None:
        hdr_obj["Content-Type"] = "application/json"
        options["body"] = json.dumps(body)
    if hdr_obj:
        options["headers"] = hdr_obj
    rendered_options = json.dumps(options, indent=2)
    return f"fetch('{url}', {rendered_options})"


def _render_axios(
    method: str, url: str, headers: list[tuple[str, str]], body: Any | None
) -> str:
    """axios() 호출 형식의 예시 코드를 렌더링한다."""
    config: dict[str, Any] = {"method": method.lower(), "url": url}
    hdr_obj: dict[str, str] = {k: v for k, v in headers}
    if hdr_obj:
        config["headers"] = hdr_obj
    if body is not None:
        config["data"] = body
    return "axios(" + json.dumps(config, indent=2) + ")"


def _render_python(
    method: str, url: str, headers: list[tuple[str, str]], body: Any | None
) -> str:
    """Python requests 호출 형식의 예시 코드를 렌더링한다."""
    lines = [
        "import requests",
        "",
        f"response = requests.{method.lower()}(",
        f"    '{url}',",
    ]
    hdr_obj = {k: v for k, v in headers}
    if hdr_obj:
        lines.append(f"    headers={json.dumps(hdr_obj)},")
    if body is not None:
        lines.append(f"    json={json.dumps(body)},")
    lines.append(")")
    lines.append("print(response.status_code, response.text)")
    return "\n".join(lines)
