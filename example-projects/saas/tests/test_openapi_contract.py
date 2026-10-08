"""Pin the canonical spelling of collection endpoints."""

import warnings

import pytest
from fastapi import FastAPI

from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning


@pytest.mark.parametrize(
    "collection_path",
    [
        "/countries",
        "/labels",
        "/organizations",
        "/projects",
        "/task-labels",
        "/tasks",
        "/uploads",
        "/users",
    ],
)
def test_openapi_uses_collection_paths_without_trailing_slashes(
    restly_app: FastAPI, collection_path: str
) -> None:
    paths = restly_app.openapi()["paths"]

    assert collection_path in paths
    assert f"{collection_path}/" not in paths


@pytest.mark.parametrize("trash_path", ["/projects/trash", "/tasks/trash"])
def test_trash_routes_carry_the_list_params(
    restly_app: FastAPI, trash_path: str
) -> None:
    """A route declaring ``list_params`` shows the list params in the contract."""
    operation = restly_app.openapi()["paths"][trash_path]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert {"page", "page_size", "sort"} <= names


def test_openapi_class_names_are_unique(restly_app: FastAPI) -> None:
    """Restly warns when two classes in the API have the same name: OpenAPI
    then shows them under long names that can change, and generated clients
    use these names as type names. This test turns the warning into an error."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        restly_app.openapi()
