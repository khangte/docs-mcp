"""uvicorn 진입점 — 모듈 임포트 시 DB 연결을 지연시킨다."""

from src.main import create_app

app = create_app()
