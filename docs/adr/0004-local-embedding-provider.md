# ADR-0004: 임베딩 프로바이더를 관리형 API 에서 로컬 CPU 모델로 전환

- 상태: accepted
- 일시: 2026-08-08
- 관련: `docs/embedding-provider-local-model-design.md`, ARCHITECTURE.md §7, ADR-0002

## 컨텍스트
기존 벡터 검색은 `GeminiEmbeddingProvider`(Gemini 임베딩 API, 256차원)를 통해 의미
유사도를 확보했다. 관리형 API 키 의존은 (a) 키 미설정 시 `HashEmbeddingProvider`
(의미 없는 결정적 해시)로 조용히 폴백되어 벡터 검색 품질이 사라지는 운영 리스크,
(b) 외부 API 비용·지연·가용성 의존을 만든다. 무료·CPU·다국어(한글 포함) 경량
임베딩으로 전환하라는 지시에 따라 로컬 모델 도입을 검토했다.

## 결정
`intfloat/multilingual-e5-small`(384차원, ~118M, CPU 추론, 다국어)을
`sentence-transformers` 로 로드하는 `LocalEmbeddingProvider` 를 기본 프로바이더로
채택한다. `GeminiEmbeddingProvider` 는 완전히 제거한다.

- E5 계열은 학습 규약상 문서엔 `"passage: "`, 질의엔 `"query: "` 접두사가 필요해,
  기존 대칭 `embed(texts)` 를 비대칭 `embed_documents(texts)`/`embed_query(text)`
  로 분리했다(`EmbeddingProvider` Protocol 변경).
- pgvector 컬럼 차원을 256 → 384 로 변경했다(컬럼은 생성 시 dim 이 고정돼
  재생성 마이그레이션 필요, `alembic/versions/ff8aa8f36266_*`). 기존 256차원
  벡터는 새 모델과 호환되지 않아 보존 의미가 없어 폐기하고, 스키마 변경과
  분리한 전용 배치(`app/scripts/reembed.py`)로 저장된 청크 텍스트만 재임베딩한다
  (재파싱·네트워크 없음).
- "벡터 fallback 을 켤지" 판단 기준을 "Gemini 키 유무"에서 "임베딩 백엔드가
  `local`(의미 유사도 있음)인지"로 바꿨다(`is_vector_fallback_available`).
  `HashEmbeddingProvider` 는 테스트·모델 로드 실패 폴백 용도로 유지한다.
- Qdrant 등 별도 벡터 DB 로의 전환은 검토했으나 비권장으로 결론 내렸다
  (`docs/vector-store-qdrant-vs-pgvector.md`) — pgvector 유지, 컬럼/인덱스
  구조는 그대로 두고 차원만 바꿨다.

## 결과
- 장점: 외부 API 키·비용·네트워크 의존 제거, 오프라인(모델 캐시 후) 동작 가능,
  다국어(한글) 의미 검색을 항상 사용 가능(키 유무에 좌우되지 않음).
- 단점: 최초 실행 시 모델 다운로드(~120MB, HuggingFace 네트워크 필요),
  `torch`+`sentence-transformers` 의존성 무게 증가(CPU 전용 휠로 고정해
  CUDA 휠 포함은 방지). 배포 환경에서 모델을 미리 캐시(`HF_HOME`)해두는 것을 권장.
- 후속 영향: `app/services/indexer/embedding_provider.py`(Protocol·구현체),
  `app/composition.py`(프로바이더 배선·fallback 판별), `app/models/openapi.py`
  (`EMBEDDING_DIM=384`), `app/core/config.py`(`DOCS_MCP_EMBEDDING_MODEL`/
  `DOCS_MCP_EMBEDDING_BACKEND`, Gemini 설정 제거), `app/scripts/reembed.py`(신규).
