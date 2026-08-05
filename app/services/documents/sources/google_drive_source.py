"""Google Drive 문서 소스 어댑터 (SPEC 기능 5).

인증은 서비스 계정 1개 고정이며, 검색 대상 폴더를 서비스 계정 이메일에
"뷰어로 공유"해두는 방식으로 접근 권한을 부여한다. Drive REST API 는 httpx 로
직접 호출한다(`google-api-python-client` 미도입). 서비스 계정 JWT 서명·토큰
발급에만 `google-auth` 를 사용한다.

폴더 범위는 `DOCS_MCP_DRIVE_FOLDER_ID` 하나로 고정하고, 하위 폴더까지 너비
우선으로 재귀 탐색한다.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import httpx

from app.core.errors import IntegrationError, ParserError
from app.core.logging import get_logger
from app.models.document_meta import SOURCE_DRIVE
from app.services.documents.sources.document_source import FileMeta
from app.services.documents.sources.time_parsing import parse_rfc3339
from app.services.parser import docx_parser, pdf_parser, pptx_parser, xlsx_parser

_LOG = get_logger("docs_mcp.documents.drive")

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
#: Google 네이티브 문서(Docs/Sheets/Slides)는 export API 로 평문 변환해야 한다.
GOOGLE_NATIVE_MIME_PREFIX = "application/vnd.google-apps."
#: 네이티브 타입별 텍스트 export 포맷. Drive export API 는 소스 타입마다
#: 지원하는 변환 포맷이 다르다 — Sheets 를 text/plain 으로 요청하면 400
#: (The requested conversion is not supported)이 난다. fetch() 가 이미
#: 조회한 mimeType 으로 여기서 사전에 올바른 포맷을 정하므로, "우선
#: text/plain 시도 후 실패하면 재시도"하는 순차 폴백으로 인한 Sheets 마다의
#: 불필요한 400 왕복이 없다. 매핑에 없는 네이티브 타입(그림/폼 등)은 텍스트
#: export 를 지원하지 않으므로 export 호출 자체를 하지 않고 바로 실패한다.
NATIVE_EXPORT_MIME_TYPES: dict[str, str] = {
    GOOGLE_DOC_MIME_TYPE: "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
#: alt=media 로 다운로드한 뒤 텍스트를 추출할 바이너리 포맷. 파서는
#: app/services/parser/ 의 기존 구현(PDF/DOCX 는 ingestor 경로에서 이미
#: 검증됨, XLSX/PPTX 는 같은 패턴으로 신규 작성)을 재사용한다. 매핑에 없는
#: 바이너리(이미지/영상 등)는 텍스트 추출을 지원하지 않으므로 명확히
#: 실패시킨다(Sheets export MIME 미지원 처리와 같은 원칙 — 깨진 바이트를
#: 텍스트인 척 반환하지 않는다).
BINARY_TEXT_EXTRACTORS = {
    PDF_MIME_TYPE: pdf_parser.extract_text,
    DOCX_MIME_TYPE: docx_parser.extract_text,
    XLSX_MIME_TYPE: xlsx_parser.extract_text,
    PPTX_MIME_TYPE: pptx_parser.extract_text,
}
#: Drive files.list 한 페이지 최대 개수(API 상한 1000).
PAGE_SIZE = 200
#: 무한 루프 방지를 위한 폴더 탐색 상한.
MAX_FOLDERS = 500
_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)"


class ServiceAccountTokenProvider:
    """서비스 계정 자격증명으로 Drive API 액세스 토큰을 발급/갱신한다.

    `google.oauth2.service_account.Credentials` 가 만료 여부를 스스로 관리하므로
    이 클래스는 "필요할 때만 refresh" 정책만 담당한다.
    """

    def __init__(
        self,
        service_account_file: str | None = None,
        service_account_json: str | None = None,
        scopes: tuple[str, ...] = DRIVE_SCOPES,
    ) -> None:
        """자격증명 출처(파일 경로 또는 JSON 문자열)와 스코프를 보관한다.

        Args:
            service_account_file: 서비스 계정 키 파일 경로.
            service_account_json: 서비스 계정 키 JSON 문자열(파일보다 우선).
            scopes: 요청할 OAuth 스코프.

        Raises:
            IntegrationError: 두 자격증명 출처가 모두 비어 있는 경우.
        """
        if not service_account_file and not service_account_json:
            raise IntegrationError(
                "google drive credentials are not configured: set "
                "DOCS_MCP_DRIVE_SERVICE_ACCOUNT_FILE or DOCS_MCP_DRIVE_SERVICE_ACCOUNT_JSON"
            )
        self._service_account_file = service_account_file
        self._service_account_json = service_account_json
        self._scopes = list(scopes)
        self._credentials: Any | None = None

    def token(self) -> str:
        """유효한 액세스 토큰 문자열을 반환한다(만료 시 자동 갱신).

        Raises:
            IntegrationError: 키 파싱 실패나 토큰 발급 실패 시.
        """
        credentials = self._load_credentials()
        if not credentials.valid:
            self._refresh(credentials)
        token = credentials.token
        if not token:
            raise IntegrationError("google drive authentication failed: empty access token")
        return str(token)

    def _load_credentials(self) -> Any:
        """서비스 계정 자격증명 객체를 만들어 캐싱한다."""
        if self._credentials is not None:
            return self._credentials
        # 지연 import: Drive 를 쓰지 않는 환경에서 의존성 로딩 비용을 피한다.
        from google.oauth2 import service_account

        try:
            if self._service_account_json:
                info = json.loads(self._service_account_json)
                credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=self._scopes
                )
            else:
                credentials = service_account.Credentials.from_service_account_file(
                    str(self._service_account_file), scopes=self._scopes
                )
        except (json.JSONDecodeError, ValueError, OSError, KeyError) as exc:
            raise IntegrationError(f"invalid google service account credentials: {exc}") from exc
        self._credentials = credentials
        return credentials

    def _refresh(self, credentials: Any) -> None:
        """액세스 토큰을 갱신하고 실패를 IntegrationError 로 변환한다."""
        from google.auth.transport.requests import Request as GoogleAuthRequest

        try:
            credentials.refresh(GoogleAuthRequest())
        except Exception as exc:  # google-auth 는 다양한 예외를 던진다
            raise IntegrationError(f"google drive authentication failed: {exc}") from exc


class GoogleDriveSource:
    """Google Drive 폴더 1개(하위 포함)를 대상으로 목록/본문을 조회하는 어댑터."""

    def __init__(
        self,
        folder_id: str,
        token_provider: ServiceAccountTokenProvider,
        timeout_seconds: float = 15.0,
        max_chars: int = 200_000,
        max_download_bytes: int = 50_000_000,
        api_base: str = DRIVE_API_BASE,
    ) -> None:
        """대상 폴더·토큰 발급기·HTTP 옵션을 보관한다.

        Args:
            folder_id: 검색 범위로 고정할 Drive 폴더 ID.
            token_provider: 액세스 토큰 발급기.
            timeout_seconds: HTTP 타임아웃.
            max_chars: 본문 fetch 시 잘라낼 최대 문자 수(과대 응답 방지).
            max_download_bytes: PDF/DOCX/XLSX/PPTX 바이너리 다운로드 크기
                상한. `max_chars` 는 텍스트 추출 *이후* 자르는 값이라
                수백MB 짜리 파일이면 파싱 자체가 메모리를 크게 잡아먹는다
                — 파싱 진입 전에 크기를 검사해 과대 파일의 파싱을
                차단한다(응답은 이미 `response.content` 로 전부 받은
                뒤이므로 진짜 스트리밍 중단은 아니다).
            api_base: Drive REST API 베이스 URL(테스트에서 교체 가능).

        Raises:
            IntegrationError: folder_id 가 비어 있는 경우.
        """
        if not folder_id:
            raise IntegrationError(
                "google drive folder is not configured: set DOCS_MCP_DRIVE_FOLDER_ID"
            )
        self._folder_id = folder_id
        self._token_provider = token_provider
        self._timeout = timeout_seconds
        self._max_chars = max_chars
        self._max_download_bytes = max_download_bytes
        self._api_base = api_base.rstrip("/")

    @property
    def source_name(self) -> str:
        """`document_meta.source` 에 기록할 소스 식별자."""
        return SOURCE_DRIVE

    def list_files(self) -> list[FileMeta]:
        """대상 폴더와 그 하위 폴더 안의 파일 메타데이터를 모두 반환한다.

        폴더 자체는 결과에 포함하지 않고 탐색 큐에만 넣는다.

        Raises:
            IntegrationError: 인증 실패·rate limit·네트워크 오류 시.
        """
        collected: list[FileMeta] = []
        pending: deque[str] = deque([self._folder_id])
        visited: set[str] = {self._folder_id}

        with self._client() as client:
            while pending and len(visited) <= MAX_FOLDERS:
                folder_id = pending.popleft()
                for raw in self._list_folder_children(client, folder_id):
                    if raw.get("mimeType") == FOLDER_MIME_TYPE:
                        child_id = str(raw.get("id") or "")
                        if child_id and child_id not in visited:
                            visited.add(child_id)
                            pending.append(child_id)
                        continue
                    collected.append(_to_file_meta(raw))

        if pending:
            _LOG.warning(
                "drive 폴더 탐색 상한(%d)에 도달해 일부 하위 폴더를 건너뜀", MAX_FOLDERS
            )
        return collected

    def fetch(self, external_id: str) -> str:
        """Drive 파일 본문을 평문으로 반환한다.

        Google 네이티브 문서(Docs/Sheets/Slides)는 export API 로 평문
        변환해 가져온다(`NATIVE_EXPORT_MIME_TYPES` 매핑). 그 밖의 파일은
        `alt=media` 로 다운로드하는데, 이후 처리는 mimeType 에 따라
        갈린다:

        - `text/*` (txt/md 등): 응답을 그대로 텍스트로 쓴다.
        - PDF/DOCX/XLSX/PPTX (`BINARY_TEXT_EXTRACTORS`): 바이트를 받아
          기존 파서(ingestor 경로에서 이미 검증된 것과 동일 구현)로 텍스트를
          추출한다.
        - 그 외(이미지/영상 등): 텍스트로 변환할 방법이 없으므로 깨진
          바이트를 텍스트인 척 반환하지 않고 명확히 실패시킨다.

        Args:
            external_id: Drive file ID.

        Returns:
            평문 텍스트(설정된 최대 문자 수로 잘림).

        Raises:
            IntegrationError: 파일이 없거나 외부 연동에 실패한 경우, 텍스트
                추출을 지원하지 않는 형식인 경우, 또는 다운로드 크기가
                상한을 넘는 경우.
        """
        if not external_id:
            raise IntegrationError("drive file id must not be empty")

        with self._client() as client:
            metadata = self._request_json(
                client, "GET", f"/files/{external_id}", params={"fields": "id, mimeType"}
            )
            mime_type = str(metadata.get("mimeType") or "")
            if mime_type.startswith(GOOGLE_NATIVE_MIME_PREFIX):
                text = self._fetch_native_export(client, external_id, mime_type)
            elif mime_type.startswith("text/"):
                text = self._request_text(
                    client, f"/files/{external_id}", params={"alt": "media"}
                )
            else:
                text = self._fetch_binary_text(client, external_id, mime_type)
        return text[: self._max_chars]

    def _fetch_native_export(
        self, client: httpx.Client, external_id: str, mime_type: str
    ) -> str:
        """Google 네이티브 문서를 export API 로 평문 변환해 가져온다."""
        export_mime_type = NATIVE_EXPORT_MIME_TYPES.get(mime_type)
        if export_mime_type is None:
            raise IntegrationError(
                f"google drive file {external_id} has unsupported native type "
                f"for text export: {mime_type}"
            )
        return self._request_text(
            client, f"/files/{external_id}/export", params={"mimeType": export_mime_type}
        )

    def _fetch_binary_text(self, client: httpx.Client, external_id: str, mime_type: str) -> str:
        """PDF/DOCX/XLSX/PPTX 를 다운로드해 기존 파서로 텍스트를 추출한다."""
        extractor = BINARY_TEXT_EXTRACTORS.get(mime_type)
        if extractor is None:
            raise IntegrationError(
                f"google drive file {external_id} has unsupported binary type "
                f"for text extraction: {mime_type}"
            )
        data = self._request_bytes(client, f"/files/{external_id}", params={"alt": "media"})
        if len(data) > self._max_download_bytes:
            raise IntegrationError(
                f"google drive file {external_id} exceeds download size limit "
                f"({len(data)} > {self._max_download_bytes} bytes)"
            )
        try:
            return extractor(data)
        except ParserError as exc:
            raise IntegrationError(
                f"google drive file {external_id} failed to parse as {mime_type}: {exc}"
            ) from exc

    # --- 내부 HTTP 헬퍼 ---------------------------------------------------

    def _client(self) -> httpx.Client:
        """인증 헤더가 붙은 httpx 클라이언트를 만든다(사용 후 반드시 close)."""
        headers = {"Authorization": f"Bearer {self._token_provider.token()}"}
        return httpx.Client(base_url=self._api_base, headers=headers, timeout=self._timeout)

    def _list_folder_children(
        self, client: httpx.Client, folder_id: str
    ) -> list[dict[str, Any]]:
        """폴더 직계 자식 목록을 페이지네이션까지 처리해 모두 반환한다."""
        children: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": _LIST_FIELDS,
                "pageSize": PAGE_SIZE,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._request_json(client, "GET", "/files", params=params)
            children.extend(payload.get("files") or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                return children

    def _request_json(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Drive API 를 호출해 JSON 본문을 dict 로 반환한다."""
        response = self._send(client, method, path, params)
        try:
            return dict(response.json())
        except ValueError as exc:
            raise IntegrationError(f"google drive returned malformed json for {path}") from exc

    def _request_text(
        self, client: httpx.Client, path: str, params: dict[str, Any] | None = None
    ) -> str:
        """Drive API 를 호출해 본문을 텍스트로 반환한다."""
        return self._send(client, "GET", path, params).text

    def _request_bytes(
        self, client: httpx.Client, path: str, params: dict[str, Any] | None = None
    ) -> bytes:
        """Drive API 를 호출해 본문을 원시 바이트로 반환한다(바이너리 파싱용)."""
        return self._send(client, "GET", path, params).content

    def _send(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        """HTTP 요청을 보내고 오류를 IntegrationError 로 통일 변환한다."""
        try:
            response = client.request(method, path, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise IntegrationError(_drive_error_message(path, exc.response)) from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(f"google drive request failed for {path}: {exc}") from exc


def _drive_error_message(path: str, response: httpx.Response) -> str:
    """Drive API 오류 상태코드를 사용자에게 보여줄 메시지로 바꾼다."""
    status = response.status_code
    if status in (401, 403):
        return (
            f"google drive access denied for {path} (status {status}): "
            "check the service account credentials and folder sharing"
        )
    if status == 404:
        return f"google drive document not found: {path}"
    if status == 429:
        return "google drive rate limit exceeded; retry later"
    return f"google drive request failed for {path} (status {status})"


def _to_file_meta(raw: dict[str, Any]) -> FileMeta:
    """Drive files.list 응답 항목 하나를 FileMeta 로 변환한다."""
    file_id = str(raw.get("id") or "")
    return FileMeta(
        external_id=file_id,
        title=str(raw.get("name") or "").strip(),
        url=str(raw.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"),
        modified_at=parse_rfc3339(raw.get("modifiedTime")),
    )
