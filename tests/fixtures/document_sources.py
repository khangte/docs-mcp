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
from app.services.documents.document_source import FileMeta


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
        #: True 로 두면 `list_files()` 가 IntegrationError 를 던진다(부분 실패 시나리오).
        self.list_should_fail = False
        #: 이 집합에 든 external_id 는 fetch 시 IntegrationError 를 던진다.
        self.failing_fetch_ids: set[str] = set()

    @property
    def source_name(self) -> str:
        """`document_meta.source` 에 기록할 소스 식별자."""
        return self._source_name

    def list_files(self) -> list[FileMeta]:
        """등록된 메타데이터 목록을 반환하고 호출 횟수를 기록한다."""
        self.list_call_count += 1
        if self.list_should_fail:
            raise IntegrationError(f"fake {self._source_name} list failure")
        return list(self.files)

    def fetch(self, external_id: str) -> str:
        """본문을 반환하고 호출 횟수/대상 ID 를 기록한다."""
        self.fetch_call_count += 1
        self.fetched_ids.append(external_id)
        if external_id in self.failing_fetch_ids:
            raise IntegrationError(f"fake {self._source_name} fetch failure: {external_id}")
        if external_id not in self.bodies:
            raise IntegrationError(
                f"fake {self._source_name} document not found: {external_id}"
            )
        return self.bodies[external_id]

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
    ) -> None:
        """문서 한 건(메타 + 본문)을 등록하거나 덮어쓴다."""
        self.files = [f for f in self.files if f.external_id != external_id]
        self.files.append(
            FileMeta(
                external_id=external_id,
                title=title,
                url=url or f"https://example.test/{self._source_name}/{external_id}",
                modified_at=modified_at,
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

    def list_files(self) -> list[FileMeta]:
        """호출되면 AssertionError 를 발생시킨다."""
        raise AssertionError(
            f"list_files() 가 호출되면 안 되는 경로에서 호출됨: {self._source_name}"
        )

    def fetch(self, external_id: str) -> str:
        """호출되면 AssertionError 를 발생시킨다."""
        raise AssertionError(
            f"fetch() 가 호출되면 안 되는 경로에서 호출됨: {self._source_name}/{external_id}"
        )
