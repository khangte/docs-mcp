"""LLM 프롬프트 조립: 입력 payload 직렬화 + 시스템/유저 프롬프트 + 재생성 판단 해시.

docs/architect-review/55 §2,§3: `PROMPT_VERSION` 을 소스 해시에 포함시켜,
프롬프트를 개선하면 기존 메타데이터가 자동으로 재생성 대상이 되게 한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

_HTML_TAG_RE = re.compile(r"<[^>]+>")
#: 청크 절단(300자, `chunk_builder.py`)과 다른 예산 — 청크는 색인 비용
#: 문제고 여기는 요약 근거 확보 문제라 더 넓게 준다(55 §2.4).
_PROMPT_DESCRIPTION_MAX_CHARS = 600

#: 프롬프트 내용을 바꾸면 반드시 올린다 — `compute_source_hash` 에 포함돼
#: 기존 메타데이터를 자동으로 재생성 대상으로 만든다(55 §3).
PROMPT_VERSION = "1"

SYSTEM_PROMPT = """당신은 OpenAPI 엔드포인트 문서에 검색용 비즈니스 메타데이터를 붙이는 \
어시스턴트다.

규칙:
- 주어진 입력에 없는 사실을 만들지 않는다. 근거가 부족하면 짧게 쓰거나 해당 필드를 비운다.
- business_description: 한국어 1문장, 최대 120자.
- user_phrases: 한국어 2개 + 영어 2개, 각 최대 40자. summary의 동사와 다른, \
사용자가 실제로 쓸 법한 동사 표현을 반드시 하나 이상 포함한다(예: \
cancel<->delete/remove/terminate, create<->add/register, list<->get all/fetch).
- keywords: 영어(필드명·도메인 용어)와 한국어를 섞어 최대 5개, 각 최대 30자.
- 출력은 아래 스키마의 JSON 객체 하나만 반환한다. 그 외 텍스트(설명, 코드펜스 등)를 붙이지 않는다.

{"business_description": "...", "keywords": ["..."], "user_phrases": ["..."]}
"""


@dataclass(frozen=True)
class EndpointMetadataInput:
    """LLM 호출 1건의 입력(엔드포인트 1건)."""

    method: str
    path: str
    summary: str
    description: str
    operation_id: str
    param_names: list[str] = field(default_factory=list)
    body_field_names: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


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


def build_user_prompt(payload_json: str) -> str:
    """유저 턴 프롬프트: 입력 payload 를 그대로 담는다."""
    return f"다음 엔드포인트에 대한 메타데이터를 생성해라:\n{payload_json}"


def compute_source_hash(payload_json: str) -> str:
    """payload 문자열 + PROMPT_VERSION 의 sha256 해시(재생성 판단 키, 55 §3)."""
    digest_input = f"{payload_json}:{PROMPT_VERSION}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
