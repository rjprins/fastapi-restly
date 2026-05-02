import json

from blog.main import app
from fastapi.testclient import TestClient


def test_openapi_spec(tmp_path):
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        spec = response.json()
        routes: list[str] = []
        for path in spec["paths"]:
            for method in spec["paths"][path]:
                routes.append(f"{method.upper()} {path}")
        assert routes == [
            "GET /blogs/",
            "POST /blogs/",
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


def test_get_blog_listing():
    with TestClient(app) as client:
        response = client.post("/blogs/", json={"title": "Yolo"})
        assert response.is_success

        response = client.get("/blogs/")
        assert response.is_success
