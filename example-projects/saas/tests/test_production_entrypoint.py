"""Smoke-test the production entrypoint's no-arg create_app() branch.

The suite's own conftest installs explicit Settings with set_settings(), so
`get_settings()` never builds from the environment -- the branch that
`uvicorn app.asgi:app` actually exercises in production. A regression there
(a new required field, a renamed env var) would only surface at deploy time. Runs in a subprocess: the in-process app is already
locked by conftest's configure_tests(), and a second fr.configure() call
would raise RestlyConfigurationError regardless of whether this branch works.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import _test_url

_SAAS_ROOT = Path(__file__).resolve().parents[1]


def test_create_app_builds_from_environment_settings(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SAAS_ROOT)
    # DATABASE_URL matches the same test service conftest resolved (honoring
    # SAAS_TEST_DATABASE_URL / RESTLY_TEST_DATABASE_URL overrides), so this
    # still targets CI's database when it isn't on the default port.
    env["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
    env.pop("DB_POOL_SIZE", None)
    env.pop("DB_MAX_OVERFLOW", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import fastapi_restly as fr\n"
            "from app.main import create_app\n"
            "create_app()\n"
            "print(fr.db.get_async_engine().url.drivername)\n",
        ],
        # An empty cwd, not the project root: Settings' env_file=".env" is
        # resolved relative to cwd, and a real .env there would leak pool
        # settings into this default-settings smoke test.
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "postgresql+asyncpg"
