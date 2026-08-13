"""섹션 sub-chunking 순수 분할 로직 테스트(docs/architect-review/23)."""

from __future__ import annotations

from app.services.indexer.section_splitter import build_section_chunks
from app.services.parser.openapi_parser import ParsedSection


def _count_words(text: str) -> int:
    """모델 없이 쓰는 페이크 토큰 카운터(단어수 기준)."""
    return len(text.split())


def test_within_limit_returns_single_chunk_unchanged() -> None:
    section = ParsedSection(title="개요", content="short body")
    chunks = build_section_chunks(section, _count_words, token_limit=10)
    assert chunks == ["# 개요\nshort body"]


def test_over_limit_packs_paragraphs_greedily() -> None:
    para_a = "word " * 3  # 3 tokens
    para_b = "word " * 3  # 3 tokens
    para_c = "word " * 3  # 3 tokens
    content = "\n\n".join([para_a.strip(), para_b.strip(), para_c.strip()])
    section = ParsedSection(title="t", content=content)
    # title "# t\n" = 2 tokens("#","t") -> budget = limit - 2
    # limit=8 -> budget=6 -> 두 문단(6토큰)까진 들어가고 세번째는 새 청크
    chunks = build_section_chunks(section, _count_words, token_limit=8)
    assert len(chunks) == 2
    assert all(c.startswith("# t\n") for c in chunks)
    assert para_a.strip() in chunks[0] and para_b.strip() in chunks[0]
    assert para_c.strip() in chunks[1]


def test_paragraph_over_budget_falls_back_to_sentence_split() -> None:
    # 한 문단 자체가 예산 초과 -> 문장 경계로 재분할
    long_para = "One two three. Four five six. Seven eight nine."
    section = ParsedSection(title="", content=long_para)
    chunks = build_section_chunks(section, _count_words, token_limit=4)
    assert len(chunks) > 1
    # title 없음 -> prefix 없이 그대로
    assert all(not c.startswith("#") for c in chunks)
    rejoined = " ".join(chunks)
    for word in "One two three Four five six Seven eight nine".split():
        assert word in rejoined


def test_single_sentence_over_budget_hard_cuts() -> None:
    # 문장 구분자 없는 단일 토큰 폭주 -> 문자 단위 하드컷
    long_word = "x" * 50  # count_tokens는 공백 기준이라 이건 "단어" 1개=1토큰
    section = ParsedSection(title="", content=long_word)
    chunks = build_section_chunks(section, lambda t: len(t), token_limit=10)
    assert len(chunks) == 5
    assert "".join(chunks) == long_word
    assert all(len(c) <= 10 for c in chunks)


def test_title_prefix_repeated_and_deducted_from_budget() -> None:
    section = ParsedSection(title="아주 긴 제목", content="a b c d e f")
    chunks = build_section_chunks(section, _count_words, token_limit=4)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.startswith("# 아주 긴 제목\n")
