"""Notion API 응답 → 평문/`FileMeta` 순수 변환 함수 모음.

HTTP 호출을 모른다 — `notion_source.NotionSource` 가 받아온 dict 를 넣으면
평문 텍스트나 `FileMeta` 가 나온다. 어댑터에서 분리해 둔 덕에 이 모듈의
테스트는 `httpx.MockTransport` 없이 값 비교만으로 끝난다.
"""

from __future__ import annotations

from typing import Any

from app.services.documents.sources.document_source import FileMeta
from app.services.documents.sources.time_parsing import parse_rfc3339

UNTITLED = "(제목 없음)"

#: 블록 타입 → markdown 마커. Notion 평문에는 서식 정보가 없어서, 이 마커를
#: 되살려야 `markdown_parser.parse_document` 가 헤딩 단위로 섹션을 쪼갠다.
#: 마커가 없으면 문서 전체가 "개요" 섹션 1개가 되고 모든 청크 앵커가
#: `# 개요` 로 뭉개진다(`docs/architect-review/50` §2.4).
_BLOCK_PREFIXES: dict[str, str] = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
}


def _block_prefix(block_type: str, payload: dict[str, Any]) -> str:
    """블록 타입(과 to_do 의 체크 상태)에 대응하는 markdown 마커를 돌려준다."""
    if block_type == "to_do":
        return "- [x] " if payload.get("checked") else "- [ ] "
    return _BLOCK_PREFIXES.get(block_type, "")


def rich_text_to_plain(items: Any) -> str:
    """Notion rich_text 배열을 평문으로 이어 붙인다."""
    if not isinstance(items, list):
        return ""
    parts = [
        str(item.get("plain_text") or "")
        for item in items
        if isinstance(item, dict)
    ]
    return "".join(parts).strip()


def cells_to_plain(cells: Any) -> str:
    """`table_row.cells`(rich_text 배열의 배열)를 ' | ' 로 이은 한 줄로 만든다.

    행 단위 한 줄이 FTS·임베딩 양쪽에서 가장 자연스럽다 — 셀을 줄바꿈으로
    쪼개면 같은 행의 증상·원인·해결이 서로 다른 청크로 흩어질 수 있다.
    """
    if not isinstance(cells, list):
        return ""
    values = [rich_text_to_plain(cell) for cell in cells]
    return " | ".join(value for value in values if value)


def block_plain_text(block: dict[str, Any]) -> str:
    """블록 한 개에서 평문 텍스트를 추출한다(markdown 마커 포함).

    `rich_text` 만 보던 기존 구현이 표(`cells`)·캡션(`caption`)·하위 문서
    제목(`title`)·링크(`url`)·수식(`expression`)을 전부 흘렸다
    (`docs/architect-review/50` §2.4). 한 블록이 여러 필드를 동시에 갖는
    경우(예: bookmark 의 caption+url)가 있으므로 모두 모아 공백으로 잇는다.

    텍스트가 비면 마커만 남기지 않고 빈 문자열을 돌려준다 — 빈 헤딩 줄이
    파서에 섹션 경계로 잡히면 내용 없는 섹션이 생긴다.
    """
    block_type = str(block.get("type") or "")
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return ""
    parts = [
        rich_text_to_plain(payload.get("rich_text")),
        cells_to_plain(payload.get("cells")),
        rich_text_to_plain(payload.get("caption")),
        # child_page / child_database 의 제목(문자열). 헤딩 마커는 일부러 안
        # 붙인다 — 마커를 붙이면 본문 없는 섹션이 되어 파서가 버린다.
        str(payload.get("title") or "").strip(),
        # bookmark / embed / link_preview 의 대상 URL.
        str(payload.get("url") or "").strip(),
        # equation 의 수식 원문.
        str(payload.get("expression") or "").strip(),
    ]
    text = " ".join(part for part in parts if part)
    if not text:
        return ""
    return _block_prefix(block_type, payload) + text


def page_title(page: dict[str, Any]) -> str:
    """페이지 properties 에서 title 타입 속성을 찾아 제목을 만든다."""
    properties = page.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = rich_text_to_plain(prop.get("title"))
                if title:
                    return title
    return UNTITLED


def child_page_to_file_meta(block: dict[str, Any]) -> FileMeta:
    """`child_page` 타입 블록 하나를 FileMeta 로 변환한다.

    블록 자체의 id 가 하위 페이지의 page id 다(그대로 fetch 대상 external_id).
    """
    block_id = str(block.get("id") or "")
    child_page = block.get("child_page")
    title = UNTITLED
    if isinstance(child_page, dict):
        title = str(child_page.get("title") or "") or UNTITLED
    return FileMeta(
        external_id=block_id,
        title=title,
        url=f"https://www.notion.so/{block_id.replace('-', '')}",
        modified_at=parse_rfc3339(block.get("last_edited_time")),
        created_at=parse_rfc3339(block.get("created_time")),
    )


def to_file_meta(page: dict[str, Any]) -> FileMeta:
    """Notion 페이지 응답 항목 하나를 FileMeta 로 변환한다."""
    page_id = str(page.get("id") or "")
    return FileMeta(
        external_id=page_id,
        title=page_title(page),
        url=str(page.get("url") or f"https://www.notion.so/{page_id.replace('-', '')}"),
        modified_at=parse_rfc3339(page.get("last_edited_time")),
        created_at=parse_rfc3339(page.get("created_time")),
    )


def property_plain_text(prop: dict[str, Any]) -> str:
    """페이지/DB 행 속성 하나를 평문 문자열로 만든다.

    `formula` 는 결과값이 이미 `GET /pages/{id}` 응답 안에 있어 추가 호출
    없이 넣는다 — 값이 `{"type": "string"|"number"|"boolean"|"date", ...}`
    로 한 겹 더 감싸여 있으므로 이 함수를 그 안쪽에 재귀 적용한다.

    `relation`/`rollup` 은 제외한다. 비용 문제가 아니라 색인 품질 문제다 —
    `relation` 값은 대상 페이지의 UUID 뿐이고 사람은 UUID 로 검색하지
    않는다. `chunk.text_tsv` 생성식은 하이픈에서 UUID 를 쪼개 `8f3a` 같은
    무의미 lexeme 만 늘리고, 임베딩에서는 그 hex 토큰이 같은 청크의 실제
    본문 신호를 희석시킨다. 유용한 형태(대상 페이지 제목)를 얻으려면
    relation 개수만큼 추가 호출이 필요한데 이는 범위 밖이다
    (`docs/architect-review/51_notion_property_relation_formula_scope_verdict.md`).
    """
    prop_type = str(prop.get("type") or "")
    value = prop.get(prop_type)
    if prop_type in ("title", "rich_text"):
        return rich_text_to_plain(value)
    if prop_type in ("select", "status"):
        return str(value.get("name") or "") if isinstance(value, dict) else ""
    if prop_type in ("multi_select", "people"):
        if not isinstance(value, list):
            return ""
        names = [str(item.get("name") or "") for item in value if isinstance(item, dict)]
        return ", ".join(name for name in names if name)
    if prop_type == "date":
        if not isinstance(value, dict):
            return ""
        start = str(value.get("start") or "")
        end = str(value.get("end") or "")
        if not start:
            return ""
        return f"{start} ~ {end}" if end else start
    if prop_type == "checkbox":
        return "true" if value else "false"
    if prop_type == "boolean":
        # formula 의 boolean 결과. 값이 없는 것을 false 로 색인하면 없던
        # 신호가 생기므로 checkbox 와 달리 None 은 빈 문자열로 낸다.
        if value is None:
            return ""
        return "true" if value else "false"
    if prop_type in ("number", "url", "email", "phone_number", "string"):
        return str(value) if value is not None else ""
    if prop_type == "formula":
        return property_plain_text(value) if isinstance(value, dict) else ""
    return ""
