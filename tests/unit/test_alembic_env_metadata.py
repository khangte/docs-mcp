"""alembic/env.py 가 모든 모델 모듈을 import 해 Base.metadata 에 등록하는지 확인한다.

`document_meta` 는 이미 명시 import 돼 있었지만 `project_drive_source`/
`project_notion_source`(현재는 병합된 `project_source`) 는 한때 누락돼
있었다(reviewer가 P1 리뷰 중 발견). env.py 가 모델 모듈을 빠뜨리면 실제
DB 에 있는 테이블이 "메타데이터엔 없다"고 오판되어 `alembic revision
--autogenerate` 가 잘못된 `DROP TABLE` 마이그레이션을 생성할 위험이 있다.

`alembic check`(1.9+)는 env.py 를 실제로 로드해 `compare_metadata` 비교를
수행하므로, 이 회귀를 잡는 가장 직접적인 방법이다 — env.py 의 import 문을
파일 텍스트로 검사하는 대신, alembic 이 실제로 무엇을 "메타데이터에 등록된
전체"로 인식하는지를 서브프로세스로 검증한다(같은 pytest 세션 안에서 다른
테스트가 이미 해당 모델을 import 해 Base.metadata 를 오염시키는 것을 피하기
위해 반드시 서브프로세스여야 한다).

로컬 Postgres 가 alembic head 까지 마이그레이션돼 있어야 한다
(`uv run alembic upgrade head`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )


def test_alembic_env_has_no_pending_autogenerate_diff() -> None:
    """`alembic check` 가 pending diff 없음(exit 0)을 보고한다.

    특히 project_source 에 대한 remove_table(=DROP TABLE) 오탐이 없어야
    한다 — env.py 가 해당 모델을 import 하지 않으면 Base.metadata 에서 그
    테이블이 빠져 autogenerate 가 삭제 마이그레이션을 만들려 한다.
    """
    result = _run_alembic_check()

    assert result.returncode == 0, (
        "alembic check 실패(pending autogenerate diff 존재) — "
        "alembic/env.py 가 모든 모델 모듈을 import 하는지 확인할 것.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "project_source" not in result.stderr
