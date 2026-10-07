"""An explicit write schema says what a client may write.

The write path used to check the response schema's ReadOnly markers as well.
A field that an explicit ``schema_create`` or ``schema_update`` declares, but
the response schema marks ReadOnly, was then dropped: a 500 for a required
field, a lost value for an optional one, an ignored PATCH, and a 500 for a
reference that the response schema embeds as a nested object.
"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient

from .conftest import create_tables


@dataclass
class _Models:
    Story: Any
    Comment: Any
    # ``story_id`` and ``note`` are ReadOnly, ``story`` a ReadOnly nested object.
    CommentSchema: Any
    # The same, with ``story`` a plain nested object.
    CommentReadNested: Any
    CommentCreate: Any
    CommentCreateByRef: Any
    CommentCreateGuarded: Any
    CommentUpdate: Any


def _models() -> _Models:
    class Story(fr.IDBase):
        title: Mapped[str]

    class Comment(fr.IDBase):
        body: Mapped[str]
        story_id: Mapped[int] = mapped_column(ForeignKey("story.id"))
        story: Mapped[Story] = relationship(default=None)
        note: Mapped[str | None] = mapped_column(default=None)

    class StorySchema(fr.IDSchema):
        title: str

    class CommentSchema(fr.IDSchema):
        body: str
        story_id: fr.ReadOnly[int]
        note: fr.ReadOnly[str | None] = None
        story: fr.ReadOnly[StorySchema]

    class CommentReadNested(fr.IDSchema):
        body: str
        story_id: fr.ReadOnly[int]
        story: StorySchema

    class CommentCreate(fr.BaseSchema):
        body: str
        story_id: fr.MustExist[int, Story]
        note: str | None = None

    class CommentCreateByRef(fr.BaseSchema):
        body: str
        story: fr.IDRef[Story]

    class CommentCreateGuarded(fr.IDSchema):
        body: str
        story_id: fr.MustExist[int, Story]
        note: fr.ReadOnly[str | None] = None

    class CommentUpdate(fr.BaseSchema):
        body: str | None = None
        note: str | None = None

    return _Models(
        Story=Story,
        Comment=Comment,
        CommentSchema=CommentSchema,
        CommentReadNested=CommentReadNested,
        CommentCreate=CommentCreate,
        CommentCreateByRef=CommentCreateByRef,
        CommentCreateGuarded=CommentCreateGuarded,
        CommentUpdate=CommentUpdate,
    )


# --- Views ---------------------------------------------------------------


@dataclass
class _Api:
    models: _Models
    make_client: Callable[..., RestlyTestClient]


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def api(request: pytest.FixtureRequest) -> Iterator[_Api]:
    models = _models()
    if request.param is fr.RestView:
        engine, _ = request.getfixturevalue("sync_db")
        fr.DataclassBase.metadata.create_all(engine)
    else:
        create_tables()

    def make_client(**comment_view_attrs: Any) -> RestlyTestClient:
        app = FastAPI()
        story_view = type(
            "StoryView", (request.param,), {"prefix": "/stories", "model": models.Story}
        )
        comment_view = type(
            "CommentView",
            (request.param,),
            {
                "prefix": "/comments",
                "model": models.Comment,
                "schema": models.CommentSchema,
                **comment_view_attrs,
            },
        )
        fr.include_view(app, story_view)
        fr.include_view(app, comment_view)
        client = RestlyTestClient(app)
        if not client.get("/stories/").json()["data"]:
            client.post("/stories/", json={"title": "first"})
            client.post("/stories/", json={"title": "second"})
        return client

    yield _Api(models=models, make_client=make_client)


def test_required_create_field_is_written(api):
    client = api.make_client(schema_create=api.models.CommentCreate)

    created = client.post(
        "/comments/", json={"body": "hi", "story_id": 2}, assert_status_code=201
    ).json()

    assert created["story_id"] == 2
    assert created["story"] == {"id": 2, "title": "second"}
    assert client.get(f"/comments/{created['id']}").json()["story_id"] == 2


def test_optional_create_field_is_written(api):
    client = api.make_client(schema_create=api.models.CommentCreate)

    created = client.post(
        "/comments/",
        json={"body": "hi", "story_id": 1, "note": "pinned"},
        assert_status_code=201,
    ).json()

    assert created["note"] == "pinned"
    assert client.get(f"/comments/{created['id']}").json()["note"] == "pinned"


def test_explicit_update_field_is_written(api):
    client = api.make_client(
        schema_create=api.models.CommentCreate, schema_update=api.models.CommentUpdate
    )
    created = client.post("/comments/", json={"body": "hi", "story_id": 1}).json()

    updated = client.patch(
        f"/comments/{created['id']}", json={"note": "edited"}, assert_status_code=200
    ).json()

    assert updated["note"] == "edited"
    assert updated["body"] == "hi"
    assert client.get(f"/comments/{created['id']}").json()["note"] == "edited"


@pytest.mark.parametrize("read", ["CommentSchema", "CommentReadNested"])
def test_reference_the_response_embeds_is_written(api, read):
    client = api.make_client(
        schema=getattr(api.models, read), schema_create=api.models.CommentCreateByRef
    )

    created = client.post(
        "/comments/", json={"body": "hi", "story": 2}, assert_status_code=201
    ).json()

    assert created["story_id"] == 2
    assert created["story"] == {"id": 2, "title": "second"}


def test_missing_reference_is_still_checked(api):
    client = api.make_client(schema_create=api.models.CommentCreate)

    by_ref = api.make_client(schema_create=api.models.CommentCreateByRef)

    client.post(
        "/comments/", json={"body": "hi", "story_id": 999}, assert_status_code=404
    )
    by_ref.post("/comments/", json={"body": "hi", "story": 999}, assert_status_code=404)

    assert client.get("/comments/").json()["data"] == []


def test_create_only_field(api):
    # ReadOnly on the response schema, declared in schema_create, and a
    # generated schema_update: settable on create, frozen afterwards.
    client = api.make_client(schema_create=api.models.CommentCreate)
    created = client.post("/comments/", json={"body": "hi", "story_id": 1}).json()

    updated = client.patch(
        f"/comments/{created['id']}",
        json={"body": "edited", "story_id": 2},
        assert_status_code=200,
    ).json()

    assert updated["body"] == "edited"
    assert updated["story_id"] == 1
    assert "story_id" in _body_properties(client, "post", "/comments")
    assert "story_id" not in _body_properties(client, "patch", "/comments/{id}")


def test_write_schema_readonly_is_still_skipped(api):
    client = api.make_client(schema_create=api.models.CommentCreateGuarded)

    created = client.post(
        "/comments/",
        json={"id": 999, "body": "hi", "story_id": 1, "note": "sneaky"},
        assert_status_code=201,
    ).json()

    assert created["id"] != 999
    assert created["note"] is None


def test_generated_write_schemas_still_drop_readonly_fields(api):
    # The default stays safe: a generated write schema leaves out what the
    # response schema marks ReadOnly, so a client cannot set it.
    client = api.make_client()

    for method, path in (("post", "/comments"), ("patch", "/comments/{id}")):
        properties = _body_properties(client, method, path)
        assert "body" in properties
        assert not {"id", "story_id", "note", "story"} & properties


def _body_properties(client: RestlyTestClient, method: str, path: str) -> set[str]:
    spec = client.get("/openapi.json").json()
    body = spec["paths"][path][method]["requestBody"]["content"]["application/json"]
    name = body["schema"]["$ref"].rsplit("/", 1)[-1]
    return set(spec["components"]["schemas"][name].get("properties", {}))


# --- fr.objects ----------------------------------------------------------


class _Objects:
    """One interface over the sync and async ``fr.objects`` write functions."""

    def __init__(self, session: Any, sync: bool) -> None:
        self.session = session
        self.sync = sync

    async def make(self, model_cls: Any, schema_obj: Any) -> Any:
        if self.sync:
            return fr.objects.make_new_object(self.session, model_cls, schema_obj)
        return await fr.objects.async_make_new_object(
            self.session, model_cls, schema_obj
        )

    async def update(self, obj: Any, schema_obj: Any) -> Any:
        if self.sync:
            return fr.objects.update_object(self.session, obj, schema_obj)
        return await fr.objects.async_update_object(self.session, obj, schema_obj)

    async def flush(self) -> None:
        if self.sync:
            self.session.flush()
        else:
            await self.session.flush()


_Scenario = Callable[[_Objects, _Models], Awaitable[None]]


@pytest.fixture(params=["sync", "async"])
def run_objects(request: pytest.FixtureRequest) -> Callable[[_Scenario], None]:
    def run(scenario: _Scenario) -> None:
        models = _models()
        if request.param == "sync":
            engine, make_session = request.getfixturevalue("sync_db")
            fr.DataclassBase.metadata.create_all(engine)

            async def go_sync() -> None:
                with make_session() as session:
                    session.add_all(
                        [models.Story(title="first"), models.Story(title="second")]
                    )
                    session.flush()
                    await scenario(_Objects(session, sync=True), models)

            asyncio.run(go_sync())
            return

        async def go_async() -> None:
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with engine.begin() as conn:
                await conn.run_sync(fr.DataclassBase.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add_all(
                    [models.Story(title="first"), models.Story(title="second")]
                )
                await session.flush()
                await scenario(_Objects(session, sync=False), models)
            await engine.dispose()

        asyncio.run(go_async())

    return run


def test_objects_write_every_field_the_payload_declares(run_objects):
    async def scenario(objects: _Objects, models: _Models) -> None:
        comment = await objects.make(
            models.Comment, models.CommentCreate(body="hi", story_id=2, note="n")
        )
        await objects.flush()
        assert (comment.story_id, comment.note) == (2, "n")

        await objects.update(comment, models.CommentUpdate(note="edited"))
        assert comment.note == "edited"
        assert comment.body == "hi"

    run_objects(scenario)


def test_objects_resolve_a_reference_by_the_payload_schema(run_objects):
    async def scenario(objects: _Objects, models: _Models) -> None:
        comment = await objects.make(
            models.Comment, models.CommentCreateByRef(body="hi", story=2)
        )
        await objects.flush()
        assert comment.story_id == 2
        assert comment.story.title == "second"

    run_objects(scenario)


def test_objects_skip_the_payload_schemas_readonly_fields(run_objects):
    async def scenario(objects: _Objects, models: _Models) -> None:
        comment = await objects.make(
            models.Comment,
            models.CommentCreateGuarded(id=999, body="hi", story_id=1, note="x"),
        )
        await objects.flush()
        assert comment.id != 999
        assert comment.note is None

        # A read schema as the payload: its ReadOnly fields are not written.
        await objects.update(
            comment,
            models.CommentSchema.model_construct(
                _fields_set={"body", "story_id", "note"},
                body="edited",
                story_id=2,
                note="y",
            ),
        )
        assert (comment.body, comment.story_id, comment.note) == ("edited", 1, None)

    run_objects(scenario)


def test_objects_still_check_must_exist(run_objects):
    async def scenario(objects: _Objects, models: _Models) -> None:
        with pytest.raises(HTTPException) as exc:
            await objects.make(
                models.Comment, models.CommentCreate(body="hi", story_id=999)
            )
        assert exc.value.status_code == 404

    run_objects(scenario)


@pytest.mark.parametrize(
    "function",
    [
        fr.objects.make_new_object,
        fr.objects.update_object,
        fr.objects.async_make_new_object,
        fr.objects.async_update_object,
    ],
)
def test_objects_take_no_schema_argument(function):
    assert "schema_cls" not in inspect.signature(function).parameters
