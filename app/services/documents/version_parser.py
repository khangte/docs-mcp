"""문서 제목에서 버전 표기를 파싱하는 순수 함수."""

from __future__ import annotations

import re

_VERSION_RE = re.compile(r"(?i)(?<![0-9A-Za-z])v[\s_]?(\d+(?:[._]\d+)*)")


def parse_version(title: str) -> str | None:
    """제목에서 `v`-접두 숫자 버전을 파싱한다.

    여러 개 있으면 마지막 매치를 쓴다(버전은 제목 접미사에 붙는 관례).
    캡처값의 `_`는 `.`로 정규화한다(`v_1.0` -> `v1.0`). `rev2`/`level2`처럼
    단어 내부에 있는 `v`는 부정 룩비하인드로 배제한다. `(최종)`/`final`/
    `draft` 같은 상태 마커는 버전과 성격이 달라 여기서 다루지 않는다 —
    파싱 대상이 아니므로 `title`에 그대로 남는다.

    Returns:
        `"v" + 정규화된 숫자` 형태 문자열. 버전 표기가 없으면 `None`
        (에러가 아니라 정상 경로).
    """
    matches = _VERSION_RE.findall(title or "")
    if not matches:
        return None
    return "v" + matches[-1].replace("_", ".")
