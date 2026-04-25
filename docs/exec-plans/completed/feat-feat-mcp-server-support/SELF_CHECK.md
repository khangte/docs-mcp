# 자체 점검표: MCP 서버 지원

## SPEC 기능 체크
- [x] 기능 1 (list_documents): 구현 완료. `mcp_server.py`의 `list_documents` 도구 확인.
- [x] 기능 2 (register_document): 구현 완료. URL 및 raw_document 처리 로직 포함.
- [x] 기능 3 (search_endpoints): 구현 완료. 하이브리드 검색 옵션 연동.
- [x] 기능 4 (query_rag): 구현 완료. 답변 및 citations 반환 확인.
- [x] 기능 5 (get_endpoint_details): 구현 완료. curl 예시 코드 생성 포함.

## 코드 품질
- [x] 전역 상태 제거 및 팩토리 함수(`create_mcp_server`) 도입으로 테스트 가능성 확보.
- [x] `service_context` 매니저를 통한 서비스 번들 수명 주기 관리.
- [x] Pydantic 에러(JSON 자동 파싱 관련)를 처리하기 위한 유연한 타입 핸들링 추가.

## 테스트 결과
- `tests/integration/test_mcp_server.py`의 4개 테스트 케이스 모두 통과.
