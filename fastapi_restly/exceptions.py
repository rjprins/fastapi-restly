"""Public exception hierarchy for FastAPI-Restly."""


class RestlyError(Exception):
    """Base class for FastAPI-Restly framework errors."""


class RestlyConfigurationError(RestlyError):
    """Raised when Restly is used before required configuration is available."""


__all__ = ["RestlyConfigurationError", "RestlyError"]
