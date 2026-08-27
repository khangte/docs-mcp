"""문서 소스 공통 인터페이스 (SPEC 기능 5).

서비스 계층은 이 Protocol 만 참조하고 구체 SDK/HTTP 구현을 직접 import 하지
않는다(기존 `OpenAPIFetcher` 와 동일 원칙). 덕분에 어댑터 교체와 테스트
페이크 주입이 가능하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

#: 소스가 하나도 구성되지 않았을 때 모든 도구가 공유하는 오류 메시지.
#: `search_documents`/`get_document`/`refresh_index` 가 같은 문구를 쓰도록
#: 한 곳에 모아, "결과 없음"과 "서버 미설정"을 사용자가 구별할 수 있게 한다.
NO_SOURCE_CONFIGURED_MESSAGE = (
    "no document source is configured: set google drive or notion credentials"
)


@dataclass(frozen=True)
class FileMeta:
    """문서 목록 조회로 얻는 메타데이터 한 건(본문 없음).

    Attributes:
        external_id: 출처 시스템의 문서 식별자.
        title: 문서 제목.
        url: 사람이 열어볼 수 있는 원본 문서 URL.
        modified_at: 원본 시스템 기준 최종 수정 시각. 알 수 없으면 None.
        mime_type: 출처 시스템의 MIME 타입. Drive 전용, Notion 은 항상 None.
        created_at: 원본 시스템 기준 생성 시각. 알 수 없으면 None.
        owner: 문서 소유자 이메일 또는 표시 이름. Drive 전용, Notion 은 항상 None.
        folder_ancestor_ids: 동기화 루트부터 직계 부모까지의 폴더 id 목록.
            Drive 전용이며 다른 소스는 빈 튜플이다.
        folder_path: 같은 폴더 체인의 이름을 "/" 로 이은 경로(동기화 루트
            제외). Drive 루트 직속 파일은 빈 문자열, 다른 소스는 None 이다.
    """

    external_id: str
    title: str
    url: str
    modified_at: datetime | None = None
    mime_type: str | None = None
    created_at: datetime | None = None
    owner: str | None = None
    folder_ancestor_ids: tuple[str, ...] = ()
    folder_path: str | None = None


@dataclass(frozen=True)
class FileListing:
    """`list_files()` 한 번의 결과(개선 #5).

    Attributes:
        files: 조회된 문서 메타데이터 목록.
        truncated: 탐색 상한(Drive MAX_FOLDERS / Notion MAX_PAGES)에 걸려
            목록이 불완전하면 True.
    """

    files: list[FileMeta]
    truncated: bool = False


@dataclass(frozen=True)
class FetchedDocument:
    """`fetch()` 가 반환하는 본문 한 건.

    Attributes:
        text: 최대 문자 수(`max_chars`)로 잘린 평문 본문. NUL(``\\x00``) 바이트는
            PostgreSQL 텍스트 컬럼에 저장할 수 없어 여기서 제거된다(다른 제어
            문자는 저장 가능하므로 건드리지 않는다).
        truncated: 원본이 `max_chars` 를 초과해 잘렸으면 True. 정확히
            `max_chars` 길이인 원본은 잘린 게 아니므로 False.
    """

    text: str
    truncated: bool

    def __post_init__(self) -> None:
        """text 에 섞인 NUL 바이트를 제거한다(모든 `DocumentSource.fetch()` 구현 공통 경계)."""
        if "\x00" in self.text:
            object.__setattr__(self, "text", self.text.replace("\x00", ""))


@runtime_checkable
class DocumentSource(Protocol):
    """협업 문서 소스(Google Drive / Notion) 어댑터 인터페이스."""

    @property
    def source_name(self) -> str:
        """`document_meta.source` 에 기록할 소스 식별자(`drive`/`notion`)."""
        ...

    def list_files(self) -> FileListing:
        """설정된 범위 안의 문서 메타데이터 목록을 반환한다.

        본문은 가져오지 않는다.

        Raises:
            IntegrationError: 인증 실패·rate limit·네트워크 오류 등 외부 연동 실패.
        """
        ...

    def fetch(self, external_id: str) -> FetchedDocument:
        """문서 한 건의 본문을 평문 텍스트로 반환한다.

        Raises:
            IntegrationError: 문서가 없거나 외부 연동에 실패한 경우.
        """
        ...

    def supports_text_extraction(self, mime_type: str | None) -> bool:
        """이 MIME 타입에서 본문 텍스트를 추출할 수 있으면 True(개선 #5).

        `mime_type` 이 None/빈 문자열이면 True — 판정 실패로 색인을
        누락시키느니 fetch 가 실패하게 둔다.
        """
        ...
