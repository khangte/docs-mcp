# ADR-0002: pgvector 기반 하이브리드 검색 도입

- 상태: accepted
- 일시: 2026-04-29
- 관련: ARCHITECTURE.md §4-2, plan.md §6

## 컨텍스트
단순 키워드 매칭만으로는 사용자의 자연어 질의(예: "로그인 관련 API 알려줘")에 정확한 엔드포인트를 찾아주기 어렵다. 반면, 벡터 검색만으로는 정확한 엔드포인트 경로(예: "/v1/auth/login")를 찾는 데 한계가 있을 수 있다.

## 결정
PostgreSQL의 `pgvector` 확장을 사용하여 벡터 유사도 검색을 구현하고, 기존 Full-text search(tsvector)와 결합한 **하이브리드 검색(Hybrid Search)** 방식을 도입한다.

## 결과
- 장점: 의미론적 검색과 키워드 정확도를 동시에 확보.
- 단점: 임베딩 모델 관리 및 벡터 인덱스(HNSW) 설정 복잡도 증가.
- 후속 영향: `app/services/search/vector_search.py` 및 `keyword_search.py` 구현.
- **구현 완료** (벡터 인덱스 영속화 작업): `ApiChunk.embedding`을 pgvector `Vector` 컬럼으로
  전환하고 `VectorSearch`가 DB에 코사인 거리(`<=>`) 쿼리를 직접 던지는 방식으로 교체.
  기존 `InMemoryVectorIndex`(프로세스 메모리 dict)는 제거. `docs/exec_plans/vector-index-persist/SPEC.md`
  참고. Keyword Search는 아직 tsvector가 아닌 애플리케이션 레벨 토큰 매칭 유지(TODO).
