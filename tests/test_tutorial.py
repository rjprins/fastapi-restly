"""The complete file of docs/tutorial.md, run as a reader would run it.

The file is example-projects/blog/blog/part1.py, which the tutorial includes.
Loading it by path here makes the framework suite fail when the tutorial's
code stops working. Each block below is one section of the tutorial, with
the responses that the section shows.
"""

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.testing import RestlyTestClient

BLOG = Path(__file__).resolve().parents[1] / "example-projects" / "blog" / "blog"


def load_tutorial_file(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"tutorial_{name}", BLOG / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def use_empty_database() -> None:
    """Replace the file's blog.db with a new in-memory database.

    The file configures blog.db when it is imported, before any connection
    opens, so this replaces that configuration.
    """
    async_make_session = _fr_globals.async_make_session
    if async_make_session is not None:
        asyncio.run(async_make_session.kw["bind"].dispose())
    fr.configure(async_database_url="sqlite+aiosqlite:///:memory:")


def test_build_a_blog_api() -> None:
    part1 = load_tutorial_file("part1.py")
    use_empty_database()

    with RestlyTestClient(part1.app) as client:
        # Create and list posts
        post = client.post(
            "/posts", json={"title": "Hello", "content": "My first post"}
        ).json()
        assert post == {
            "id": 1,
            "title": "Hello",
            "content": "My first post",
            "published": False,
        }
        page = client.get("/posts").json()
        assert page == {
            "data": [post],
            "total_count": 1,
            "page": 1,
            "page_size": 50,
            "total_pages": 1,
        }
        schemas = part1.app.openapi()["components"]["schemas"]
        assert {"PostCreate", "PostResponse", "PostUpdate"} <= set(schemas)

        # Comment on posts
        missing = client.post(
            "/comments",
            json={"content": "Nice post", "post_id": 999},
            assert_status_code=404,
        )
        assert missing.json() == {"detail": "Id not found for post_id: 999"}
        comment = client.post(
            "/comments", json={"content": "Nice post", "post_id": 1}
        ).json()
        assert comment == {"id": 1, "content": "Nice post", "post_id": 1}

        # Find posts
        client.post(
            "/posts",
            json={"title": "Second", "content": "Now published", "published": True},
        )
        published = client.get("/posts?published=true").json()
        assert [p["title"] for p in published["data"]] == ["Second"]
        assert published["total_count"] == 1
        hello = client.get("/posts?title__icontains=hello").json()
        assert [p["title"] for p in hello["data"]] == ["Hello"]
        newest_first = client.get("/posts?sort=-id&page=1&page_size=10").json()
        assert [p["id"] for p in newest_first["data"]] == [2, 1]
