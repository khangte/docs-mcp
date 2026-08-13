"""긴 섹션을 임베딩 토큰 상한 이하의 sub-chunk 텍스트로 분할하는 순수 함수.

`docs/architect-review/23-long-section-sub-chunking-phase2-design.md` §2 계층적
그리디 분할(문단 → 문장/줄 → 토큰 하드컷, overlap 없음)의 구현. 토큰 카운터는
임베딩 프로바이더에서 콜러블로 주입받아 이 모듈은 모델에 의존하지 않는다.

`docs/architect-review/26-pdf-docx-deterministic-catchall-gate-recheck.md`:
pdf/docx는 헤딩이 원리적으로 없어(파서가 서식 없는 순수 텍스트만 추출)
section_count=1 로 결정론적으로 묶이고, 실측(다중페이지 PDF 1건, 10335토큰)으로
480토큰 초과가 확인되어 게이트가 실배선 착수로 상향되었다. `chunk_builder.build_chunks`
가 이 모듈을 호출한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.services.parser.openapi_parser import ParsedSection

#: 문장/줄 경계: 종결부호(.!?。) 뒤 공백, 또는 줄바꿈.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。])\s+|\n")

CountTokens = Callable[[str], int]


def build_section_chunks(
    section: ParsedSection, count_tokens: CountTokens, token_limit: int
) -> list[str]:
    """섹션 1개를 상한 이하 텍스트 N개로 분할한다(상한 이하면 1개 그대로).

    각 sub는 `# {title}` 을 머리에 달아 문맥 앵커를 유지한다(제목 있을 때만).
    """
    title_prefix = f"# {section.title}\n" if section.title else ""
    full = title_prefix + section.content
    if count_tokens(full) <= token_limit:
        return [full]

    budget = max(1, token_limit - count_tokens(title_prefix))
    parts = _split_by_paragraph(section.content, budget, count_tokens)
    return [title_prefix + p for p in parts]


def _split_by_paragraph(content: str, budget: int, count_tokens: CountTokens) -> list[str]:
    """문단(`\\n\\n`) 경계로 그리디 팩킹. 문단 자체가 초과면 문장 단위로 폴백."""
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append("\n\n".join(buf))
            buf.clear()

    for para in content.split("\n\n"):
        if count_tokens(para) > budget:
            flush()
            out.extend(_split_by_sentence(para, budget, count_tokens))
            continue
        candidate = "\n\n".join([*buf, para])
        if buf and count_tokens(candidate) > budget:
            flush()
        buf.append(para)
    flush()
    return out


def _split_by_sentence(text: str, budget: int, count_tokens: CountTokens) -> list[str]:
    """문장/줄 경계로 그리디 팩킹. 단일 문장도 초과면 토큰 하드컷."""
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append(" ".join(buf))
            buf.clear()

    for sentence in _SENTENCE_BOUNDARY.split(text):
        if not sentence:
            continue
        if count_tokens(sentence) > budget:
            flush()
            out.extend(_hard_cut(sentence, budget, count_tokens))
            continue
        candidate = " ".join([*buf, sentence])
        if buf and count_tokens(candidate) > budget:
            flush()
        buf.append(sentence)
    flush()
    return out


def _hard_cut(text: str, budget: int, count_tokens: CountTokens) -> list[str]:
    """문장/줄 경계 없이도 상한을 지키도록 문자 단위 이분탐색으로 잘라낸다."""
    if not text:
        return []
    if count_tokens(text) <= budget:
        return [text]

    lo, hi, best = 1, len(text), 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if count_tokens(text[:mid]) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return [text[:best], *_hard_cut(text[best:], budget, count_tokens)]
