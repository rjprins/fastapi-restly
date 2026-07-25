try:
    from fastapi_restly._pytest_fixtures import (
        _restly_managed_isolation,  # noqa: F401 -- autouse; inert until fr.testing.configure_tests()
        _restly_managed_schema,  # noqa: F401 -- autouse; inert until fr.testing.configure_tests()
        _shared_connection,  # noqa: F401 -- re-exported so dependent fixtures resolve it
        pytest_addoption,  # noqa: F401 -- pytest hook; must be found on the plugin module
        pytest_configure,  # noqa: F401 -- pytest hook; must be found on the plugin module
        pytest_report_header,  # noqa: F401 -- pytest hook; must be found on the plugin module
        pytest_unconfigure,  # noqa: F401 -- pytest hook; must be found on the plugin module
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
