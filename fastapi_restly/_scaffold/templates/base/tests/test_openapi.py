import warnings

from fastapi_restly.exc import RestlyMisuseWarning
from fastapi_restly.testing import RestlyTestClient


def test_openapi_class_names_are_unique(restly_client: RestlyTestClient) -> None:
    """Restly warns when two classes in the API have the same name: OpenAPI
    then shows them under long names that can change, and generated clients
    use these names as type names. This test turns the warning into an error."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyMisuseWarning)
        restly_client.app.openapi()
