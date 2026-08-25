"""스펙 → 메타데이터 입력 payload 조립 + 재생성 판단 해시(LLM 무관 순수 로직).

docs/architect-review/56 §1.3: CLI 생성 경로와 write-back 경로가 같은
`source_hash` 를 계산해야 "스펙이 바뀌면 낡은 것으로 본다" 규칙이 두 경로에서
동일하게 성립한다. LLM 호출·API 키에 의존하지 않으므로 `app/mcp` 에서
import 해도 된다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from app.models import ApiEndpoint

_HTML_TAG_RE = re.compile(r"<[^>]+>")
#: 청크 절단(300자, `chunk_builder.py`)과 다른 예산 — 청크는 색인 비용
#: 문제고 여기는 요약 근거 확보 문제라 더 넓게 준다(55 §2.4).
_PROMPT_DESCRIPTION_MAX_CHARS = 600

#: CLI 프롬프트(`prompt.SYSTEM_PROMPT`) 또는 write-back 지시문
#: (`writeback_service.WRITEBACK_INSTRUCTION`) 중 하나라도 의미가 바뀌면 올린다 —
#: `compute_source_hash` 에 포함돼 기존 메타데이터가 자동으로 낡은 것이 된다(56 §1.3).
METADATA_INSTRUCTION_VERSION = "1"


@dataclass(frozen=True)
class EndpointMetadataInput:
    """메타데이터 생성 1건의 입력(엔드포인트 1건)."""

    method: str
    path: str
    summary: str
    description: str
    operation_id: str
    param_names: list[str] = field(default_factory=list)
    body_field_names: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def build_endpoint_input(endpoint: ApiEndpoint) -> EndpointMetadataInput:
    """ORM 엔드포인트에서 입력 payload 원본 필드를 뽑는다(55 §2.4)."""
    body_field_names: list[str] = []
    if endpoint.request_body is not None:
        body_field_names = sorted((endpoint.request_body.schema or {}).get("properties", {}))
    return EndpointMetadataInput(
        method=endpoint.method,
        path=endpoint.path,
        summary=endpoint.summary,
        description=endpoint.description,
        operation_id=endpoint.operation_id or "",
        param_names=[p.name for p in endpoint.parameters],
        body_field_names=body_field_names,
        tags=endpoint.tags,
    )


def build_payload_json(data: EndpointMetadataInput) -> str:
    """프롬프트에 넣고 해시할 입력 payload 를 결정적 JSON 문자열로 만든다."""
    description = _HTML_TAG_RE.sub("", data.description)[:_PROMPT_DESCRIPTION_MAX_CHARS]
    payload = {
        "method": data.method,
        "path": data.path,
        "summary": data.summary,
        "description": description,
        "operation_id": data.operation_id,
        "param_names": data.param_names,
        "body_field_names": data.body_field_names,
        "tags": data.tags,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def compute_source_hash(payload_json: str) -> str:
    """payload 문자열 + 지시문 버전의 sha256 해시(재생성/낡음 판단 키, 55 §3)."""
    digest_input = f"{payload_json}:{METADATA_INSTRUCTION_VERSION}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
