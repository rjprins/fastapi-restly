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
