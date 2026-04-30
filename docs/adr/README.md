# Architecture Decision Records

아키텍처 결정은 여기에 개별 파일로 기록한다.

## 파일명 규칙

```
NNNN-<kebab-case-title>.md
```

- `NNNN`: 4자리 순번 (0001 부터)
- 번호는 재사용하지 않는다. 폐기된 ADR 은 `상태: superseded-by ADR-YYYY` 로 두고 파일은 남긴다.

## 포맷 (MADR 변형)

```markdown
# ADR-NNNN: <제목>

- 상태: proposed | accepted | superseded-by ADR-YYYY | deprecated
- 일시: YYYY-MM-DD
- 관련: ARCHITECTURE.md §N, plan.md §N

## 컨텍스트
<왜 결정이 필요했나. 어떤 제약/대안이 있었나.>

## 결정
<무엇을 정했나. 한두 문장으로 명확히.>

## 결과
<장단점, 후속 영향, 관찰할 지표, 재검토 트리거.>
```

## 작성 원칙

- 결정이 실제로 바뀔 수 있는 지점만 ADR 로 만든다. "관례" 수준의 선택은 ARCHITECTURE.md 로 충분.
- 하나의 ADR 은 **하나의 결정**만 담는다. 묶지 않는다.
- ADR 이 accepted 되면 `ARCHITECTURE.md` 의 관련 섹션을 **같은 PR 에서** 갱신한다.
- 기존 결정을 뒤집을 때는 새 ADR 을 만들고 이전 ADR 의 상태를 `superseded-by` 로 바꾼다.

## 인덱스

- [ADR-0001: 저장형 검색 구조 채택](0001-storage-search-structure.md)
- [ADR-0002: pgvector 기반 하이브리드 검색 도입](0002-pgvector-hybrid-search.md)
- [ADR-0003: MCP 도구의 읽기 전용 경계 유지](0003-read-only-mcp-boundary.md)
