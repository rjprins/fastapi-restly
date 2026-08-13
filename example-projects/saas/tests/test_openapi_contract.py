"""Pin the canonical spelling of collection endpoints."""

import pytest
from app.main import app


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
    collection_path: str,
) -> None:
    paths = app.openapi()["paths"]

    assert collection_path in paths
    assert f"{collection_path}/" not in paths
