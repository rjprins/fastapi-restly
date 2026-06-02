import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError, RestlyError


def test_restly_exception_hierarchy_is_public():
    assert fr.exc.RestlyError is RestlyError
    assert fr.exc.RestlyConfigurationError is RestlyConfigurationError
    assert issubclass(RestlyConfigurationError, RestlyError)
    assert issubclass(RestlyConfigurationError, Exception)
