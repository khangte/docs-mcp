# 48. 발표용 아키텍처 다이어그램 검토

- 대상: 사용자가 제시한 발표 슬라이드용 아키텍처 그림(원본 이미지는 첨부 형태로만 존재, 파일 없음)
- 검토 기준: `ARCHITECTURE.md` §2·§5·§6, `app/mcp/tools/`, `app/services/documents/`, `app/scripts/refresh_documents.py`, `docs/architecture-overview.html` "전체 그림"
- 판정: **수정 필요** (구조 오류 1건, 표기 오류 2건, 누락 4건)

## 1. 검토한 그림의 구성

말로 옮긴 원본 구성은 다음과 같다.

1. 사용자 → 클러스터/서버 아이콘
2. 클러스터/서버 아이콘 → 번개(⚡) 아이콘
3. 번개 → **점선** → 세 노드(레이더 형태의 분석 아이콘, Google Drive, Notion)
4. 세 노드 → **회색 화살표** → PostgreSQL(코끼리)로 합류
5. 번개 → **실선** → PostgreSQL 직결

## 2. 실제 데이터 흐름 (코드 확인 결과)

- MCP 클라이언트(Claude Desktop/Code)가 stdio 로 `docs-mcp` 서버 프로세스를 띄우고 도구를 호출한다. 서버는 클라이언트 세션마다 뜨고 지는 단명 프로세스이며, 상시 기동되는 클러스터가 아니다(`ARCHITECTURE.md` §5).
- 서버는 답변 문장을 만들지 않는다. 근거 문서(후보)를 돌려주고 최종 답변은 호출 LLM 이 만든다(`README.md` 도입부, ADR-0003).
- 외부 소스는 세 종류이고 성격이 다르다.
  - Google Drive: 폴더 매핑(`register_drive_source`)
  - Notion: DB/허브 페이지 매핑(`register_notion_source`, `register_notion_page`)
  - OpenAPI/Swagger 명세: URL 또는 원문 등록(`register_document`)
- 수집 경로: 외부 소스 → **서버(수집·파싱·섹션 분할·임베딩)** → PostgreSQL. 외부 소스가 DB 에 직접 쓰는 경로는 존재하지 않는다(`app/services/ingestor/`, `app/services/indexer/`, `app/services/documents/document_index_service.py`).
- 임베딩은 서버 안에서 로컬 CPU 모델(`sentence-transformers`, 기본 `intfloat/multilingual-e5-small`)로 만든다. 실패 시 `HashEmbeddingProvider` 폴백(ADR-0004).
- 주기 갱신은 서버 안의 스케줄러가 아니라 OS 스케줄러(systemd timer/cron)가 원샷 CLI `app/scripts/refresh_documents.py` 를 돌리는 구조다. 축 A(메타 1시간)·축 B(등록 문서 재색인 1일)·축 C(본문 색인)로 나뉜다.
- 검색(기본 `indexed` 전략)은 PostgreSQL 안에서만 끝난다 — 제목/키워드(FTS) + 벡터(pgvector, HNSW) 3-arm RRF. 외부 API 를 호출하지 않는다.
- 다만 외부 실시간 접근 경로가 **두 군데** 실재한다.
  - `get_document`: 본문을 캐시하지 않고 호출 시점에 원본을 fetch (`app/mcp/tools/documents.py:167`)
  - `fetch` degrade 전략: 후보를 DB 메타에서 고른 뒤 본문만 실시간 fetch (`document_search_service.py` `_rank_with_body`)

## 3. 지적 사항

### C1 (구조 오류) 외부 소스 → PostgreSQL 직결 화살표

그림에서 Drive/Notion/분석 아이콘이 회색 화살표로 곧장 PostgreSQL 로 합류한다. 실제로는 그런 경로가 없다. 세 소스의 데이터는 전부 서버를 거쳐 파싱·섹션 분할·임베딩을 받은 뒤에야 DB 에 들어간다. 지금 그림은 이 프로젝트에서 가장 손이 많이 간 부분(색인 파이프라인)을 지워 버리고, 마치 외부 SaaS 가 DB 에 직접 적재하는 것처럼 읽힌다.

수정: 소스에서 나온 화살표를 서버로 되돌리고, 서버에서 PostgreSQL 로 가는 화살표 하나에 "파싱 → 섹션 분할 → 임베딩 → 저장" 을 라벨로 건다.

### C2 (표기 오류) 사용자와 서버 사이의 "클러스터/서버 아이콘"

이 자리는 Claude Desktop/Code 같은 **MCP 클라이언트**다. 클러스터 아이콘은 규모/이중화를 암시해 실제(로컬 stdio 단명 프로세스)와 어긋난다. 발표에서 "서버가 두 개인가요?" 라는 질문을 부른다.

수정: 해당 노드를 "Claude · MCP 클라이언트"로 라벨링하고 아이콘도 사람/채팅 계열로 바꾼다. 함께 "서버는 답을 쓰지 않는다 — 근거 문서만 건넨다"를 한 줄 캡션으로 붙이면 프로젝트의 핵심 주장이 그림 한 장에서 전달된다.

### C3 (표기 오류) 레이더 형태의 분석 아이콘

세 소스 중 하나가 OpenAPI/Swagger 명세를 뜻하는 것으로 보이는데, 레이더/분석 아이콘은 "모니터링" 또는 "검색 엔진"으로 읽힌다. Drive·Notion 은 로고로 즉시 식별되는데 세 번째만 정체 불명이면 시선이 거기서 멈춘다.

수정: "API 명세 (OpenAPI/Swagger)" 텍스트 라벨을 붙이고 문서/코드 계열 아이콘을 쓴다.

### M1 (누락) 점선·실선·회색 화살표의 범례

선이 세 종류인데 범례가 없다. 관객은 점선을 "선택적 경로"로도 "비동기"로도 읽는다.

수정: 범례 두 줄로 줄인다 — 실선 = 항상 도는 경로, 점선 = 갱신/실시간 조회 시에만.

### M2 (누락) 주기 동기화(배치) 경로

"사람이 챙기지 않아도 최신"은 이 프로젝트의 셀링 포인트인데(`docs/architecture-overview.html` 지표 "0"), 그림에 스케줄러가 없다. OS 스케줄러 → `refresh_documents.py` → (서버와 같은 서비스 함수) → 소스 fetch → DB 갱신 흐름이 빠져 있으면, 관객은 사용자가 도구를 호출할 때만 데이터가 들어오는 것으로 이해한다.

수정: 오른쪽 위에 "OS 스케줄러(systemd/cron)" 박스를 하나 두고 서버와 같은 서비스 계층으로 점선을 넣는다. 슬라이드가 빡빡하면 서버 박스 안에 "정해진 주기로 자동 재색인" 한 줄로 축약해도 된다.

### M3 (누락) 임베딩 단계

벡터가 어디서 생기는지 그림에 없다. PostgreSQL 이 알아서 벡터를 만드는 것으로 오해될 수 있다. 로컬 CPU 임베딩(외부 임베딩 API 비용 0)은 발표에서 유리한 결정이므로 오히려 드러내는 편이 낫다.

수정: 서버 박스 안에 "임베딩(로컬 CPU 모델)" 칸을 넣는다.

### M4 (누락) PostgreSQL 이 두 역할을 겸한다는 사실

지금 그림에서 PostgreSQL 은 그냥 저장소로 보인다. 실제로는 키워드(FTS)와 벡터(pgvector) 검색을 한 DB 가 함께 맡는 것이 설계 결정(ADR-0002)이고, 별도 검색 엔진·벡터 전용 저장소를 두지 않았다는 점이 이 그림의 하이라이트가 될 수 있다.

수정: PostgreSQL 박스 안에 "FTS(키워드) + pgvector(의미) — RRF 로 순위 융합" 한 줄.

### N1 (참고) 실시간 조회 경로가 점선의 진짜 근거다

점선을 살릴 거라면 그 라벨은 "수집"이 아니라 "원문 실시간 조회(`get_document`) / 갱신 시 fetch" 가 맞다. 기본 검색은 DB 안에서 끝나므로, 점선이 매 질의마다 도는 것처럼 보이면 지연·외부 의존성에 대한 오해를 부른다.

## 4. 권고

`docs/architecture-overview.html` 의 "전체 그림"이 이미 같은 내용을 두 갈래(문서를 **쌓는 길** / 질문에 **답하는 길**)로 정리해 두었고, 이 구성이 위 지적을 전부 해소한다. 발표 슬라이드는 새로 그리기보다 그 구성을 그대로 옮기는 편을 권한다.

최소 수정안(현 그림 유지 시):

```
 사람 ─▶ Claude · MCP 클라이언트 ─▶ [docs-mcp 서버]
                                       │  파싱 · 섹션 분할 · 임베딩(로컬 CPU)
                                       │
              ┌────────── 점선(갱신 / 실시간 조회) ──────────┐
              ▼                                              │
   Google Drive · Notion · API 명세(OpenAPI) ────────────────┘
                                       │
                                       ▼ 실선(저장 · 검색)
                        PostgreSQL + pgvector
                        FTS(키워드) + 벡터, RRF 순위 융합
```

## 5. 조치

- 발표자료 수정은 designer/lead 판단 영역이므로 developer 수정 지시는 내지 않는다.
- 코드·문서 변경은 불필요하다. 현재 구현이 문서와 일치하며, 어긋난 것은 그림뿐이다.
