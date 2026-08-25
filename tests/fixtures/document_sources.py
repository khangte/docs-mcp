"""Drive/Notion 검증용 페이크 문서 소스.

실제 자격증명 없이 SPEC 기능 5~8 을 검증하기 위한 `DocumentSource` 구현이다.
실제 HTTP 호출은 하지 않고, `list_files()`/`fetch()` 호출 횟수와 fetch 대상
ID 를 기록해 다음 SPEC 검증 기준을 카운트로 단언할 수 있게 한다.

- 1단계 후보가 0건이면 본문 fetch 가 **한 번도** 일어나지 않는다.
- 한 번의 검색에서 fetch 하는 문서 수가 `top_k` 를 넘지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from app.core.errors import IntegrationError
from app.services.documents.sources.document_source import (
    FetchedDocument,
    FileListing,
    FileMeta,
)


class _FailingFileList(list):
    """N 번째 항목을 순회하는 순간 IntegrationError 를 던지는 리스트.

    `len()` 과 인덱싱은 정상 리스트처럼 동작하므로 서비스가 목록 조회에는
    성공했다고 판단하고 저장 루프에 진입한다. 그 루프 도중에 외부 연동이
    끊기는 상황(페이지네이션 중 rate limit 등)을 재현한다.
    """

    def __init__(self, items: list[FileMeta], fail_at: int, message: str) -> None:
        """원본 항목과 실패 인덱스·메시지를 보관한다."""
        super().__init__(items)
        self._fail_at = fail_at
        self._message = message

    def __iter__(self):
        """설정된 인덱스에 도달하면 IntegrationError 를 발생시키며 순회한다."""
        for index, item in enumerate(list.__iter__(self)):
            if index == self._fail_at:
                raise IntegrationError(self._message)
            yield item


class FakeDocumentSource:
    """메모리 딕셔너리로 동작하는 문서 소스 페이크."""

    def __init__(
        self,
        source_name: str,
        files: list[FileMeta] | None = None,
        bodies: dict[str, str] | None = None,
    ) -> None:
        """소스 이름과 초기 파일/본문 데이터를 보관하고 호출 카운터를 초기화한다.

        Args:
            source_name: `drive` 또는 `notion`.
            files: `list_files()` 가 돌려줄 메타데이터 목록.
            bodies: external_id → 본문 텍스트 매핑.
        """
        self._source_name = source_name
        self.files: list[FileMeta] = list(files or [])
        self.bodies: dict[str, str] = dict(bodies or {})
        self.list_call_count = 0
        self.fetch_call_count = 0
        self.fetched_ids: list[str] = []
        #: True 로 두면 `list_files()` 가 IntegrationError 를 던진다(소스 간 격리 시나리오).
        self.list_should_fail = False
        #: 이 집합에 든 external_id 는 fetch 시 IntegrationError 를 던진다.
        self.failing_fetch_ids: set[str] = set()
        #: 0 보다 크면 `list_files()` 는 성공하되 반환 목록의 N 번째(0-based) 항목을
        #: 소비하는 순간 IntegrationError 를 던진다. "목록 조회는 됐는데 저장
        #: 도중 끊긴" 상황을 재현해 배치 커밋 경계를 검증하는 데 쓴다.
        self.fail_listing_at_index: int | None = None
        #: 이 집합에 든 external_id 는 fetch 시 truncated=True 로 반환한다
        #: (search 경로가 truncated 를 무시하는지 검증하는 용도).
        self.truncated_ids: set[str] = set()
        #: `list_files()` 가 반환할 FileListing.truncated 값(개선 #5).
        self.listing_truncated = False
        #: 이 집합에 든 MIME 타입은 `supports_text_extraction()` 이 False 를 반환한다.
        self.unsupported_mime_types: set[str] = set()

    @property
    def source_name(self) -> str:
        """`document_meta.source` 에 기록할 소스 식별자."""
        return self._source_name

    def list_files(self) -> FileListing:
        """등록된 메타데이터 목록을 반환하고 호출 횟수를 기록한다.

        `fail_listing_at_index` 가 설정돼 있으면 목록 조회 자체는 성공하되,
        반환된 목록을 순회하다 해당 인덱스에서 IntegrationError 가 터진다.
        """
        self.list_call_count += 1
        if self.list_should_fail:
            raise IntegrationError(f"fake {self._source_name} list failure")
        if self.fail_listing_at_index is not None:
            return FileListing(
                files=_FailingFileList(
                    list(self.files),
                    self.fail_listing_at_index,
                    f"fake {self._source_name} failure while saving",
                ),
                truncated=self.listing_truncated,
            )
        return FileListing(files=list(self.files), truncated=self.listing_truncated)

    def supports_text_extraction(self, mime_type: str | None) -> bool:
        """`unsupported_mime_types` 에 등록된 MIME 이 아니면 True(개선 #5)."""
        if not mime_type:
            return True
        return mime_type not in self.unsupported_mime_types

    def fetch(self, external_id: str) -> FetchedDocument:
        """본문을 반환하고 호출 횟수/대상 ID 를 기록한다."""
        self.fetch_call_count += 1
        self.fetched_ids.append(external_id)
        if external_id in self.failing_fetch_ids:
            raise IntegrationError(f"fake {self._source_name} fetch failure: {external_id}")
        if external_id not in self.bodies:
            raise IntegrationError(
                f"fake {self._source_name} document not found: {external_id}"
            )
        return FetchedDocument(
            text=self.bodies[external_id], truncated=external_id in self.truncated_ids
        )

    def reset_counts(self) -> None:
        """호출 카운터와 기록을 초기화한다."""
        self.list_call_count = 0
        self.fetch_call_count = 0
        self.fetched_ids = []

    def put(
        self,
        external_id: str,
        title: str,
        body: str,
        url: str | None = None,
        modified_at: datetime | None = None,
        mime_type: str | None = None,
        created_at: datetime | None = None,
        owner: str | None = None,
    ) -> None:
        """문서 한 건(메타 + 본문)을 등록하거나 덮어쓴다."""
        self.files = [f for f in self.files if f.external_id != external_id]
        self.files.append(
            FileMeta(
                external_id=external_id,
                title=title,
                url=url or f"https://example.test/{self._source_name}/{external_id}",
                modified_at=modified_at,
                mime_type=mime_type,
                created_at=created_at,
                owner=owner,
            )
        )
        self.bodies[external_id] = body

    def remove(self, external_id: str) -> None:
        """문서 한 건을 소스에서 제거한다(원본 삭제 시나리오)."""
        self.files = [f for f in self.files if f.external_id != external_id]
        self.bodies.pop(external_id, None)


class ExplodingDocumentSource:
    """호출되면 즉시 실패하는 페이크.

    "이 경로에서는 외부 문서 API 가 절대 호출되면 안 된다"를 카운트가 아니라
    예외로 단언하고 싶을 때 쓴다.
    """

    def __init__(self, source_name: str) -> None:
        """소스 이름만 보관한다."""
        self._source_name = source_name

    @property
    def source_name(self) -> str:
        """`document_meta.source` 에 기록할 소스 식별자."""
        return self._source_name

    def list_files(self) -> FileListing:
        """호출되면 AssertionError 를 발생시킨다."""
        raise AssertionError(
            f"list_files() 가 호출되면 안 되는 경로에서 호출됨: {self._source_name}"
        )

    def fetch(self, external_id: str) -> FetchedDocument:
        """호출되면 AssertionError 를 발생시킨다."""
        raise AssertionError(
            f"fetch() 가 호출되면 안 되는 경로에서 호출됨: {self._source_name}/{external_id}"
        )
