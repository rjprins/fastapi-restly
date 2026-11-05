"""The rules that "Tutorial: Add Authors and Drafts" builds, one test each.

Anna (user 1) and Ben (user 2) play the two writers, as in the tutorial.
"""

import asyncio
import warnings

import pytest
from blog.main import Current, PostView, app

import fastapi_restly as fr
from fastapi_restly.exc import RestlyDuplicateSchemaNameWarning
from fastapi_restly.testing import RestlyTestClient

ANNA = {"X-User-ID": "1"}
BEN = {"X-User-ID": "2"}


def write_draft(client: RestlyTestClient, headers: dict[str, str] = ANNA) -> int:
    post = client.post(
        "/posts", json={"title": "Hello", "content": "World"}, headers=headers
    )
    return int(post.json()["id"])


def test_routes() -> None:
    routes = [
        f"{method.upper()} {path}"
        for path, methods in app.openapi()["paths"].items()
        for method in methods
    ]
    assert routes == [
        "POST /posts/{id}/publish",
        "GET /posts",
        "POST /posts",
        "GET /posts/{id}",
        "PATCH /posts/{id}",
        "DELETE /posts/{id}",
        "GET /comments",
        "POST /comments",
        "GET /comments/{id}",
        "PATCH /comments/{id}",
        "DELETE /comments/{id}",
    ]


def test_the_publish_route_documents_the_post_response() -> None:
    """``-> Post`` declares the view's response, as on the CRUD routes."""
    publish = app.openapi()["paths"]["/posts/{id}/publish"]["post"]
    body = publish["responses"]["200"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/PostResponse"}


def test_a_new_post_is_a_draft_by_the_caller(restly_client: RestlyTestClient) -> None:
    post = restly_client.post(
        "/posts",
        json={"title": "Hello", "content": "World", "published": True, "author_id": 2},
        headers=ANNA,
    ).json()

    # The read-only fields ignore the payload values.
    assert post["author_id"] == 1
    assert post["published"] is False


def test_an_anonymous_reader_cannot_write(restly_client: RestlyTestClient) -> None:
    restly_client.post(
        "/posts", json={"title": "Hello", "content": "World"}, assert_status_code=401
    )


def test_only_the_author_sees_a_draft(restly_client: RestlyTestClient) -> None:
    post_id = write_draft(restly_client)

    restly_client.get(f"/posts/{post_id}", headers=ANNA)
    restly_client.get(f"/posts/{post_id}", headers=BEN, assert_status_code=404)
    restly_client.get(f"/posts/{post_id}", assert_status_code=404)
    assert restly_client.get("/posts", headers=ANNA).json()["total_count"] == 1
    assert restly_client.get("/posts", headers=BEN).json()["total_count"] == 0
    assert restly_client.get("/posts").json()["total_count"] == 0


def test_others_cannot_change_or_comment_on_a_draft(
    restly_client: RestlyTestClient,
) -> None:
    post_id = write_draft(restly_client)

    restly_client.patch(
        f"/posts/{post_id}",
        json={"title": "Edited"},
        headers=BEN,
        assert_status_code=404,
    )
    restly_client.post(f"/posts/{post_id}/publish", headers=BEN, assert_status_code=404)
    restly_client.post(
        "/comments",
        json={"content": "Nice post", "post_id": post_id},
        headers=BEN,
        assert_status_code=404,
    )


def test_the_author_publishes_a_post_for_everyone(
    restly_client: RestlyTestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    post_id = write_draft(restly_client)

    post = restly_client.post(f"/posts/{post_id}/publish", headers=ANNA).json()

    assert post["published"] is True
    assert capsys.readouterr().out == "New post: Hello\n"
    restly_client.get(f"/posts/{post_id}")
    restly_client.post(
        f"/posts/{post_id}/publish", headers=ANNA, assert_status_code=409
    )


def test_only_the_author_changes_a_published_post(
    restly_client: RestlyTestClient,
) -> None:
    post_id = write_draft(restly_client)
    restly_client.post(f"/posts/{post_id}/publish", headers=ANNA)

    restly_client.patch(
        f"/posts/{post_id}",
        json={"title": "Edited"},
        headers=BEN,
        assert_status_code=403,
    )
    restly_client.delete(f"/posts/{post_id}", headers=BEN, assert_status_code=403)
    edited = restly_client.patch(
        f"/posts/{post_id}", json={"title": "Edited"}, headers=ANNA
    ).json()
    assert edited["title"] == "Edited"


def test_a_write_without_a_post_fails() -> None:
    """A new write action that changes no single post, such as one that
    archives all posts, has no `obj`. authorize refuses it until you allow it."""
    view = PostView(request=None, session=None)  # authorize uses neither
    with Current.bind(user_id=1), pytest.raises(fr.exc.Forbidden):
        asyncio.run(view.authorize("archive_all"))


def test_comments_follow_the_same_rules(restly_client: RestlyTestClient) -> None:
    post_id = write_draft(restly_client)
    restly_client.post(f"/posts/{post_id}/publish", headers=ANNA)

    comment = restly_client.post(
        "/comments", json={"content": "Nice post", "post_id": post_id}, headers=BEN
    ).json()

    assert comment["author_id"] == 2
    restly_client.patch(
        f"/comments/{comment['id']}",
        json={"content": "Edited"},
        headers=ANNA,
        assert_status_code=403,
    )
    restly_client.delete(f"/comments/{comment['id']}", headers=BEN)


def test_comments_on_a_hidden_draft_stay_hidden(
    restly_client: RestlyTestClient,
) -> None:
    post_id = write_draft(restly_client)
    comment = restly_client.post(
        "/comments", json={"content": "Note to self", "post_id": post_id}, headers=ANNA
    ).json()

    assert restly_client.get("/comments", headers=ANNA).json()["total_count"] == 1
    assert restly_client.get("/comments", headers=BEN).json()["total_count"] == 0
    assert restly_client.get("/comments").json()["total_count"] == 0
    restly_client.get(f"/comments/{comment['id']}", headers=BEN, assert_status_code=404)

    # the comment becomes visible together with its post
    restly_client.post(f"/posts/{post_id}/publish", headers=ANNA)
    assert restly_client.get("/comments", headers=BEN).json()["total_count"] == 1


def test_openapi_class_names_are_unique() -> None:
    """Restly warns when two classes in the API have the same name: OpenAPI
    then shows them under long names that can change, and generated clients
    use these names as type names. This test turns the warning into an error."""
    app.openapi_schema = None  # build it again, even if an earlier test cached it
    with warnings.catch_warnings():
        warnings.simplefilter("error", RestlyDuplicateSchemaNameWarning)
        app.openapi()
