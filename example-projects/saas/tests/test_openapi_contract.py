"""Pin the canonical spelling of collection endpoints."""

import pytest
from fastapi import FastAPI


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
def test_trash_routes_carry_the_listing_grammar(
    restly_app: FastAPI, trash_path: str
) -> None:
    """A route declaring ``query_params`` shows the listing grammar in the contract."""
    operation = restly_app.openapi()["paths"][trash_path]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert {"page", "page_size", "sort"} <= names
