"""Test configuration.

The suite builds its own settings and installs them before calling the factory,
so no environment variable has to be set before an import.
"""

import os

import fastapi_restly as fr

from app.main import create_app
from app.settings import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/myapp_test",
)

# _env_file=None so a developer's local .env cannot redirect the test suite.
# The ignore is for the type checker only: pydantic-settings accepts these
# underscore arguments at runtime, but they are not in the synthesized __init__.
Settings.use(Settings(database_url=TEST_DATABASE_URL, _env_file=None))  # type: ignore[call-arg]

app = create_app()

# Build the schema by running the migrations, so the tests exercise the
# same path a deployment does.
fr.testing.configure_tests(app=app, base=fr.DataclassBase, alembic_upgrade=True)
