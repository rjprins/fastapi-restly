import fastapi_restly as fr
from fastapi_restly.exceptions import RestlyConfigurationError, RestlyError


def test_restly_exception_hierarchy_is_public():
    assert fr.RestlyError is RestlyError
    assert fr.RestlyConfigurationError is RestlyConfigurationError
    assert issubclass(RestlyConfigurationError, RestlyError)
    assert issubclass(RestlyConfigurationError, Exception)
