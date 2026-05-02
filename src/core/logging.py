"""구조화 JSON 로깅 팩토리.

`get_logger(name)` 은 JSON 한 줄 로그를 기본 stderr 로 내보내는 로거를 반환한다.
요청 시 trace_id 가 있으면 `extra={"trace_id": ..., "duration_ms": ...}` 식으로 주입한다.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """logging.Formatter 를 JSON 한 줄로 커스터마이즈."""

    def format(self, record: logging.LogRecord) -> str:
        """LogRecord 를 JSON 한 줄 문자열로 직렬화한다."""
        base: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("trace_id", "duration_ms", "document_id", "tool_name", "task_id"):
            value = getattr(record, key, None)
            if value is not None:
                base[key] = value
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


def _configure_root(level: str) -> None:
    """루트 로거에 JSON 핸들러를 1회만 부착한다."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """이름 별 JSON 로거를 반환한다."""
    _configure_root(level)
    return logging.getLogger(name)
