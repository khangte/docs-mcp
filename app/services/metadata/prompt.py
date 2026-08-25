"""CLI 생성 경로의 LLM 프롬프트 조립(입력 payload/해시는 spec_payload 로 이관).

docs/architect-review/56 §1.3: payload 조립과 해시는 write-back 경로와
공유해야 하므로 `spec_payload` 에 있다. 이 모듈은 LLM 프롬프트 문안만 갖는다.
"""

from __future__ import annotations

from app.services.metadata.spec_payload import (
    METADATA_INSTRUCTION_VERSION,
    EndpointMetadataInput,
    build_payload_json,
    compute_source_hash,
)

#: 기존 import 경로(`prompt.PROMPT_VERSION`) 호환용 별칭.
PROMPT_VERSION = METADATA_INSTRUCTION_VERSION

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


def build_user_prompt(payload_json: str) -> str:
    """유저 턴 프롬프트: 입력 payload 를 그대로 담는다."""
    return f"다음 엔드포인트에 대한 메타데이터를 생성해라:\n{payload_json}"


__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "EndpointMetadataInput",
    "build_payload_json",
    "build_user_prompt",
    "compute_source_hash",
]
