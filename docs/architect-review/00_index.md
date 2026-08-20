# architect-review 목차

`docs/architect-review/`에 쌓인 설계 검토·판정 문서 51건의 색인이다. 번호는 작성 순서이고,
설명은 각 문서의 결론 한 줄이다. 판정 문서는 "무엇을 어떻게 결정했는가"를 설명에 담았다.

| # | 파일명 | 제목 | 설명 |
|---|---|---|---|
| 01 | [`01_app_layout_refactor.md`](01_app_layout_refactor.md) | app/ 디렉터리 계층 분리 설계안 | 웹·MCP·공유 코어가 `app/` 최상위에 뒤섞인 경계 오배치를 분해. FastAPI 제거 후 MCP 단일 진입점으로 착지 |
| 02 | [`02_documents_boundary.md`](02_documents_boundary.md) | `services/documents/` 경계 정리 방안 | 경계 자체는 이미 자기완결적. 결합을 끊는 리팩터링이 아니라 이름을 책임에 맞추는 작업으로 재정의 |
| 03 | [`03_search_performance_improvements.md`](03_search_performance_improvements.md) | 검색 성능 개선 방안 | 두 검색 경로의 병목을 P1~P6로 분해. RRF 포함 전 항목 구현 완료 |
| 04 | [`04_search_p1_keyword_fts_design.md`](04_search_p1_keyword_fts_design.md) | P1 — 키워드 검색 Postgres FTS 이관 | 한글이 키워드 매칭에서 통째로 버려지던 문제를 FTS 이관으로 해소. `tokenize.py`는 해시 임베딩이 공유하므로 건드리지 않음 |
| 05 | [`05_embedding_provider_local_model_design.md`](05_embedding_provider_local_model_design.md) | 임베딩 프로바이더 교체 — Gemini API 제거, 로컬 CPU 모델 | `multilingual-e5-small`(384dim) 채택. E5 비대칭 접두사 때문에 Protocol을 `embed_documents`/`embed_query`로 분리 |
| 06 | [`06_vector_store_qdrant_vs_pgvector.md`](06_vector_store_qdrant_vs_pgvector.md) | 벡터 스토어 — pgvector 유지 vs Qdrant 전환 | pgvector 유지. 한 행에 벡터와 FTS가 같이 있는 구조를 쪼개면 이중쓰기·크로스스토어 융합이 새로 생긴다 |
| 07 | [`07_search_rrf_reevaluation.md`](07_search_rrf_reevaluation.md) | RRF 순위 융합 적용 재검토 | 키워드 0건일 때만 벡터를 타던 fallback을 상시 융합으로 전환. top-3 recall 80%→95%, 회귀 0 |
| 08 | [`08_postgres_weight_and_metadata_review.md`](08_postgres_weight_and_metadata_review.md) | PostgreSQL 무게 및 메타데이터 필요성 | 배포 목표를 개인·내부용으로 확정해 Postgres 유지. SQLite 전환의 유일한 트리거는 "docker 없이 설치 즉시 실행" |
| 09 | [`09_search_quality_post_rrf.md`](09_search_quality_post_rrf.md) | RRF 이후 검색 품질 추가 개선 | P1 평가셋 확장·P5 `ef_search` 완료, P4 K 스윕은 `K=60` 유지로 null result. P3 리랭킹은 recall이 구속 지표가 아니라 보류 |
| 10 | [`10_collab_docs_search_fixes.md`](10_collab_docs_search_fixes.md) | 협업 문서(Drive/Notion) 검색 수정사항 | 구버전 문서를 최종본으로 오인한 실사례에서 서버 결함 다수 확인. 6건 중 5건 수정, 1건 기각 |
| 11 | [`11_search_performance_round2.md`](11_search_performance_round2.md) | 검색 성능 라운드 2 — 잔여 병목 | Quick win 3건 구현, 구조적 개선 3건은 제안까지. 서버 측 LLM 질의확장은 스코프 밖으로 배제 |
| 12 | [`12_rag_depth_directions.md`](12_rag_depth_directions.md) | RAG를 더 깊게 만드는 방향 검토 | 후보 4개 중 계층·문맥 청킹만 조건부 권장. 서버가 판단을 대신하는 방향은 배제하고 클라이언트 LLM에 위임 |
| 13 | [`13_rag_context_chunking_experiment.md`](13_rag_context_chunking_experiment.md) | 계층·문맥 청킹 설계 검토 + 실험 계획 | 작은 픽스처에서는 가짜 개선이 나온다는 교훈을 남기고 실험 종결 |
| 14 | [`14_vector_only_and_remaining_rag_levers.md`](14_vector_only_and_remaining_rag_levers.md) | 키워드 arm 제거·전량 벡터화 판단 검증 | 전량 벡터화·Qdrant 모두 비권장. 키워드 arm에는 P1이 튜닝한 한글·경로분해 FTS 자산이 실려 있어 제거는 순손실 |
| 15 | [`15_embedding_model_swap_experiment.md`](15_embedding_model_swap_experiment.md) | 임베딩 모델 교체 실험 설계 | 교차언어 Recall@3 50%를 벡터 arm 자신의 약점으로 보고 모델 교체를 정공법으로 설계. 측정 없이 착수 금지 규율 확립 |
| 16 | [`16_long_section_chunking_blindspot.md`](16_long_section_chunking_blindspot.md) | 긴 섹션 무상한 청크화 + 512토큰 조용한 truncation | 맹점은 실재하나 코퍼스 측정 결과 512 초과 0건. Phase 1 경고 로깅만 넣고 분할은 YAGNI로 보류 |
| 17 | [`17_schema_coupling_review.md`](17_schema_coupling_review.md) | DB 스키마 결합 구조 재검토 | openapi만 FK CASCADE 강결합, drive/notion은 project 문자열 느슨결합인 비대칭을 검토 |
| 18 | [`18_openapi_schema_overengineering_audit.md`](18_openapi_schema_overengineering_audit.md) | openapi 테이블군 과설계 감사 | 과설계는 있으나 1:1 과잉 정규화가 아니라 write-only 죽은 무게(테이블 1개 + 컬럼 2개) |
| 19 | [`19_schema_migration_wave_plan.md`](19_schema_migration_wave_plan.md) | 스키마 마이그레이션 물결 설계 | `project_source` 병합·죽은 컬럼 제거·`document_sync_history` 존치를 alembic 리비전 하나로 묶음 |
| 20 | [`20_spec_vs_code_source_of_truth_review.md`](20_spec_vs_code_source_of_truth_review.md) | OpenAPI 스펙을 색인 소스로 삼는 접근의 타당성 | 이 프로젝트에선 스펙 기반이 맞다. 대상 코드를 대개 보유하지 않는 문서 검색 서버라 drift는 경계 밖 |
| 21 | [`21_models_file_split_design.md`](21_models_file_split_design.md) | `app/models/openapi.py` 파일 분리 설계 | 분리 자체보다 모델 등록 허브(`__init__.py`) 도입이 본질적 이득 — alembic 메타데이터 누락 버그류를 구조적으로 제거 |
| 22 | [`22_table_name_scope_rename_review.md`](22_table_name_scope_rename_review.md) | 테이블명 스코프 정합 리네임 검토 | `api_` 접두사가 붙었지만 실제로는 전 포맷 공용인 테이블들의 이름·스코프 불일치 판정 |
| 23 | [`23_long_section_sub_chunking_phase2_design.md`](23_long_section_sub_chunking_phase2_design.md) | 긴 섹션 sub-chunking 설계 | 분할은 파서가 아니라 chunk 빌드 시점에. `ref_id`를 sub끼리 공유해 RRF dedupe가 자동 병합, 스키마 변경 불필요 |
| 24 | [`24_parent_child_chunking_greenfield_vs_actual.md`](24_parent_child_chunking_greenfield_vs_actual.md) | Parent-Child 청킹 — 그린필드 설계 vs 현 구조 | 표준 패턴을 무전제로 설계한 뒤, 이 코드베이스에 부수효과로 이미 있는 유사 구조와 차이를 정리 |
| 25 | [`25_unconditional_n1_vs_gated_subchunking.md`](25_unconditional_n1_vs_gated_subchunking.md) | 무조건 N:1 전환 vs 게이트형 조건부 sub-chunking | 게이트형이 낫다. 질문에 깔린 전제 두 개를 정정 |
| 26 | [`26_pdf_docx_deterministic_catchall_gate_recheck.md`](26_pdf_docx_deterministic_catchall_gate_recheck.md) | pdf/docx 결정론적 개요-캐치올 — 게이트 전제 재판단 | pdf/docx는 서식이 없어 구조적으로 100% 단일 섹션. 실측이 게이트 전제를 흔들어 판정 수정 |
| 27 | [`27_search_quality_eval_real_corpus_design.md`](27_search_quality_eval_real_corpus_design.md) | 검색 품질 평가 설계 — 실 코퍼스 질의셋 | 합성 하네스를 Stripe·GitHub 공개 스펙 기반 recall@k / MRR 방법론으로 대체 |
| 28 | [`28_schema_chunk_ref_id_truncation_fix.md`](28_schema_chunk_ref_id_truncation_fix.md) | schema 청크 `ref_id` 트렁케이션 크래시 | developer가 낸 3개 안 모두 기각. 근본 원인은 컬럼 폭이 아니라 schema 청크만 `ref_id` 규약을 이탈한 것 |
| 29 | [`29_search_quality_eval_real_corpus_results.md`](29_search_quality_eval_real_corpus_results.md) | 검색 품질 평가 — 실 코퍼스 측정 결과 | 합성 벤치는 포화됐던 것. 실 규모 코퍼스에서 수치가 절반 이하로 떨어짐을 자체 반증 |
| 30 | [`30_eval_batch_automation.md`](30_eval_batch_automation.md) | 배치 자동화 적용 대상 특정 | 대상 미지정 요청에서 평가 재측정 배치를 특정. MCP 서버 내부 스케줄러는 반려 |
| 31 | [`31_refresh_index_batch_automation.md`](31_refresh_index_batch_automation.md) | `refresh_index` 배치 자동화 — 서버 밖 상주 러너 | 내부 스케줄링 반려는 유지하고 서버 밖 러너로 설계 |
| 32 | [`32_notion_page_id_legacy_slot_seed.md`](32_notion_page_id_legacy_slot_seed.md) | 레거시 슬롯 `DOCS_MCP_NOTION_PAGE_ID` 시드 설계 | 부트스트랩 시드 경로가 이 값을 실제로 쓰지 않던 실버그 포함 3건 수정 필요 판정 |
| 33 | [`33_notion_api_version_upgrade_judgment.md`](33_notion_api_version_upgrade_judgment.md) | Notion API 버전 업그레이드 판단 | 지금은 올리지 않는다. 구버전 지원 종료 계획이 없어, 진단 로그만 넣고 트리거 발생 시 착수 |
| 34 | [`34_drive_notion_no_embedding_rationale.md`](34_drive_notion_no_embedding_rationale.md) | Drive/Notion 소스가 임베딩을 쓰지 않는 이유 | 임베딩은 본문을 영속화하는 경로에서만 성립하는데 이 경로는 메타 캐시만 저장해 붙일 자리가 없다 |
| 35 | [`35_drive_notion_embedding_migration_and_refresh_strategy.md`](35_drive_notion_embedding_migration_and_refresh_strategy.md) | Drive/Notion 임베딩 도입 변경범위 · refresh 전략 | 본문 영속화를 Phase 0~3으로 분해하고 `refresh_index`의 upsert vs delete-and-insert를 판정 |
| 36 | [`36_user_rag_proposal_vs_our_design_diff.md`](36_user_rag_proposal_vs_our_design_diff.md) | 사용자 범용 RAG 제안 vs 우리 설계 대조 | 뼈대는 같고 다른 곳이 셋. 그중 둘은 이미 실험해서 반대로 판정한 건 |
| 37 | [`37_document_search_phase3_rrf_verdict.md`](37_document_search_phase3_rrf_verdict.md) | `DocumentSearchService` 2단계 교체 구조 판정 | 가중합 유지(A) 대신 엔드포인트 검색과 동형인 3-arm RRF(B) 채택 |
| 38 | [`38_doc36_step13_legacy_fetch_removal_gate.md`](38_doc36_step13_legacy_fetch_removal_gate.md) | 구경로 제거 게이트 판정 | 지금은 반려. 본문 예산 장치를 걷어내기 전에 선행 작업을 지정 |
| 39 | [`39_body_index_backfill_gate_fix.md`](39_body_index_backfill_gate_fix.md) | 기존 문서 본문 소급 백필 — 게이트 조건 수정 | 별도 스크립트·force 플래그 반려. 게이트가 본문 유무가 아니라 메타 변경만 보던 것이 원인 |
| 40 | [`40_body_backfill_content_normalization_and_commit_boundary.md`](40_body_backfill_content_normalization_and_commit_boundary.md) | 본문 백필 크래시 3종 판정 | NUL 정규화 위치·빈 본문 처리·커밋 경계를 각각 판정. 소급 색인 중 중간 커밋이 없던 문제 포함 |
| 41 | [`41_backfill_result_verification_and_indexed_default_gate.md`](41_backfill_result_verification_and_indexed_default_gate.md) | 백필 결과 검증 및 `indexed` 기본값 전환 게이트 | 보고치와 DB 사이 3건 오차를 규명하고 기본 전략을 `indexed`로 전환해도 되는지 판정 |
| 42 | [`42_snippet_as_of_mcp_exposure_verdict.md`](42_snippet_as_of_mcp_exposure_verdict.md) | `snippet_as_of` MCP 응답 노출 여부 | 서비스 DTO에만 있고 MCP 페이로드에는 빠져 있던 필드의 노출 판정 |
| 43 | [`43_agent_oriented_rag_claim_verification.md`](43_agent_oriented_rag_claim_verification.md) | "Agent-oriented RAG" 대외 주장 vs 실제 구현 | Candidate/Evidence 분리·Lazy Fetch 주장을 코드와 대조해 어디까지가 사실인지 검증 |
| 44 | [`44_layer_boundary_exceptions_verdict.md`](44_layer_boundary_exceptions_verdict.md) | 계층 경계 예외 2건 판정 | 고칠 것과 남길 것을 나눔. `get_raw_document`의 project 스코프 건은 별건으로 분리 |
| 45 | [`45_portfolio_metrics_and_type_only_import_verdict.md`](45_portfolio_metrics_and_type_only_import_verdict.md) | 포트폴리오 문서 리뷰 판정 | reviewer 지적 2건 전부 수용 — 반증된 헤드라인 지표 정정, 런타임 임포트를 타입 전용으로 강제 |
| 46 | [`46_get_raw_document_project_scope_verdict.md`](46_get_raw_document_project_scope_verdict.md) | `get_raw_document` project 스코프 검사 누락 | `document_id`만 알면 다른 project 문서 원문을 받을 수 있던 건. 다른 도구와의 정합 검사 비대칭 판정 |
| 47 | [`47_notion_nested_block_indexing_gap_and_design.md`](47_notion_nested_block_indexing_gap_and_design.md) | Notion nested block 색인 갭 조사 및 설계 | 검색 로직(3-arm RRF)은 이미 충분하고 구멍은 전부 수집 단계. 하위 페이지·하위 DB·본문 재귀 색인 설계 |
| 48 | [`48_presentation_architecture_diagram_review.md`](48_presentation_architecture_diagram_review.md) | 발표용 아키텍처 다이어그램 검토 | 구조 오류 1건·표기 오류 2건·누락 4건으로 수정 필요 판정 |
| 49 | [`49_data_flow_scenarios.md`](49_data_flow_scenarios.md) | 데이터 흐름 시나리오별 정리 및 플로우 차트 | 케이스 6개를 mermaid 플로우차트로 정리. 색 범례를 문서 전체에 통일 적용 |
| 50 | [`50_refresh_lock_abort_asymmetry_verdict.md`](50_refresh_lock_abort_asymmetry_verdict.md) | `refresh_index` advisory lock — aborted 트랜잭션 비대칭 | 지적 타당. 목적이 다른 두 층을 각각 독립적으로 수정 |
| 51 | [`51_project_structure_and_git_hygiene_audit.md`](51_project_structure_and_git_hygiene_audit.md) | 프로젝트 구조 · git 추적 상태 전반 점검 | 자격증명 노출 없음. `uv.lock` 미커밋·`.env.example` 누락 등 조치 4건, 아키텍처 HTML 정리 권고 2건 |
