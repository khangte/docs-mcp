"""OpenAPI 태그 목록 조회 서비스.

LLM 이 검색 범위를 좁힐 때 쓰는 탐색 보조 정보를 만든다.
등록된 엔드포인트의 태그를 집계해 태그별 엔드포인트 수를 돌려준다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.core.errors import DocumentNotFoundError
from app.repositories.document_repository import DocumentRepository
from app.repositories.endpoint_repository import EndpointRepository


@dataclass(frozen=True)
class TagSummary:
    """태그 한 건의 이름과 해당 태그가 붙은 엔드포인트 수."""

    name: str
    endpoint_count: int


class TagCatalogService:
    """등록 문서의 태그를 집계해 목록으로 제공하는 서비스."""

    def __init__(
        self,
        endpoint_repo: EndpointRepository,
        document_repo: DocumentRepository,
    ) -> None:
        """엔드포인트/문서 저장소 의존성을 보관한다."""
        self._endpoint_repo = endpoint_repo
        self._document_repo = document_repo

    def list_tags(self, document_id: str | None = None) -> list[TagSummary]:
        """태그 목록을 집계해 반환한다.

        Args:
            document_id: 특정 문서로 범위를 제한할 때 지정. 생략하면 전체 문서.

        Returns:
            엔드포인트 수 내림차순, 동수일 때는 이름 오름차순으로 정렬된 태그
            목록. 태그가 하나도 없으면 빈 리스트.

        Raises:
            DocumentNotFoundError: document_id 가 등록되지 않은 문서인 경우.
        """
        if document_id is not None and self._document_repo.get(document_id) is None:
            raise DocumentNotFoundError(document_id)

        counter: Counter[str] = Counter()
        for endpoint in self._endpoint_repo.list_all(document_id=document_id):
            for tag in endpoint.tags:
                normalized = str(tag).strip()
                if normalized:
                    counter[normalized] += 1

        return [
            TagSummary(name=name, endpoint_count=count)
            for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
