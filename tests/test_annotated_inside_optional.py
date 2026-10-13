"""A field type with ``Annotated`` inside ``Optional`` works like the plain type.

Pydantic moves the metadata of an outer ``Annotated`` to ``field.metadata``,
but it keeps an ``Annotated`` inside ``Optional``:
``Annotated[IDRef[User], Field(description=...)] | None`` stays on the field
as ``Optional[Annotated[IDRef[User], FieldInfo]]``. Restly removed only the
``Optional`` before, so it did not see such a field as a reference or as a
nested schema. Each test runs both spellings.
"""

from typing import Annotated, Any, Optional

import pydantic
import pytest
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.views._base import _warn_scalar_named_reference_fields

from ._models import SharedBase
from .conftest import create_tables

spellings = pytest.mark.parametrize(
    "annotated", [False, True], ids=["plain", "annotated-inside-optional"]
)


def _optional(type_: Any, annotated: bool) -> Any:
    if annotated:
        return Optional[Annotated[type_, pydantic.Field(description="x")]]
    return Optional[type_]


@spellings
def test_an_async_view_loads_the_nested_schema_of_a_nested_schema(client, annotated):
    """Before, the loader did not load ``member.team``, and serialization
    then tried a lazy load, which an async session cannot do: 500."""

    class Team(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Member(fr.IDMixin, SharedBase):
        name: Mapped[str]
        team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
        team: Mapped[Team] = relationship(init=False)

    class Task(fr.IDMixin, SharedBase):
        title: Mapped[str]
        member_id: Mapped[int] = mapped_column(ForeignKey("member.id"))
        member: Mapped[Member] = relationship(init=False)

    class TeamSchema(fr.IDSchema):
        name: str

    class MemberSchema(fr.IDSchema):
        name: str
        team_id: int
        team: fr.ReadOnly[TeamSchema | None] = None

    class TaskSchema(fr.IDSchema):
        title: str
        member_id: int
        member: fr.ReadOnly[_optional(MemberSchema, annotated)] = None

    @fr.include_view(client.app)
    class TeamView(fr.AsyncRestView):
        prefix = "/teams"
        model = Team
        schema = TeamSchema

    @fr.include_view(client.app)
    class MemberView(fr.AsyncRestView):
        prefix = "/members"
        model = Member
        schema = MemberSchema

    @fr.include_view(client.app)
    class TaskView(fr.AsyncRestView):
        prefix = "/tasks"
        model = Task
        schema = TaskSchema

    create_tables()
    client.post("/teams/", json={"name": "T1"})
    client.post("/members/", json={"name": "M1", "team_id": 1})
    created = client.post("/tasks/", json={"title": "t", "member_id": 1})

    team = {"id": 1, "name": "T1"}
    assert created.json()["member"]["team"] == team
    assert client.get("/tasks/1").json()["member"]["team"] == team
    assert client.get("/tasks/").json()["data"][0]["member"]["team"] == team


@spellings
def test_a_reference_field_resolves_is_checked_and_is_documented(client, annotated):
    """Before, Restly did not see such an ``IDRef`` as a reference. A write
    to a field named after a foreign key column returned 500. A create through
    a field named after a relationship returned 500 when the foreign key is a
    required argument. Two references to different rows were saved without
    the "Conflicting references" 422. The misuse warning did not come, and
    OpenAPI did not name the resource of the relationship field."""

    class Author(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Comment(fr.IDMixin, SharedBase):
        body: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("author.id"))
        author: Mapped[Author] = relationship(default=None)

    class Note(fr.IDMixin, SharedBase):
        body: Mapped[str]
        author_id: Mapped[int | None] = mapped_column(
            ForeignKey("author.id"), default=None
        )
        author: Mapped[Author | None] = relationship(default=None)

    class AuthorSchema(fr.IDSchema):
        name: str

    class CommentSchema(fr.IDSchema):
        body: str
        author: _optional(fr.IDRef[Author], annotated) = None

    class NoteSchema(fr.IDSchema):
        body: str
        author_id: _optional(fr.IDRef[Author], annotated) = None
        author: _optional(fr.IDRef[Author], annotated) = None

    @fr.include_view(client.app)
    class AuthorView(fr.AsyncRestView):
        prefix = "/authors"
        model = Author
        schema = AuthorSchema

    @fr.include_view(client.app)
    class CommentView(fr.AsyncRestView):
        prefix = "/comments"
        model = Comment
        schema = CommentSchema

    @fr.include_view(client.app)
    class NoteView(fr.AsyncRestView):
        prefix = "/notes"
        model = Note
        schema = NoteSchema

    # The lint that ``fr.configure(warn_on_misuse=True)`` turns on.
    with pytest.warns(fr.exc.RestlyMisuseWarning, match="NoteSchema.author_id"):
        _warn_scalar_named_reference_fields(NoteView)

    create_tables()
    client.post("/authors/", json={"name": "A1"})
    client.post("/authors/", json={"name": "A2"})

    created = client.post("/comments/", json={"body": "hi", "author": 1})
    assert created.json()["author"] == 1

    note = client.post("/notes/", json={"body": "hi", "author_id": 1})
    assert note.json()["author_id"] == 1
    patched = client.patch(f"/notes/{note.json()['id']}", json={"author_id": 2})
    assert patched.json()["author_id"] == 2

    response = client.post(
        "/notes/",
        json={"body": "hi", "author_id": 1, "author": 2},
        assert_status_code=422,
    )
    assert "Conflicting references" in response.json()["detail"]

    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    author = schemas[CommentView.schema_response.__name__]["properties"]["author"]
    assert author["x-resource-ref"] == "authors"
