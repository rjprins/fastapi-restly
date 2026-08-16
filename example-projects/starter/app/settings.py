from pathlib import Path

import fastapi_restly as fr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(fr.utils.CurrentSettingsMixin, BaseSettings):
    # Absolute: a relative env_file resolves against the working directory, so
    # running from a subdirectory would silently miss it.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: str
