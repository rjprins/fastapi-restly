import os

import fastapi_restly as fr

from app.main import create_app
from app.settings import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/myapp_test",
)

# _env_file=None so a local .env cannot point the suite at the development
# database. The ignore is for the type checker: pydantic-settings accepts the
# underscore arguments at runtime but does not declare them.
Settings.use(Settings(database_url=TEST_DATABASE_URL, _env_file=None))  # type: ignore[call-arg]

app = create_app()
fr.testing.configure_tests(app=app, base=fr.DataclassBase, alembic_upgrade=True)
