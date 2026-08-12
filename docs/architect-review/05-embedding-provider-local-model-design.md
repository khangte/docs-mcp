# 임베딩 프로바이더 교체 설계 — Gemini API 제거 → 로컬 CPU 모델

- 상태: 구현 완료(커밋 `be774dd`) — 아래 착수 순서 1~7단계 전부 반영됨
- 일시: 2026-08-08
- 작성: architect
- 지시: lead(무료·CPU·다국어 경량 임베딩으로 전환, 예시 `intfloat/multilingual-e5-small`)
- 대상: `app/services/indexer/embedding_provider.py`, `app/composition.py`, `app/core/config.py`, `app/models/openapi.py`, `app/services/indexer/indexer_service.py`, `app/services/search/vector_search.py`, `alembic/versions/`, `pyproject.toml`, `.env.example`, 문서/테스트

## 요약(결정 사항)
1. **모델**: `intfloat/multilingual-e5-small`(384차원, CPU, 다국어=한글 포함) 채택. E5 계열은 **query/passage 비대칭 접두사**가 필수라는 점이 유일한 실질 설계 포인트.
2. **Protocol 변경**: 대칭 `embed(texts)` → 비대칭 `embed_documents(texts)` / `embed_query(text)` 로 분리(+ `is_semantic` 능력 플래그).
3. **차원 256 → 384**: pgvector 컬럼 dim 은 생성 시 고정 → 컬럼+HNSW 인덱스 재생성 마이그레이션 + **전체 재임베딩** 필요.
4. **재임베딩**: 마이그레이션은 스키마만. 재임베딩은 **청크 텍스트 순회 전용 배치**(재파싱·네트워크 없음)로 분리, 마이그레이션이 모델을 로드하지 않는다.
5. **배선**: 로컬 모델은 "키 유무" 개념이 없음 → `is_vector_fallback_available` 을 "gemini 키"에서 "provider.is_semantic"으로 재정의. 테스트·모델로드 실패용 폴백으로 `HashEmbeddingProvider` 는 유지.
6. **의존성**: `google-genai` 제거, `sentence-transformers`(+CPU torch) 추가. `google-auth` 는 Drive 용이라 **유지**.

---

## 1. 새 EmbeddingProvider 구현
### 1-1. 모델 선택
- **채택: `intfloat/multilingual-e5-small`** — 384dim, ~118M, CPU 추론 가능, 다국어(한글 포함), 무료. lead/사용자가 지목.
- 대안(참고, 재검토로 시간 끌지 않음): `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`(384dim, 접두사 불필요라 배선이 더 단순). E5 대비 성능 우열은 워크로드마다 다르므로 **지시대로 E5 채택**하되, 접두사 처리가 부담되면 MiniLM 이 드롭인 대안임을 기록.

### 1-2. E5 비대칭 접두사 — 핵심 설계 포인트
E5 는 학습 규약상 **문서(색인 대상)엔 `"passage: "`, 질의엔 `"query: "`** 접두사를 붙여야 유사도가 제대로 나온다. 접두사를 섞거나 빠뜨리면 저장 벡터와 질의 벡터가 같은 공간에서 비교되지 않아 벡터 검색 품질이 무너진다.

현재 `EmbeddingProvider.embed(texts)` 는 **대칭**이라(색인·질의 같은 메서드) 이 구분을 표현 못 한다. → **Protocol 을 비대칭으로 변경**:
```
class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...  # "passage: " 접두
    def embed_query(self, text: str) -> list[float]: ...                   # "query: " 접두
    @property
    def dim(self) -> int: ...
    @property
    def is_semantic(self) -> bool: ...   # 의미 유사도를 갖는가(벡터 fallback 가치 판단용)
```
- `LocalEmbeddingProvider`(신규): 내부에 `SentenceTransformer(model_name, device="cpu")` 보관. `embed_documents` 는 `"passage: "+t`, `embed_query` 는 `"query: "+t` 로 인코딩. `normalize_embeddings=True`(ST 옵션)로 코사인 정렬 정규화 — 기존 `_l2_normalize` 와 동일 효과라 provider 내부에서 처리. `is_semantic=True`.
- `HashEmbeddingProvider`(유지): `embed_documents`/`embed_query` 를 접두사 무시하고 동일 로직으로 구현(결정적). `is_semantic=False`.
- 호출부 변경: `IndexerService.index_document` 는 `embed(texts)` → **`embed_documents(texts)`**(색인=passage). `VectorSearch.search` 는 `embed([query])[0]` → **`embed_query(query)`**(검색=query).
- (`GeminiEmbeddingProvider` 는 삭제. 재시도/에러 처리 로직도 함께 제거.)

## 2. 의존성 교체 (`pyproject.toml`)
- **제거**: `google-genai>=1.47.0`.
- **유지**: `google-auth>=2.30`(Drive 서비스 계정 JWT 서명 — Gemini 와 무관, 절대 지우지 말 것).
- **추가**: `sentence-transformers>=3.0`(transitively `torch`, `transformers`, `huggingface-hub`).
- ⚠️ **설치 무게/오프라인 주의(실질 리스크)**:
  - `torch` CPU 휠은 크다(수백 MB). CUDA 휠이 딸려오지 않게 **CPU 전용 torch** 를 명시해야 한다(uv/pip extra-index `https://download.pytorch.org/whl/cpu` 또는 `torch` CPU 변형 핀). developer 가 설치 방식 확정 필요.
  - **모델 최초 다운로드**: `SentenceTransformer("intfloat/multilingual-e5-small")` 는 최초 실행 시 HuggingFace 에서 **~471MB**(developer 실측 — safetensors 가중치 + tokenizer 등 포함)를 받는다. 오프라인/CI 영향:
    - 캐시 경로 `HF_HOME`/`SENTENCE_TRANSFORMERS_HOME` 를 지정해 컨테이너 빌드시 프리페치하거나 볼륨 마운트.
    - CI·테스트는 모델을 받지 않도록 **기본 폴백을 Hash 로**(5절) 두고, 벡터 스모크 테스트만 opt-in.
    - Docker 이미지 빌드 단계에서 모델을 미리 받아 레이어에 굽는 것을 권장(런타임 콜드스타트·네트워크 의존 제거).

## 3. 차원 변경 256 → 384 (마이그레이션)
현재 dim 이 **두 곳**에 이중화: `app/models/openapi.py:33 EMBEDDING_DIM=256`(컬럼 DDL `Vector(EMBEDDING_DIM)`) + `config.py embedding_dim`(env `DOCS_MCP_EMBEDDING_DIM`, composition 이 provider 에 주입).

- **단일 진실원(SSOT) 정리**: 로컬 모델의 dim 은 **모델이 고정**(384)하므로 독립 튜닝 대상이 아니다. → `DOCS_MCP_EMBEDDING_DIM` env 및 `config.embedding_dim` **제거**, `EMBEDDING_DIM=384` 상수 하나만 DB 컬럼 진실원으로 남긴다. provider 는 `.dim` 을 모델에서 읽고, **부트스트랩에서 `provider.dim == EMBEDDING_DIM` 를 assert**(불일치 시 즉시 실패 — 컬럼과 모델이 어긋난 채 색인되는 사고 방지).
- **pgvector 컬럼 재생성**: Vector 컬럼 dim 은 생성 시 고정이라 in-place 확장이 안전하지 않다. 마이그레이션 순서:
  1. `DROP INDEX ix_api_chunk_embedding_hnsw`(app 스키마).
  2. `DROP COLUMN embedding` → `ADD COLUMN embedding vector(384) NULL`(기존 256 벡터는 전부 무효라 보존 의미 없음).
  3. HNSW 인덱스 재생성(`postgresql_using='hnsw'`, `vector_cosine_ops` — 기존 정의 패턴 그대로).
  - downgrade: 역순으로 `vector(256)` 복원.
  - alembic 리비전 신규 1개. 모델 상수도 384 로 동시 변경(모델·마이그레이션 정합).
- **키워드 검색 영향 없음**: `text_tsv`(FTS/GIN)는 embedding 과 독립 → 재임베딩 진행 중에도 키워드 검색은 정상. 벡터 fallback 만 재임베딩 완료 전까지 비게 된다(graceful degradation).

## 4. 재임베딩 전략
- 마이그레이션 직후 모든 `embedding` 은 NULL → 벡터 검색은 빈 결과. **전체 재임베딩 필요**.
- **마이그레이션 안에서 재임베딩하지 않는다**: alembic 리비전이 ML 모델을 로드/추론하는 것은 부적절(무겁고, 오프라인 실패, 롤백 곤란). 스키마 변경만 담당.
- **전용 재임베딩 배치(신규 진입점)** 권장: 기존 재색인 경로 `SyncService.resync(force=True)` 는 문서를 **재파싱·재fetch**(URL 문서는 네트워크)까지 해서 과하다. dim 변경엔 **본문이 안 바뀌었고 임베딩만 다시** 필요하므로:
  - `app/scripts/reembed.py`(가칭) — 전 `ApiChunk` 를 순회하며 `chunk.embedding = provider.embed_documents([chunk.text])[0]` 로 갱신 후 커밋. **재파싱·네트워크 없음**, 저장된 `chunk.text` 만 사용. 배치 크기로 나눠 커밋.
  - 운영 안내: `alembic upgrade head` → `uv run python -m app.scripts.reembed`. 재임베딩 완료 전까지 벡터 fallback 은 비활성 상태로 동작(키워드는 정상).
- (대안: 문서 원문까지 갱신이 목적이면 문서별 `resync(force=True)` 루프. 이번 목적은 임베딩 재계산뿐이라 청크 순회 배치가 최소·안전.)

## 5. `composition.py` 배선 변경
- `_build_embedding_provider`: gemini 키 분기 제거. 기본은 `LocalEmbeddingProvider(model_name)` 반환. 단 **폴백 유지**:
  - 테스트 환경(무거운 모델 로딩 회피)·모델 로드 실패 시 `HashEmbeddingProvider`. 선택 기준은 명시적 플래그(예: env `DOCS_MCP_EMBEDDING_BACKEND=local|hash`, 기본 `local`) 또는 테스트의 dependency override. 테스트는 override 로 Hash 주입(권장, env 오염 없음).
- `is_vector_fallback_available()`: 판별 기준을 **"gemini 키 유무" → "provider.is_semantic"** 으로 교체. Hash 폴백일 때 벡터 fallback 을 끄는 의도는 그대로 유지되며, 키 개념이 사라진 로컬 모델에 맞는 기준이 된다.
- `AppState.from_engine`/`build_services`: `embedding_dim` 파라미터(기본 256)를 제거(3절 SSOT). `vector_fallback_enabled` 기본 결정도 `is_semantic` 기반으로.
- `bootstrap.py`: `embedding_dim=cfg.embedding_dim` 전달 라인 제거(EMBEDDING_DIM 상수/provider.dim 사용).

## 6. 설정 정리 (`config.py`, `.env.example`)
- **제거**: `gemini_api_key`(`DOCS_MCP_GEMINI_API_KEY`), `gemini_embedding_model`(`DOCS_MCP_GEMINI_EMBEDDING_MODEL`), `embedding_dim`(`DOCS_MCP_EMBEDDING_DIM`).
- **추가(최소)**: `DOCS_MCP_EMBEDDING_MODEL`(기본 `intfloat/multilingual-e5-small`). 모델명은 오프라인 로컬 경로 지정·모델 교체에 실익이 있어 env 로 두는 것을 권장(하드코딩보다 이 한 개는 값어치 있음). 필요 시 `DOCS_MCP_EMBEDDING_BACKEND`(local|hash), 캐시 경로는 표준 `HF_HOME` 사용.
- `.env.example`: Gemini 3줄 삭제, `DOCS_MCP_EMBEDDING_MODEL` 주석과 함께 추가. `DOCS_MCP_HYBRID_ALPHA` 는 이 경로와 무관(별건)이므로 손대지 않음.

## 7. 문서 정리 대상(목록만 — 실제 수정은 developer 단계)
- `README.md`, `ARCHITECTURE.md`: Gemini 언급 → 로컬 모델로 갱신.
- `docs/adr/0002-pgvector-hybrid-search.md`: "임베딩 모델 관리" 서술이 API 전제 → **신규 ADR-0004(임베딩 프로바이더: 관리형 API → 로컬 CPU 모델) 추가**로 결정 이력을 남기고, 0002 는 소급 수정 대신 후속 참조 메모만 권장.
- `.env.example`(6절), `pyproject.toml`(2절).
- `docs/architect-review/07-search-rrf-reevaluation.md`: "Gemini 키 유무"·"HashEmbeddingProvider" 서술을 "로컬 모델 활성 여부"로 갱신(RRF 게이팅 논리 자체는 `is_semantic` 기준으로 그대로 유효).
- `docs/exec_plans/*`(vector-index-persist/SPEC, docs_mcp_expansion/*): 시점 기록물이라 소급 수정하지 않고 그대로 둔다(필요 시 상단 메모만).

## 8. 테스트 영향(교체 범위)
- `tests/unit/test_gemini_embedding_provider.py`: 삭제 → `test_local_embedding_provider.py`(신규)로 대체.
- `tests/unit/test_vector_fallback_availability.py`: gemini 키 기반 → `is_semantic` 기반으로 재작성.
- `tests/unit/test_endpoint_candidate_search.py`: gemini 참조(mock/env) → 새 provider 게이팅으로 수정.
- `tests/conftest.py`: gemini env 셋업 제거, 벡터 경로 테스트가 무거운 모델을 안 받도록 **Hash provider 강제**(override) 정비.
- dim 을 256 으로 단정하는 테스트가 있으면 384 로 갱신.

## 9. 회귀·검증(스모크 테스트 설계)
- **의미 유사도 스모크(opt-in, 실모델)**: `embed_query("로그인")` 과 `embed_documents(["사용자 인증 API"])` 의 코사인이, 무관 쌍 `embed_documents(["결제 취소 처리"])` 보다 **높다**를 assert. 다국어(한글) 동작까지 함께 확인. 모델 로드가 필요하므로 `@slow`/마커로 분리, 기본 CI 는 스킵 가능.
- **접두사 적용 검증(모델 불필요)**: `SentenceTransformer` 를 페이크로 주입해 `embed_documents` 가 `"passage: "`, `embed_query` 가 `"query: "` 접두사를 붙여 인코더에 넘기는지 인자 캡처로 assert(비대칭 규약이 코드로 고정).
- **차원 계약**: `provider.dim == 384 == EMBEDDING_DIM`, 부트스트랩 assert 통과.
- **정규화**: 반환 벡터 L2 노름 ≈ 1(코사인 검색 전제).

---

## 착수 순서(구현 단계)
> ✅ **전 단계 구현 완료(커밋 `be774dd`).** 아래 1~7 단계가 모두 반영·커밋되었다.

1. **의존성**(2절): `google-genai` 제거, `sentence-transformers`+CPU torch 추가, 설치/캐시 방식 확정.
2. **Protocol + LocalEmbeddingProvider**(1절): 비대칭 `embed_documents`/`embed_query`/`is_semantic` 도입, Gemini provider 삭제, Hash provider 를 새 계약에 맞춰 갱신.
3. **차원 SSOT + 마이그레이션**(3절): `EMBEDDING_DIM=384`, config/embedding_dim 제거, 컬럼+HNSW 재생성 alembic 리비전 → `alembic upgrade head`.
4. **호출부 배선**(1-2·5절): indexer=`embed_documents`, vector_search=`embed_query`, composition 폴백/`is_vector_fallback_available`/`is_semantic` 재정의, bootstrap 정리.
5. **설정/문서**(6·7절): config·.env.example 갱신, 문서 목록 갱신 + ADR-0004 추가.
6. **재임베딩 배치**(4절): `app/scripts/reembed.py` 구현 → 스키마 마이그레이션 후 실행.
7. **테스트**(8·9절): gemini 테스트 교체, 스모크·접두사·차원 테스트 추가, 전체 스위트 통과 + mypy 클린.

> 운영 배포 순서(요약): 코드 배포 → `alembic upgrade head`(스키마·embedding NULL화) → `python -m app.scripts.reembed`(재임베딩) → 벡터 fallback 복귀. 재임베딩 전에도 키워드 검색은 무중단.
