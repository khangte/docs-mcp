"""MCP 도구 응답 스키마.

각 @mcp.tool() 함수가 실제로 반환하는 dict 구조를 TypedDict로 명시한다.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class ErrorPayload(TypedDict):
    """DomainError/IntegrationError 발생 시 도구가 반환하는 에러 페이로드."""

    error: bool
    code: str
    message: str


class DocumentSummary(TypedDict):
    """list_documents 가 반환하는 리스트의 원소 타입."""

    document_id: str
    title: str
    version: str
    doc_type: str
    project: str
    source_url: str | None
    endpoints_count: int
    indexed_at: str | None


class RegisterDocumentResult(TypedDict):
    """register_document 의 반환 타입."""

    document_id: str
    title: str
    version: str
    doc_type: str
    project: str
    endpoints_count: int
    sections_count: int
    chunks_count: int
    status: str


class EndpointCandidateItem(TypedDict):
    """search_endpoints 가 반환하는 후보 한 건.

    후보 식별에 필요한 최소 필드만 담는다. 파라미터·응답 등 상세 정보와
    snippet 은 포함하지 않는다(상세는 get_endpoint_details 로 조회).
    """

    endpoint_id: str
    method: str
    path: str
    summary: str
    match_type: Literal["keyword", "vector", "both", "exact"]


class EndpointSearchResponse(TypedDict):
    """search_endpoints 의 반환 타입."""

    items: list[EndpointCandidateItem]


class ParameterItem(TypedDict):
    """get_endpoint_details 응답의 parameters 원소 타입."""

    name: str
    location: str
    required: bool
    description: str
    schema: dict[str, Any]
    schema_ref: str | None


class RequestBodyItem(TypedDict):
    """get_endpoint_details 응답의 request_body 타입."""

    content_type: str
    required: bool
    schema: dict[str, Any]
    schema_ref: str | None


class ResponseItem(TypedDict):
    """get_endpoint_details 응답의 responses 원소 타입."""

    status_code: str
    content_type: str
    description: str
    schema: dict[str, Any]
    schema_ref: str | None


class RelatedEndpointItem(TypedDict):
    """get_endpoint_details 응답의 related_endpoints 원소 타입(순회 힌트)."""

    endpoint_id: str
    method: str
    path: str


class MetadataRequestPayload(TypedDict):
    """get_endpoint_details 응답의 메타데이터 기여 요청 힌트(56 §3.2).

    메타데이터가 이미 최신이면 상위 응답에 이 키 자체가 없다.
    """

    reason: str
    instruction: str


class MetadataSubmitResult(TypedDict):
    """submit_endpoint_metadata 의 반환 타입.

    `status` 는 "stored"(저장됨) | "already_current"(해시 동일해 무시) |
    "rejected"(정규화 후 내용 없음). `reason` 은 status 가 "stored" 가 아닐
    때만 키가 존재한다. `reindexed=False` 는 저장은 됐지만 즉시 색인 반영에
    실패했다는 뜻으로, 다음 전체 재색인에서 반영된다(56 §4.4).
    """

    status: str
    endpoint_id: str
    reindexed: bool
    truncated: bool
    reason: NotRequired[str]


class EndpointDetails(TypedDict):
    """get_endpoint_details 의 반환 타입.

    `example_code` 는 include_example=True 로 호출했을 때만 키가 존재한다.
    `referenced_schema_refs`/`related_endpoints` 는 순회 힌트다 — 서버가
    다음 홉을 자동 호출하지 않고 후보만 노출한다(밟을지는 호출측 판단,
    `docs/architect-review/12_rag_depth_directions.md` 후보2 얇은 버전).
    `metadata_request` 는 기여 요청 힌트이며 최신이면 키가 없다.
    """

    endpoint_id: str
    document_id: str
    method: str
    path: str
    summary: str
    description: str
    tags: list[str]
    parameters: list[ParameterItem]
    request_body: RequestBodyItem | None
    responses: list[ResponseItem]
    example_code: NotRequired[str]
    referenced_schema_refs: list[str]
    related_endpoints: list[RelatedEndpointItem]
    #: 메타데이터가 없거나 스펙 변경으로 낡았을 때만 존재한다(56 §3.2).
    #: 최신이거나 write-back 이 꺼져 있으면 키 자체가 없다(토큰 오버헤드 0).
    metadata_request: NotRequired[MetadataRequestPayload]


class SchemaFieldItem(TypedDict):
    """resolve_ref 응답의 fields 원소 타입."""

    name: str
    type: str
    required: bool
    description: str


class ResolvedSchemaResult(TypedDict):
    """resolve_ref 의 반환 타입.

    `document_id` 는 스키마가 속한 문서를 밝혀, 여러 문서에 동명 스키마가 있을 때
    어느 문서의 것을 받았는지 확인할 수 있게 한다.
    """

    name: str
    document_id: str
    fields: list[SchemaFieldItem]


class TagItem(TypedDict):
    """list_tags 응답의 tags 원소 타입."""

    name: str
    endpoint_count: int


class TagListResult(TypedDict):
    """list_tags 의 반환 타입."""

    tags: list[TagItem]


class MatchedChunkPayload(TypedDict):
    """search_documents 결과 한 건의 `matched_chunks` 원소(57번 리뷰 §5 개선1).

    어느 arm(keyword/vector/both)이 어떤 본문 조각으로 이 문서를 뽑았는지를
    담는다. `arm="both"` 는 keyword/vector 승자 청크가 같은 청크였다는 뜻이다.
    """

    chunk_id: str
    text: str
    chunk_type: str
    arm: Literal["keyword", "vector", "both"]


class DocumentSearchItemPayload(TypedDict):
    """search_documents 가 반환하는 결과 한 건.

    score 는 기본 indexed 전략에서는 RRF 점수(순서만 유의미)이고, 롤백
    스위치인 fetch 전략에서는 제목·본문 매칭을 합산한 [0,1] 가중합이다 —
    두 전략 간 score 절대값은 비교 불가하다.

    `matched_chunks`: 어느 arm 이 어떤 본문 조각으로 이 문서를 뽑았는지.
    비어 있으면 본문 근거가 없다(제목 매칭만).
    `match_reasons`: 사람이 읽는 근거 문자열 목록(순서 고정, 값은 모듈
    상수라 LLM 이 안정적으로 파싱할 수 있다).
    `modified_at`: 원본 시스템 기준 최종 수정 시각(최신성 판단용).
    `indexed`: False 면 이 문서의 본문이 아직 색인되지 않아 **제목 매칭
    만으로** 걸린 결과라는 뜻이다 — 본문 근거가 전혀 없으므로, 원문 확인이
    필요하면 get_document 로 이어가야 한다.
    """

    title: str
    source: Literal["drive", "notion"]
    project: str
    url: str
    snippet: str
    score: float
    version: str | None
    snippet_as_of: str | None
    #: get_document(source, external_id) 에 그대로 넘기는 값(45번 리뷰 §3.1).
    external_id: str
    matched_chunks: list[MatchedChunkPayload]
    match_reasons: list[str]
    modified_at: str | None
    indexed: bool
    #: 출처 시스템의 MIME 타입(Drive 전용). Notion·백필 전 Drive 문서는 None.
    mime_type: str | None


class DocumentSearchResponse(TypedDict):
    """search_documents 의 반환 타입."""

    items: list[DocumentSearchItemPayload]


class DocumentContentPayload(TypedDict):
    """get_document 의 반환 타입(fetch 시점의 최신 원문).

    title/url 은 메타 캐시에 있으면 그 값, **메타 캐시에 없으면 둘 다 `""`**
    다(식별자 기반 기본값이 아니다). `""`은 "이 문서의 메타데이터가 서버에
    캐시돼 있지 않다"는 뜻일 뿐이며, content 는 이 경우에도 항상 fetch
    시점의 authoritative 최신 원문이다 — content 유무와 메타 유무는 독립이다.
    """

    title: str
    source: Literal["drive", "notion"]
    url: str
    content: str
    version: str | None
    truncated: bool


class RegisteredResyncResult(TypedDict):
    """include_registered=True 일 때 URL 기반 Document 재동기화 집계."""

    total: int
    reindexed: int
    skipped: int
    failed: list[str]


class RefreshCoverage(TypedDict):
    """refresh_index 응답의 `coverage` 하위 dict(개선 #5).

    `synced`/`added`/`updated`/`removed` 가 **이번 실행의 변화량**인 것과
    달리, 이 값들은 **갱신 대상 범위의 현재 상태**라 중첩해서 분리한다.

    `unindexed`: 본문 색인이 없는데(document_id NULL) 지원 MIME 이라 조치가
    필요한 문서 수. `unsupported`: 텍스트 추출이 불가한 MIME 이라 fetch 자체를
    건너뛴 문서 수(정상). 둘은 서로소다.
    `listing_truncated`: 탐색 상한(Drive MAX_FOLDERS/Notion MAX_PAGES)에
    걸려 목록이 잘린 "<project>/<source>" 목록.
    """

    unindexed: int
    unsupported: int
    listing_truncated: list[str]


class RefreshIndexResult(TypedDict):
    """refresh_index 의 반환 타입.

    `failed_sources` 는 부분 실패한 소스 이름 목록이다. 비어 있으면 전부 성공.
    `coverage` 는 항상 존재한다(개선 #5, 하위호환 분기 없음 — 키 추가는
    MCP 클라이언트에 파괴적이지 않다).
    `registered` 는 include_registered=True 로 호출했을 때만 키가 존재한다.
    """

    synced: int
    added: int
    updated: int
    removed: int
    failed_sources: list[str]
    coverage: RefreshCoverage
    registered: NotRequired[RegisteredResyncResult]


class DriveSourceItem(TypedDict):
    """list_drive_sources 가 반환하는 매핑 한 건."""

    project: str
    folder_id: str
    created_at: str
    updated_at: str


class NotionSourceItem(TypedDict):
    """list_notion_sources 가 반환하는 매핑 한 건.

    `kind` 가 `"page"` 이면 `database_id` 필드에는 page_id 가 담긴다(값
    컬럼을 database/page 가 공유).
    """

    project: str
    database_id: str
    kind: Literal["database", "page"]
    created_at: str
    updated_at: str


class DriveSourceListResult(TypedDict):
    """list_drive_sources 의 반환 타입."""

    items: list[DriveSourceItem]


class NotionSourceListResult(TypedDict):
    """list_notion_sources 의 반환 타입."""

    items: list[NotionSourceItem]


class RegisterDriveSourceResult(TypedDict):
    """register_drive_source 의 반환 타입."""

    project: str
    folder_id: str
    status: Literal["created", "updated"]


class RegisterNotionSourceResult(TypedDict):
    """register_notion_source 의 반환 타입."""

    project: str
    database_id: str
    status: Literal["created", "updated"]


class RegisterNotionPageResult(TypedDict):
    """register_notion_page 의 반환 타입."""

    project: str
    page_id: str
    status: Literal["created", "updated"]


class RemoveDriveSourceResult(TypedDict):
    """remove_drive_source 의 반환 타입."""

    project: str
    removed: bool


class RemoveNotionSourceResult(TypedDict):
    """remove_notion_source 의 반환 타입."""

    project: str
    removed: bool
