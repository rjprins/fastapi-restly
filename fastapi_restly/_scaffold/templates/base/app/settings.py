from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Absolute: a relative env_file resolves against the working directory, so
    # running from a subdirectory would silently miss it.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: str


def get_settings() -> Settings:
    """Read the settings from the environment and the .env file."""
    # pydantic-settings fills the required fields from the environment. Type
    # checkers do not know that, and report them as missing arguments.
    return Settings()  # type: ignore[call-arg]
