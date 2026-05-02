## 코드 스타일 및 컨벤션

**네이밍**:
- 파일/폴더: snake_case
- 함수: snake_case
- 클래스: PascalCase

**린팅 & 포매팅**:
- Tool: ruff
- Line length: 100
- Target: Python 3.11+
- Rules: E (pycodestyle), F (pyflakes), I (isort), N (naming)
- Import: 절대 import만 허용 (상대 import 금지)

**Import 정렬**: isort로 자동 정렬

**타입 힌트**: 필수

**Docstrings**: 한글로 작성 (간단한 설명)

**로깅**: logging 모듈 사용 (print 금지)

**테스트**:
- Framework: pytest
- Async mode: auto
- Test path: tests/
