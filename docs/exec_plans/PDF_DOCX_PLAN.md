# PDF/DOCX 파싱 지원 설계안 (리포트 1단계)

- 대상: docs-mcp 문서 등록 경로에 `pdf`/`docx` doc_type 추가.
- 원칙(ponytail): 파서 계층은 이미 `str → ParsedDocument` 다. **바이너리를 텍스트로 먼저 추출한 뒤 기존 흐름에 얹으면**, `register()`·`content_hash`·`raw_text`·`resync`·indexer 를 전혀 손대지 않는다.

## 핵심 결정

**결정 1 — 추출 지점: `parse_document` 진입 직전, 라우터에서 새 doc_type 으로 분기.**
- `sync_service.register()`(`app/services/ingestor/sync_service.py:88-90`)의 `raw:str` 흐름은 그대로 두되, PDF/DOCX 는 `raw` 가 **base64 문자열**로 도착한다. 라우터가 `pdf`/`docx` 분기에서 base64 디코드 → bytes → 텍스트 추출 → 기존 `ParsedDocument`(단일 또는 헤딩 섹션) 반환.
- 파서 안에서 bytes 를 직접 받게 하지 않는 이유: `parse_document(raw:str, ...)` 시그니처(4곳 호출)와 markdown/csv/openapi 파서의 `str` 계약을 깨지 않기 위함. base64 문자열도 `str` 이므로 시그니처 불변.

**결정 2 — 입력 채널: MCP 는 base64 (`raw_document`), URL fetch 는 이번 스코프 제외.**
- `register_document(raw_document:str)` 가 이미 `str` 이므로 base64 를 그 필드로 받으면 **MCP 도구 시그니처·`register()` 시그니처 무변경**. `doc_type="pdf"|"docx"` 를 명시하면 라우터가 base64 로 해석.
- URL(.pdf/.docx) fetch 는 fetcher 가 `str` 만 반환(`openapi_fetcher.py:19,31`)하므로 bytes 반환 오버로드가 필요 → 디프가 커진다. YAGNI, 후속 단계로 미룸. `detect_doc_type` 의 URL 확장자 감지에 pdf/docx 도 안 넣는다(URL 경로 미지원이므로).

**결정 3 — `content_hash`/`raw_text` 는 추출한 텍스트 기준.**
- `content_hash=_hash(raw)`, `raw_text=raw` 를 **추출 후 텍스트**로 저장(원본 바이트 아님). 근거: (a) `resync` 가 `document.raw_text` 를 그대로 재파싱하는데(`sync_service.py:149,176`), 여기 텍스트가 있어야 재색인이 성립. (b) 청크/검색 대상은 텍스트지 바이트가 아니다. (c) 바이트 해시로 두면 라이브러리 버전에 따라 추출 결과가 달라져도 해시가 안 변해 skip 되는 함정.
- 부작용: 라우터가 추출 텍스트를 `register()` 로 되돌려줘야 함 → **`parse_document` 가 (ParsedDocument, 추출텍스트) 를 주는 형태로 확장**하거나, 라우터에 별도 `extract_text(raw, doc_type) -> str` 를 두고 `register()` 가 pdf/docx 일 때 이를 먼저 호출해 `raw` 를 텍스트로 치환. 후자가 기존 파서 반환형 불변이라 더 작은 디프 → **`extract_text` 선(先)추출 방식 채택.**

**결정 4 — 라이브러리: PDF=`pypdf`, DOCX=`python-docx`(둘 다 순수 파이썬).**
- `pyproject.toml` 의존성 2개 추가. 추출 실패(암호화 PDF·손상 파일)는 기존 `ParserError` 로 변환(빈 텍스트면 markdown 파서와 동일하게 `ParserError("empty document")` 계열).

## 변경 지점 (최소 디프)

1. `app/services/ingestor/sync_service.py:88` 부근 — `resolved_doc_type` 확정 후, pdf/docx 이면 `raw = extract_text(raw, resolved_doc_type)` 로 치환(base64→텍스트). 이후 라인(`content_hash=_hash(raw)`, `raw_text=raw`) 전부 자동으로 텍스트 기준이 됨. **그 외 register 본문 무변경.**
2. `app/services/parser/document_router.py` — `_KNOWN_TYPES` 에 `"pdf","docx"` 추가. `parse_document` 에 두 분기 추가(추출 텍스트를 markdown 파서로 재사용해 섹션화, 또는 단일 "본문" 섹션). 신규 `extract_text(raw_b64:str, doc_type:str) -> str` 공개 함수 추가.
3. `pyproject.toml` — `pypdf`, `python-docx` 추가.

`detect_doc_type` 은 손대지 않는다(pdf/docx 는 doc_type 명시 필수 — base64 는 내용 스니핑이 불가하므로 명시 요구가 자연스럽다). MCP `register_document` docstring 에 "pdf/docx 는 base64 인코딩 + `doc_type` 명시" 한 줄만 보강.

## developer 가 만들/수정할 파일

- 신규 `app/services/parser/pdf_parser.py` — `extract_text(data:bytes)->str` (pypdf, 페이지 텍스트 이어붙임).
- 신규 `app/services/parser/docx_parser.py` — `extract_text(data:bytes)->str` (python-docx, 문단 이어붙임).
- 수정 `app/services/parser/document_router.py` — `_KNOWN_TYPES` 확장, `extract_text(raw_b64, doc_type)` 추가(base64 디코드 → 위 두 추출기 위임), `parse_document` 의 pdf/docx 분기(추출 텍스트를 markdown 파서로 섹션화 재사용).
- 수정 `app/services/ingestor/sync_service.py` — register 에서 pdf/docx 일 때 base64→텍스트 선추출 1줄.
- 수정 `pyproject.toml` — 의존성 2개.
- 신규 테스트 `tests/unit/test_pdf_docx_parser.py` — 작은 pdf/docx 바이트 fixture 로 추출·섹션화, 잘못된 base64/암호화 PDF 시 ParserError, register 통합(base64 raw_document + doc_type 지정 → 텍스트 raw_text 저장·재색인).

## 미스코프(후속)
- URL(.pdf/.docx) 직접 fetch(fetcher bytes 오버로드 필요).
- 표/이미지 OCR, 레이아웃 보존.
