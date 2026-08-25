"""협업 문서 검색의 토큰화·제목/본문 점수 계산 순수 함수 모음."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from app.models.document_meta import DocumentMeta
from app.repositories.document_meta_repository import collapse

_LOG = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[0-9A-Za-z_]+|[가-힣]+")
_PURE_HANGUL_RE = re.compile(r"^[가-힣]+$")

#: 질의 하나에서 파생할 수 있는 복합어 term 총 개수 상한(concat + split 합산).
#: 질의 길이에 상한이 없어(_validate 는 top_k 만 본다) 토큰이 많으면 tsquery 가
#: 폭증할 수 있어 캡을 둔다. 평가셋이 없어 근거 있는 값이 아니므로 모듈 상수 고정,
#: env 미노출(RRF_K·TITLE_ARM_WEIGHT 와 같은 방침).
COMPOUND_TERM_LIMIT = 32
#: 2분할 시 양쪽 조각의 최소 길이(음절 1개짜리 조각은 잡음이라 만들지 않는다).
_MIN_SPLIT_PART_LEN = 2


def documents_tokenize(text: str) -> list[str]:
    """텍스트를 영숫자/언더스코어 또는 한글 덩어리 단위 소문자 토큰으로 자른다.

    협업 문서 제목에는 한글이 흔하므로 OpenAPI 쪽 토크나이저와 달리 한글
    음절 범위를 함께 인식한다.
    """
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _same_script_run(tokens: Sequence[str]) -> bool:
    """run 안의 모든 토큰이 순수 한글이거나 모두 그 외(ASCII 등)인지 확인.

    `TEXT_TSV_EXPRESSION` 이 ASCII/언더스코어 ↔ 한글 경계에 공백을 넣어
    tsvector 를 만들기 때문에, 본문 lexeme 은 항상 한 스크립트 부류로만
    이뤄진다. 경계를 넘어 이어붙이면 어떤 문서에도 없는 죽은 term 이 된다
    (59 §F2).
    """
    is_hangul = bool(_PURE_HANGUL_RE.match(tokens[0]))
    return all(bool(_PURE_HANGUL_RE.match(t)) == is_hangul for t in tokens[1:])


def compound_concat_terms(tokens: Sequence[str], limit: int | None = None) -> list[str]:
    """인접 토큰의 연속 run(길이 2 이상, 같은 스크립트 부류)을 이어붙인 term 목록.

    질의가 '결제 장애'(띄어씀)처럼 여러 토큰으로 쪼개져도, 본문이
    '결제장애'(붙여씀) 단일 lexeme 이면 keyword arm tsquery 의 OR 항으로
    이 concat term 을 추가해야 매치된다. `[a,b,c]` → `ab`, `bc`, `abc`
    순으로(짧은 run 먼저) 반환하며, 원본 토큰과 같은 값·중복 값은 뺀다.
    run 이 순수 한글/그 외 스크립트 경계를 넘으면 만들지 않는다(§F2) —
    그런 혼합 lexeme 은 본문에 존재할 수 없다. `limit` 지정 시 그 개수에
    도달하는 즉시 생성을 멈춘다(§F5, 토큰 수가 많을 때 O(n^3) 낭비 방지).
    """
    seen: set[str] = set(tokens)
    terms: list[str] = []
    token_count = len(tokens)
    for run_len in range(2, token_count + 1):
        for start in range(token_count - run_len + 1):
            run = tokens[start : start + run_len]
            if not _same_script_run(run):
                continue
            joined = "".join(run)
            if joined in seen:
                continue
            seen.add(joined)
            terms.append(joined)
            if limit is not None and len(terms) >= limit:
                return terms
    return terms


def compound_split_phrases(
    tokens: Sequence[str], limit: int | None = None
) -> list[tuple[str, str]]:
    """순수 한글 토큰의 2분할 후보 목록(양쪽 조각 길이 >= _MIN_SPLIT_PART_LEN).

    질의가 '결제장애'(붙여씀) 단일 토큰이어도, 본문이 '결제 장애'(띄어씀)
    두 lexeme 이면 tsquery 구문 연산자 `<->` 로 묶은 이 2분할 후보를 OR
    항으로 추가해야 매치된다. 사전 없이 정확한 경계를 알 수 없으므로
    가능한 2분할을 전부 낸다 — 엉뚱한 분할은 `<->` 인접성 강제로 사실상
    걸러진다. ASCII 복합어는 v1 범위 밖이라 순수 한글 토큰만 다룬다.
    `limit` 지정 시 그 개수에 도달하는 즉시 생성을 멈춘다(§F5).
    """
    seen: set[tuple[str, str]] = set()
    phrases: list[tuple[str, str]] = []
    for token in tokens:
        if len(token) < 2 * _MIN_SPLIT_PART_LEN or not _PURE_HANGUL_RE.match(token):
            continue
        for split_at in range(_MIN_SPLIT_PART_LEN, len(token) - _MIN_SPLIT_PART_LEN + 1):
            phrase = (token[:split_at], token[split_at:])
            if phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
            if limit is not None and len(phrases) >= limit:
                return phrases
    return phrases


def compound_terms_for_tokens(
    tokens: Sequence[str], limit: int = COMPOUND_TERM_LIMIT
) -> tuple[list[str], list[tuple[str, str]]]:
    """concat/split 복합어 term 을 만들어 `limit` 으로 캡한다.

    concat term 을 먼저 채우고 남는 예산만 split phrase 로 채운다(concat
    은 정확 매치라 split 보다 신호가 강하다). 생성 단계에서부터 상한을
    넘기지 않으므로 토큰 수가 많아도 계산 비용이 결과 크기 이상으로
    늘지 않는다(§F5). `limit` 은 원본 질의 + variant 문자열에 걸쳐 예산을
    누적 배분할 때 호출자가 남은 값을 넘기기 위한 매개변수다(§F3) —
    기본값은 모듈 상한 `COMPOUND_TERM_LIMIT` 전체다.
    """
    if limit <= 0:
        return [], []
    concat_terms = compound_concat_terms(tokens, limit=limit)
    remaining = limit - len(concat_terms)
    split_phrases = compound_split_phrases(tokens, limit=remaining) if remaining > 0 else []
    if len(concat_terms) == limit or (remaining > 0 and len(split_phrases) == remaining):
        _LOG.debug(
            "복합어 term 캡 도달: concat=%d split=%d limit=%d",
            len(concat_terms),
            len(split_phrases),
            limit,
        )
    return concat_terms, split_phrases


def _title_score(row: DocumentMeta, query_tokens: set[str], query: str) -> float:
    """제목(+URL)과 질의 토큰의 겹침 비율로 1단계 점수를 계산한다."""
    haystack_title = documents_tokenize(row.title)
    haystack_url = documents_tokenize(row.url)
    overlap = query_tokens & (set(haystack_title) | set(haystack_url))
    token_score = len(overlap) / len(query_tokens) if overlap else 0.0
    collapsed_score = max(
        _collapse_match_score(query, haystack_title, len(query_tokens)),
        _collapse_match_score(query, haystack_url, len(query_tokens)),
    )
    return max(token_score, collapsed_score)


def _body_score(body: str, query_tokens: set[str], query: str) -> float:
    """본문과 질의 토큰의 겹침 비율로 2단계 점수를 계산한다."""
    if not body:
        return 0.0
    haystack_tokens = documents_tokenize(body)
    overlap = query_tokens & set(haystack_tokens)
    token_score = len(overlap) / len(query_tokens) if overlap else 0.0
    collapsed_score = _collapse_match_score(query, haystack_tokens, len(query_tokens))
    return max(token_score, collapsed_score)


def _collapse_match_score(query: str, haystack_tokens: Sequence[str], token_count: int) -> float:
    """공백 변형(예: '트러블슈팅' vs '트러블 슈팅')을 흡수하되 토큰 경계를 존중하는 점수.

    `_token_aligned_concat_match` 로 질의 concat 이 haystack 토큰들의 연속
    부분열과 경계까지 일치하는지 본다(부분문자열 판정이면 'api' 가
    'Rapid' 안에서 걸리는 잡음이 생긴다). 통과하면 토큰 1개가 겹친 것과
    동등한 점수만 준다 — 이미 토큰 단위로 여러 개가 겹친 경우보다
    우선하지 않도록 상한을 낮게 잡아, `max()` 로 기존 겹침 비율과
    합성했을 때 순위 의미가 뒤집히지 않게 한다.
    """
    if not _token_aligned_concat_match(query, haystack_tokens):
        return 0.0
    return 1 / token_count


def _token_aligned_concat_match(query: str, haystack_tokens: Sequence[str]) -> bool:
    """질의 토큰 concat 이 haystack 토큰들의 **연속 부분열** concat 과 정확히 일치하는지.

    `_collapse_match_score` 와 달리 토큰 경계를 존중한다. '결제장애' 는 제목
    ['결제','장애','대응'] 의 연속 부분열 '결제'+'장애' 와 일치해 통과하지만,
    'api' 는 ['rapid','onboarding'] 의 어떤 연속 부분열과도 같지 않아 탈락한다
    (부분문자열로는 'rapid' 안에 들어 있지만 경계가 맞지 않는다).
    """
    target = "".join(documents_tokenize(query))
    if not target or not haystack_tokens:
        return False
    boundaries = {0}
    joined_parts: list[str] = []
    offset = 0
    for token in haystack_tokens:
        joined_parts.append(token)
        offset += len(token)
        boundaries.add(offset)
    joined = "".join(joined_parts)
    pos = joined.find(target)
    while pos >= 0:
        if pos in boundaries and pos + len(target) in boundaries:
            return True
        pos = joined.find(target, pos + 1)
    return False


def _passes_title_gate(row: DocumentMeta, filter_tokens: set[str], queries: Sequence[str]) -> bool:
    """title/url 이 질의와 토큰 수준에서 실제로 겹치는지(부분문자열 잡음 배제).

    SQL 1단계(`search_by_tokens`)는 `ILIKE '%token%'` 부분문자열 매칭이라
    토큰 경계를 무시한다 — 질의 토큰 'api' 가 제목의 'rapid' 안에 들어 있어도
    후보로 올라온다. `_title_score`(`_collapse_match_score` 경유)도 같은
    문제가 있어 게이트로 재사용할 수 없다(57번 리뷰 §5 개선3 T3 개정).
    이 함수는 별도로 토큰 경계를 지켜 그 잡음만 걸러낸다 — (원본 ∪ variant
    토큰) 중 하나가 title/url 토큰과 완전 일치하거나, 어떤 질의의 토큰
    concat 이 title/url 토큰들의 연속 부분열과 완전 일치하면 통과.
    title 토큰열과 url 토큰열은 따로 본다(둘을 이어붙인 경계를 넘는 매치는
    허용하지 않는다).
    """
    title_tokens = documents_tokenize(row.title)
    url_tokens = documents_tokenize(row.url)
    if filter_tokens & (set(title_tokens) | set(url_tokens)):
        return True
    return any(
        _token_aligned_concat_match(q, title_tokens) or _token_aligned_concat_match(q, url_tokens)
        for q in queries
    )


def _match_positions(body: str, query_tokens: set[str]) -> list[int]:
    """토큰별 최초 매치 위치(원본 `body` 기준 인덱스) 후보 목록을 만든다.

    스니펫 생성(`_build_snippet`)과 점수 계산(`_title_score`/`_body_score`)이
    서로 다른 매칭 기준을 쓰면 "점수는 매치로 잡히는데 스니펫은 엉뚱한
    곳을 보여주는" 불일치가 생긴다. 이 헬퍼를 양쪽이 공유해 판단 기준을
    하나로 유지한다.

    토큰마다 "정확 매치 우선, 없으면 collapse" 를 각각 독립적으로 적용해
    후보를 하나씩만 낸다(후보 수 = 토큰 수로 한정 — 흔한 토큰이 본문에
    수백 번 나와도 후보가 폭증하지 않는다). collapse 를 질의 전체가 아니라
    토큰 단위로 적용해야 '주문목록'이 본문의 '주문 목록' 구간과 제 힘으로
    매치되어 'api' 의 위치와 동등한 후보로 나란히 설 수 있다.

    단순히 이 후보들의 최솟값만 취하면, 흔하고 짧은 토큰이 문서 극초반에
    우연히 있을 때 그 위치가 항상 이겨서 정작 질의의 핵심 토큰이 담긴
    구간을 못 보여주는 문제가 있다(예: 'api'가 194번째, '주문목록'의
    collapse 매치가 2522번째인 문서에서 'api' 위치만 선택됨). 그래서 이
    함수는 최솟값을 고르지 않고 후보 전체를 반환한다 — 실제 스니펫 구간
    선택(커버리지 비교)은 호출자(`_build_snippet`)의 몫이다.

    Args:
        body: 매치 위치를 찾을 본문.
        query_tokens: 질의 토큰 집합.

    Returns:
        각 토큰의 매치 위치 후보 목록(매치 없는 토큰은 제외). 중복 위치는
        제거하지 않는다 — 호출자가 정렬해 순회한다.
    """
    if not query_tokens:
        return []
    lowered = body.lower()
    collapsed_body, index_map = _collapse_with_index_map(body)
    positions: list[int] = []
    for token in query_tokens:
        exact_pos = lowered.find(token)
        if exact_pos >= 0:
            positions.append(exact_pos)
            continue
        collapsed_token = collapse(token)
        if not collapsed_token:
            continue
        collapsed_pos = collapsed_body.find(collapsed_token)
        if collapsed_pos >= 0:
            positions.append(index_map[collapsed_pos])
    return positions


def _collapse_with_index_map(text: str) -> tuple[str, list[int]]:
    """공백을 제거하며, collapsed 인덱스 -> 원본 인덱스 매핑을 함께 만든다."""
    collapsed_chars: list[str] = []
    index_map: list[int] = []
    for original_index, char in enumerate(text or ""):
        if char.isspace():
            continue
        collapsed_chars.append(char.lower())
        index_map.append(original_index)
    return "".join(collapsed_chars), index_map
