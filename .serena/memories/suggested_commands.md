## 개발 및 실행 명령어

**서버 실행**:
```bash
# FastAPI 개발 서버
uvicorn app.main:app --reload

# 또는 팩토리 패턴 사용
uvicorn app.main:create_app --factory --reload
```

**테스트**:
```bash
pytest tests/
pytest tests/ -v  # 상세 출력
```

**코드 포매팅 & 린팅**:
```bash
ruff format src/
ruff check src/ --fix
```

**의존성 관리**:
```bash
pip install -r requirements.txt
# 또는 uv 사용
uv pip install -r requirements.txt
```

**데이터베이스**:
- 기본값: SQLite (`./docs_mcp.db`)
- 설정: `src/core/config.py`

**MCP 서버 테스트**:
Claude Desktop의 `claude_desktop_config.json`에 등록 후 도구로 사용 가능
