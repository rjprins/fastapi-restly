try:
    from fastapi_restly._pytest_fixtures import (
        _shared_connection,
        restly_app,
        restly_async_session,
        restly_client,
        restly_project_root,
        restly_session,
    )
except ModuleNotFoundError as exc:
    if exc.name in {"httpx", "pytest"}:
        raise ModuleNotFoundError(
            "fastapi_restly.pytest_fixtures requires optional testing dependencies. "
            'Install them with: pip install "fastapi-restly[testing]"'
        ) from exc
    raise

__all__ = [
    "restly_app",
    "restly_async_session",
    "restly_client",
    "restly_project_root",
    "restly_session",
]
