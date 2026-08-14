"""Smoke-test the production entrypoint's no-arg create_app() branch.

The suite's own conftest always passes explicit Settings to create_app(), so
`settings = settings or Settings()` -- the branch `uvicorn --factory
app.main:create_app` actually exercises in production -- is otherwise never
run. A regression there (a new required field, a renamed env var) would only
surface at deploy time. Runs in a subprocess: the in-process app is already
locked by conftest's configure_tests(), and a second fr.configure() call
would raise RestlyConfigurationError regardless of whether this branch works.
"""

import os
import subprocess
import sys
from pathlib import Path


def test_create_app_builds_from_environment_settings() -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = (
        "postgresql+asyncpg://postgres:postgres@localhost:5433/saas_test"
    )
    env.pop("DB_POOL_SIZE", None)
    env.pop("DB_MAX_OVERFLOW", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.main import create_app\n"
            "app = create_app()\n"
            "print(app.state.engine.url.drivername)\n",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "postgresql+asyncpg"
