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
    """

    external_id: str
    title: str
    url: str
    modified_at: datetime | None = None


@runtime_checkable
class DocumentSource(Protocol):
    """협업 문서 소스(Google Drive / Notion) 어댑터 인터페이스."""

    @property
    def source_name(self) -> str:
        """`document_meta.source` 에 기록할 소스 식별자(`drive`/`notion`)."""
        ...

    def list_files(self) -> list[FileMeta]:
        """설정된 범위 안의 문서 메타데이터 목록을 반환한다.

        본문은 가져오지 않는다.

        Raises:
            IntegrationError: 인증 실패·rate limit·네트워크 오류 등 외부 연동 실패.
        """
        ...

    def fetch(self, external_id: str) -> str:
        """문서 한 건의 본문을 평문 텍스트로 반환한다.

        Raises:
            IntegrationError: 문서가 없거나 외부 연동에 실패한 경우.
        """
        ...
