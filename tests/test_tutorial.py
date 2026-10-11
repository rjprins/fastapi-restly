"""The complete files of the two tutorials, run as a reader would run them.

docs/tutorial.md includes example-projects/blog/blog/part1.py, and
docs/tutorial_customizing.md includes example-projects/blog/blog/main.py.
Loading them by path here makes the framework suite fail when a tutorial's
code stops working. Each block below is one section of a tutorial, with the
responses that the section shows.
"""

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.testing import RestlyTestClient

BLOG = Path(__file__).resolve().parents[1] / "example-projects" / "blog" / "blog"
ANNA = {"X-User-ID": "1"}
BEN = {"X-User-ID": "2"}


def load_tutorial_file(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"tutorial_{name}", BLOG / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def use_empty_database() -> None:
    """The reader deletes blog.db. Here, a new in-memory database replaces it.

    The file configures blog.db when it is imported, before any connection
    opens, so the first call replaces that configuration too.
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


def test_add_authors_and_drafts(capsys: pytest.CaptureFixture[str]) -> None:
    main = load_tutorial_file("main.py")
    use_empty_database()

    with RestlyTestClient(main.app) as client:
        # Know who is calling
        for operations in main.app.openapi()["paths"].values():
            for operation in operations.values():
                names = [p["name"] for p in operation.get("parameters", [])]
                assert "X-User-ID" in names

        # Record the author
        post = client.post(
            "/posts",
            json={"title": "Hello", "content": "World", "author_id": 2},
            headers=ANNA,
        ).json()
        assert post["id"] == 1
        assert post["author_id"] == 1
        assert post["published"] is False

        # Hide drafts from other readers
        client.get("/posts/1", headers=BEN, assert_status_code=404)
        assert client.get("/posts").json()["total_count"] == 0
        client.get("/posts/1", headers=ANNA)
        client.post(
            "/comments",
            json={"content": "Nice post", "post_id": 1},
            headers=BEN,
            assert_status_code=404,
        )

        # Only the author may change a post
        anonymous = client.post(
            "/posts",
            json={"title": "Hi", "content": "Anonymous"},
            assert_status_code=401,
        )
        assert anonymous.json() == {"detail": "Log in to write"}
        client.patch(
            "/posts/1", json={"title": "Edited"}, headers=BEN, assert_status_code=404
        )

        # Publish a draft
        published = client.post("/posts/1/publish", headers=ANNA).json()
        assert published["published"] is True
        client.get("/posts/1")
        forbidden = client.patch(
            "/posts/1", json={"title": "Edited"}, headers=BEN, assert_status_code=403
        )
        assert forbidden.json() == {"detail": "Only the author can change this"}
        again = client.post("/posts/1/publish", headers=ANNA, assert_status_code=409)
        assert again.json() == {"detail": "Already published"}

        # Tell subscribers about a new post
        capsys.readouterr()
        second = client.post(
            "/posts", json={"title": "Second", "content": "More"}, headers=ANNA
        ).json()
        assert second["id"] == 2
        client.post("/posts/2/publish", headers=ANNA)
        assert capsys.readouterr().out == "New post: Second\n"

    # Share the rules with comments: the reader deletes blog.db again.
    use_empty_database()
    with RestlyTestClient(main.app) as client:
        client.post("/posts", json={"title": "Hello", "content": "World"}, headers=ANNA)
        note = client.post(
            "/comments", json={"content": "Note to self", "post_id": 1}, headers=ANNA
        ).json()
        assert note["author_id"] == 1
        assert client.get("/comments", headers=BEN).json()["total_count"] == 0
        client.post("/posts/1/publish", headers=ANNA)
        nice = client.post(
            "/comments", json={"content": "Nice post", "post_id": 1}, headers=BEN
        ).json()
        assert nice["id"] == 2
        assert nice["author_id"] == 2
        forbidden = client.patch(
            "/comments/2",
            json={"content": "Edited"},
            headers=ANNA,
            assert_status_code=403,
        )
        assert forbidden.json() == {"detail": "Only the author can change this"}
