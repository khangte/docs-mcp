## 작업 완료 시 체크리스트

1. **코드 변경 후**:
   - [ ] ruff lint 확인: `ruff check src/ --fix`
   - [ ] import 자동 정렬: `ruff format src/`
   - [ ] 타입 힌트 추가
   - [ ] 불필요한 코드/임포트 제거

2. **테스트**:
   - [ ] 기존 테스트 통과: `pytest tests/`
   - [ ] 변경사항 관련 새 테스트 작성 (필요시)

3. **커밋**:
   - [ ] Conventional Commits 형식 사용
   - [ ] 한글로 커밋 메시지 작성
   - [ ] Atomic commit (변경 1개 = 커밋 1개)

4. **임시 파일 정리**:
   - [ ] `temp_*`, `_new`, `_old`, `_backup` 파일 삭제
   - [ ] 디버그 코드 제거
