try:
    from ._client import RestlyTestClient
except ModuleNotFoundError as exc:
    if exc.name == "httpx":
        raise ModuleNotFoundError(
            "fastapi_restly.testing requires optional testing dependencies. "
            'Install them with: pip install "fastapi-restly[testing]"'
        ) from exc
    raise

__all__ = [
    "RestlyTestClient",
]
