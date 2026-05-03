try:
    from ._client import RestlyTestClient
    from ._fixtures import (
        app,
        async_session,
        autouse_alembic_upgrade,
        autouse_savepoint_only_mode_sessions,
        client,
        project_root,
        session,
    )
except ModuleNotFoundError as exc:
    if exc.name in {"httpx", "pytest"}:
        raise ModuleNotFoundError(
            "fastapi_restly.testing requires optional testing dependencies. "
            'Install them with: pip install "fastapi-restly[testing]"'
        ) from exc
    raise

__all__ = [
    "RestlyTestClient",
    "app",
    "async_session",
    "autouse_alembic_upgrade",
    "autouse_savepoint_only_mode_sessions",
    "client",
    "project_root",
    "session",
]
