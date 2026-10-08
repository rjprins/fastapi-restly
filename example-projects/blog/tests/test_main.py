import json
import warnings

from blog.main import BlogView, app
from fastapi.testclient import TestClient

import fastapi_restly as fr
from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning


def test_blog_view_uses_sync_auto_schema():
    assert issubclass(BlogView, fr.RestView)
    assert BlogView.schema.__name__ == "BlogSchema"
    assert set(BlogView.schema.model_fields) == {"id", "title"}


def test_openapi_spec(tmp_path):
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        spec = response.json()
        routes: list[str] = []
        for path in spec["paths"]:
            for method in spec["paths"][path]:
                routes.append(f"{method.upper()} {path}")
        assert routes == [
            "GET /blogs",
            "POST /blogs",
            "GET /blogs/{id}",
            "PATCH /blogs/{id}",
            "DELETE /blogs/{id}",
        ]
        # Snapshot the spec to a temp dir for inspection during the test run only.
        # Writing to the project directory would create spurious git diffs.
        snapshot = tmp_path / "openapi.json"
        with open(snapshot, "w") as fp:
            json.dump(spec, fp, indent=2)
        assert snapshot.exists()


def test_get_blog_list():
    with TestClient(app) as client:
        response = client.post("/blogs/", json={"title": "Yolo"})
        assert response.is_success

        response = client.get("/blogs/")
        assert response.is_success


def test_openapi_class_names_are_unique() -> None:
    """Restly warns when two classes in the API have the same name: OpenAPI
    then shows them under long names that can change, and generated clients
    use these names as type names. This test turns the warning into an error."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        app.openapi()
