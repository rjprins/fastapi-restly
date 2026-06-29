try:
    from fastapi_restly._pytest_fixtures import (
        _shared_connection,  # noqa: F401 -- re-exported so dependent fixtures resolve it
        restly_app,
        restly_async_session,
        restly_client,
        restly_project_root,
        restly_session,
    )
except ModuleNotFoundError as exc:  # pragma: no cover -- exercised via
    # subprocess in test_testing_fixtures_coverage.py (httpx/httpx2/pytest blocked)
    if exc.name in {"httpx", "httpx2", "pytest"}:
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
