"""Environment settings.

``Settings.current`` builds the settings the first time something reads them and
returns the same object afterwards. Nothing is built at import, so importing
``myapp.main`` never requires a configured environment: Alembic and the test
suite depend on that.

``Settings.use(...)`` installs settings explicitly, which is how ``tests/conftest.py``
points the suite at its own database.
"""

from pathlib import Path

import fastapi_restly as fr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(fr.utils.CurrentSettingsMixin, BaseSettings):
    """Settings read from the environment, or from ``.env`` if present."""

    # An absolute path: a relative env_file resolves against the working
    # directory, so running from a subdirectory would silently miss it.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: str
