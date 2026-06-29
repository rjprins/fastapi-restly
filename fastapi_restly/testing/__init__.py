try:
    from fastapi_restly.db import (
        activate_savepoint_only_mode,
        deactivate_savepoint_only_mode,
    )

    from ._client import RestlyTestClient
except ModuleNotFoundError as exc:  # pragma: no cover -- exercised via
    # subprocess in test_testing_fixtures_coverage.py (httpx/httpx2 blocked).
    # Newer Starlette's testclient requires httpx2 and raises with name="httpx2";
    # our own _client.py import raises name="httpx". Treat both as the missing
    # test-client dependency.
    if exc.name in {"httpx", "httpx2"}:
        raise ModuleNotFoundError(
            "fastapi_restly.testing requires optional testing dependencies. "
            'Install them with: pip install "fastapi-restly[testing]"'
        ) from exc
    raise

__all__ = [
    "RestlyTestClient",
    "activate_savepoint_only_mode",
    "deactivate_savepoint_only_mode",
]
