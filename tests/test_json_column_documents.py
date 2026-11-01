"""A JSON column whose shape is declared as a nested pydantic model.

Typing the schema field as a nested model is how a document column gets a
validated shape on the way in. The value then arrives at the ORM as a model
instance, and a ``JSON`` column binds through ``json.dumps``, which cannot
take one: the write used to fail at flush with the model sitting in the bind
parameters. Restly owns the schema-to-model translation, so it dumps the model
to plain JSON there.

A ``TypeDecorator`` over ``JSON`` is left alone: it has its own bind
processor, and may well want the object as it stands.
"""

import asyncio
import dataclasses
import json
import math
from collections import deque
from collections.abc import Iterable, Iterator
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any
from uuid import UUID

import fastapi
import pydantic
import pytest
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.db._config import _fr_config
from fastapi_restly.testing._client import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    app = FastAPI()
    yield RestlyTestClient(app)


class Address(pydantic.BaseModel):
    street: str
    number: int = 0


def _define_model(name: str, table: str):
    return type(
        name,
        (fr.IDMixin, SharedBase),
        {
            "__tablename__": table,
            "__annotations__": {
                "title": Mapped[str],
                "address": Mapped[dict],
                "history": Mapped[list],
            },
            "address": mapped_column(sqlalchemy.JSON),
            "history": mapped_column(sqlalchemy.JSON, insert_default=list),
        },
    )


class DocSchema(fr.IDSchema):
    title: str
    address: Address
    history: list[Address] = []


class _PrivateDocument(pydantic.BaseModel):
    label: str = "visible"
    secret: fr.WriteOnly[str] = "default-secret"


class _ExcludedDocument(pydantic.BaseModel):
    secret: str = pydantic.Field(default="default-secret", exclude=True)


class _RequiredPrivateDocument(pydantic.BaseModel):
    secret: fr.WriteOnly[str]


class _ExtraDocument(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow")


class _DocumentTree(pydantic.BaseModel):
    documents: dict[str, list[_PrivateDocument]]


class _DocumentQueue(pydantic.BaseModel):
    documents: deque[_PrivateDocument]


@dataclasses.dataclass
class _DataclassDocument:
    label: str = "visible"


class _DataclassTree(pydantic.BaseModel):
    document: _DataclassDocument


class _DocumentIterator(pydantic.BaseModel):
    documents: Iterable[_PrivateDocument]


class _PrivateKey(_PrivateDocument):
    model_config = pydantic.ConfigDict(frozen=True)


@dataclasses.dataclass(frozen=True)
class _DataclassKey:
    label: str = "key"


class _DocumentKeys(pydantic.BaseModel):
    documents: dict[_PrivateKey | _DataclassKey, str]


class _DocumentChoice(Enum):
    PRIVATE = _PrivateDocument()
    EXCLUDED = _ExcludedDocument()
    DATACLASS = _DataclassDocument()


class _DocumentEnumTree(pydantic.BaseModel):
    choice: _DocumentChoice


class _SecretDocument(pydantic.BaseModel):
    token: pydantic.SecretStr


class _NoSecretYetDocument(pydantic.BaseModel):
    """A secret field without a value fails too, so the first write of such a
    document fails, not the first with a secret."""

    token: pydantic.SecretStr | None = None


class _SecretValuesDocument(pydantic.BaseModel):
    tokens: dict[str, pydantic.SecretStr]


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "document_type, document",
    [
        (_PrivateDocument, _PrivateDocument()),
        (_RequiredPrivateDocument, _RequiredPrivateDocument(secret="required-secret")),
        (_ExcludedDocument, _ExcludedDocument()),
        (list[_PrivateDocument], [_PrivateDocument(secret="list-secret")]),
        (tuple[_PrivateDocument, ...], (_PrivateDocument(),)),
        (_ExtraDocument, _ExtraDocument(nested=_PrivateDocument())),
        (_DocumentTree, _DocumentTree(documents={"nested": [_PrivateDocument()]})),
        (_DocumentQueue, _DocumentQueue(documents=deque([_PrivateDocument()]))),
        (_DataclassTree, _DataclassTree(document=_DataclassDocument())),
        (_DocumentIterator, _DocumentIterator(documents=[_PrivateDocument()])),
        (_DocumentKeys, _DocumentKeys(documents={_PrivateKey(): "value"})),
        (_DocumentKeys, _DocumentKeys(documents={_DataclassKey(): "value"})),
        (_DocumentEnumTree, _DocumentEnumTree(choice=_DocumentChoice.PRIVATE)),
        (_DocumentEnumTree, _DocumentEnumTree(choice=_DocumentChoice.EXCLUDED)),
        (_DocumentEnumTree, _DocumentEnumTree(choice=_DocumentChoice.DATACLASS)),
        (
            pydantic.RootModel[list[_PrivateDocument]],
            pydantic.RootModel[list[_PrivateDocument]]([_PrivateDocument()]),
        ),
        # a JSON dump masks a secret, so the column would get "**********"
        (_SecretDocument, _SecretDocument(token=pydantic.SecretStr("s3cret"))),
        (_NoSecretYetDocument, _NoSecretYetDocument()),
        (
            _SecretValuesDocument,
            _SecretValuesDocument(tokens={"api": pydantic.SecretStr("s3cret")}),
        ),
        # not in a model: the dump of a dict or a list masks a secret too
        (dict[str, pydantic.SecretStr], {"api": pydantic.SecretStr("s3cret")}),
        (list[_DataclassDocument], [_DataclassDocument()]),
    ],
)
def test_unsupported_documents_are_rejected_before_writing(
    sync_db, operation, document_type, document
):
    class Document(fr.IDMixin, SharedBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    schema = pydantic.create_model("DocumentInput", payload=(document_type, ...))
    engine, make_session = sync_db
    SharedBase.metadata.create_all(engine)
    original = {"label": "original", "secret": "stored-secret"}
    with make_session() as session:
        if operation == "update":
            row = Document(payload=original.copy())
            session.add(row)
            session.commit()

        with pytest.raises(fr.exc.RestlyConfigurationError, match="TypeDecorator"):
            if operation == "create":
                fr.objects.make_new_object(session, Document, schema(payload=document))
            else:
                fr.objects.update_object(session, row, schema(payload=document))

        session.commit()
        rows = session.scalars(sqlalchemy.select(Document)).all()
        assert [row.payload for row in rows] == (
            [] if operation == "create" else [original]
        )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_http_create_rejects_writeonly_json_without_committing(request, asynchronous):
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class Document(fr.IDMixin, SharedBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    class DocumentSchema(fr.IDSchema):
        payload: _PrivateDocument

    @fr.include_view(client.app)
    class DocumentView(fr.AsyncRestView if asynchronous else fr.RestView):
        prefix = "/private-documents"
        model = Document
        schema = DocumentSchema

    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    with pytest.raises(fr.exc.RestlyConfigurationError, match="Document.payload"):
        client.post("/private-documents", json={"payload": {"secret": "input-secret"}})
    assert client.get("/private-documents").json()["total_count"] == 0


def test_create_stores_a_nested_model_as_plain_json(client):
    Doc = _define_model("AsyncDoc", "jcd_async_docs")

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

    create_tables()

    body = client.post(
        "/docs",
        json={
            "title": "t",
            "address": {"street": "Main", "number": 4},
            "history": [{"street": "Old"}],
        },
    ).json()

    assert body["address"] == {"street": "Main", "number": 4}
    assert body["history"] == [{"street": "Old", "number": 0}]
    assert client.get("/docs/1").json() == body


def test_update_replaces_the_document(client):
    Doc = _define_model("AsyncPatchDoc", "jcd_async_patch_docs")

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

    create_tables()
    client.post("/docs", json={"title": "t", "address": {"street": "Main"}})

    body = client.patch("/docs/1", json={"address": {"street": "Side", "number": 9}})

    assert body.json()["address"] == {"street": "Side", "number": 9}


def test_sync_create_stores_a_nested_model_as_plain_json(sync_client):
    """Parity: both variants share the schema-to-model translation."""
    Doc = _define_model("SyncDoc", "jcd_sync_docs")

    @fr.include_view(sync_client.app)
    class DocView(fr.RestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])

    body = sync_client.post(
        "/docs", json={"title": "t", "address": {"street": "Main", "number": 4}}
    ).json()

    assert body["address"] == {"street": "Main", "number": 4}


def test_the_stored_value_is_a_dict_not_a_model(sync_client):
    """Asserted on the row, not the response: the response would look right
    either way, because the response schema re-validates a dict back into the
    nested model."""
    Doc = _define_model("StoredDoc", "jcd_stored_docs")

    @fr.include_view(sync_client.app)
    class DocView(fr.RestView):
        prefix = "/docs"
        model = Doc
        schema = DocSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    sync_client.post(
        "/docs",
        json={
            "title": "t",
            "address": {"street": "Main"},
            "history": [{"street": "O"}],
        },
    )

    with fr.open_session() as session:
        row = session.get(Doc, 1)
        assert isinstance(row.address, dict)
        assert row.history == [{"street": "O", "number": 0}]


_UUID = "6f1c3b0e-8a52-4d2b-9a77-0c4e5f6a7b8c"


@pytest.mark.parametrize("asynchronous", [True, False], ids=["async", "sync"])
@pytest.mark.parametrize(
    "field_type, sent, stored",
    [
        pytest.param(
            dict[str, Address],
            {"home": {"street": "Main"}},
            {"home": {"street": "Main", "number": 0}},
            id="dict-of-models",
        ),
        pytest.param(
            list[list[Address]],
            [[{"street": "Main"}]],
            [[{"street": "Main", "number": 0}]],
            id="nested-lists",
        ),
        pytest.param(
            list[datetime],
            ["2026-01-02T03:04:05Z"],
            ["2026-01-02T03:04:05Z"],
            id="datetimes",
        ),
        pytest.param(dict[str, UUID], {"key": _UUID}, {"key": _UUID}, id="uuids"),
        pytest.param(set[str], ["a"], ["a"], id="set"),
    ],
)
def test_a_value_inside_a_container_is_stored_as_json_data(
    request, asynchronous, field_type, sent, stored
):
    """Pydantic dumps the whole value in JSON mode, also a model in a dict,
    and a value that ``json.dumps`` cannot take, such as a ``datetime``."""
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class Document(fr.IDMixin, SharedBase):
        payload: Mapped[Any] = mapped_column(sqlalchemy.JSON)

    class DocumentSchema(fr.IDSchema):
        payload: field_type

    @fr.include_view(client.app)
    class DocumentView(fr.AsyncRestView if asynchronous else fr.RestView):
        prefix = "/documents"
        model = Document
        schema = DocumentSchema

    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    # An empty value first, so that the update changes the row.
    client.post("/documents", json={"payload": type(sent)()})
    client.patch("/documents/1", json={"payload": sent})
    client.post("/documents", json={"payload": sent})

    stmt = sqlalchemy.select(Document.payload).order_by(Document.id)
    assert _stored(stmt, asynchronous=asynchronous) == [stored, stored]


@pytest.mark.skipif(
    tuple(map(int, fastapi.__version__.split(".")[:2])) < (0, 130),
    reason="FastAPI before 0.130 writes a response with Starlette's "
    "JSONResponse, which refuses NaN, also for a field of type float",
)
@pytest.mark.parametrize("asynchronous", [True, False], ids=["async", "sync"])
def test_a_nan_in_a_container_is_stored_as_a_nan(request, asynchronous):
    """Python's ``json`` reads ``NaN``, so a body can hold it. The dump keeps
    it, as for a field of type ``float``: the row then reads back as it was
    written. A dump to ``null`` would store a row that its schema cannot
    read."""
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class Series(fr.IDMixin, SharedBase):
        points: Mapped[Any] = mapped_column(sqlalchemy.JSON)

    class SeriesSchema(fr.IDSchema):
        points: list[float]

    @fr.include_view(client.app)
    class SeriesView(fr.AsyncRestView if asynchronous else fr.RestView):
        prefix = "/series"
        model = Series
        schema = SeriesSchema

    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    # httpx refuses to encode NaN, so the body is sent as text.
    response = client.post(
        "/series",
        content='{"points": [1.0, NaN, Infinity]}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 201, response.text

    client.get("/series/1").raise_for_status()
    stmt = sqlalchemy.select(Series.points)
    [points] = _stored(stmt, asynchronous=asynchronous)
    assert points[0] == 1.0
    assert math.isnan(points[1])
    assert points[2] == math.inf


def test_json_null_and_an_sql_expression_go_to_the_column_as_they_are(sync_db):
    """SQLAlchemy's ``JSON.NULL`` stores JSON ``null``, and ``null()`` stores
    SQL ``NULL``. A dump would turn them into other values."""

    class Note(fr.IDMixin, SharedBase):
        body: Mapped[Any] = mapped_column(sqlalchemy.JSON, nullable=True)

    class NoteInput(pydantic.BaseModel):
        body: Any

    engine, make_session = sync_db
    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        for body in (sqlalchemy.JSON.NULL, sqlalchemy.null()):
            fr.objects.make_new_object(session, Note, NoteInput(body=body))
        session.commit()
        stmt = sqlalchemy.select(sqlalchemy.cast(Note.body, sqlalchemy.String))
        assert list(session.scalars(stmt.order_by(Note.id))) == ["null", None]


@pytest.mark.parametrize("asynchronous", [True, False], ids=["async", "sync"])
def test_a_model_annotation_gets_a_derived_schema(request, asynchronous):
    """``Mapped[Address]`` on a JSON column gives the field type ``Address``,
    as ``Mapped[dict[str, Address]]`` gives ``dict[str, Address]``."""
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class Shop(fr.IDMixin, SharedBase):
        address: Mapped[Address] = mapped_column(sqlalchemy.JSON)
        old_address: Mapped[Address | None] = mapped_column(sqlalchemy.JSON)

    @fr.include_view(client.app)
    class ShopView(fr.AsyncRestView if asynchronous else fr.RestView):
        prefix = "/shops"
        model = Shop

    fields = ShopView.schema.model_fields
    assert fields["address"].annotation is Address
    assert fields["address"].is_required()
    assert fields["old_address"].annotation == Address | None
    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])

    client.post("/shops", json={"address": {}}, assert_status_code=422)
    created = client.post("/shops", json={"address": {"street": "Main"}}).json()
    assert created["address"] == {"street": "Main", "number": 0}
    assert created["old_address"] is None
    updated = client.patch(
        "/shops/1", json={"old_address": {"street": "Dock", "number": 2}}
    ).json()
    assert updated["old_address"] == {"street": "Dock", "number": 2}
    assert client.get("/shops/1").json() == updated

    stmt = sqlalchemy.select(Shop.old_address)
    assert _stored(stmt, asynchronous=asynchronous) == [{"street": "Dock", "number": 2}]


class _RoundTripDocument(pydantic.BaseModel):
    """A document that Pydantic reads back only from the right dump: it reads
    a field by its alias, refuses an extra key, and reads a ``Json`` field
    from text."""

    model_config = pydantic.ConfigDict(extra="forbid")
    street_name: str = pydantic.Field(alias="streetName")
    sizes: pydantic.Json[list[int]]

    @pydantic.computed_field
    @property
    def size_count(self) -> int:
        return len(self.sizes)


@pytest.mark.parametrize("asynchronous", [True, False], ids=["async", "sync"])
def test_a_document_reads_back_as_it_was_written(request, asynchronous):
    """The column gets the document by the names of its fields, without the
    computed field, and with the ``Json`` field as text. A response reads a
    document by the names of its fields, so it reads it back. Before, the
    column also got the computed field and the ``Json`` field as a list, and
    each response with the row was a 500."""
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class Shop(fr.IDMixin, SharedBase):
        document: Mapped[_RoundTripDocument] = mapped_column(sqlalchemy.JSON)

    @fr.include_view(client.app)
    class ShopView(fr.AsyncRestView if asynchronous else fr.RestView):
        prefix = "/shops"
        model = Shop

    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])

    sent = {"streetName": "Main", "sizes": "[1, 2]"}
    created = client.post("/shops", json={"document": sent}).json()
    shown = {"streetName": "Main", "sizes": [1, 2], "size_count": 2}
    assert created["document"] == shown
    assert client.get("/shops/1").json()["document"] == shown
    stmt = sqlalchemy.select(Shop.document)
    assert _stored(stmt, asynchronous=asynchronous) == [
        {"street_name": "Main", "sizes": "[1,2]"}
    ]


@pytest.mark.skipif(
    not hasattr(pydantic.fields.FieldInfo, "exclude_if"),
    reason="Pydantic has exclude_if from version 2.12",
)
def test_a_field_with_exclude_if_is_rejected_before_writing(sync_db):
    """The dump leaves the field out when the condition holds. The document is
    refused also when it does not hold, so the first write fails, not the
    first with such a value."""

    class Note(pydantic.BaseModel):
        text: str = pydantic.Field("", exclude_if=lambda value: value == "")

    class Document(fr.IDMixin, SharedBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    schema = pydantic.create_model("DocumentInput", payload=(Note, ...))
    engine, make_session = sync_db
    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        with pytest.raises(
            fr.exc.RestlyConfigurationError,
            match=r"Document.payload: Note.text has exclude_if",
        ):
            fr.objects.make_new_object(
                session, Document, schema(payload=Note(text="kept"))
            )


def _stored(stmt, *, asynchronous):
    """The rows of ``stmt``, read in a new session."""
    if not asynchronous:
        with fr.open_session() as session:
            return list(session.scalars(stmt))

    async def read():
        async with fr.open_async_session() as session:
            return list(await session.scalars(stmt))

    return asyncio.run(read())


class _PydanticAddress(sqlalchemy.types.TypeDecorator):
    """A column type that wants the model itself, not a dict."""

    impl = sqlalchemy.JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        assert isinstance(value, Address), f"expected a model, got {type(value)}"
        return json.loads(value.model_dump_json())

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return value


class _OpaqueToken:
    def __init__(self, value: str) -> None:
        self.value = value


class _OpaquePayload(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    token: _OpaqueToken

    @pydantic.field_validator("token", mode="before")
    @classmethod
    def parse_token(cls, value: Any) -> Any:
        return _OpaqueToken(value) if isinstance(value, str) else value


class _OpaquePayloadType(sqlalchemy.types.TypeDecorator):
    """A column type that serializes a value that Pydantic cannot dump."""

    impl = sqlalchemy.String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        assert isinstance(value, _OpaquePayload)
        return value.token.value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return _OpaquePayload(token=_OpaqueToken(value))


class _OpaqueSchema(fr.IDSchema):
    payload: fr.WriteOnly[_OpaquePayload]


def _define_opaque_model(name: str, table: str):
    return type(
        name,
        (fr.IDMixin, SharedBase),
        {
            "__tablename__": table,
            "__annotations__": {"payload": Mapped[_OpaquePayload]},
            "payload": mapped_column(_OpaquePayloadType),
        },
    )


def test_a_type_decorator_still_receives_the_model(sync_client):
    class Decorated(fr.IDMixin, SharedBase):
        __tablename__ = "jcd_decorated"
        address: Mapped[Address] = mapped_column(_PydanticAddress)

    class DecoratedSchema(fr.IDSchema):
        address: Address

    @fr.include_view(sync_client.app)
    class DecoratedView(fr.RestView):
        prefix = "/decorated"
        model = Decorated
        schema = DecoratedSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])

    body = sync_client.post(
        "/decorated", json={"address": {"street": "Main", "number": 2}}
    ).json()

    assert body["address"] == {"street": "Main", "number": 2}


def test_create_leaves_pydantic_serialization_to_a_type_decorator(sync_client):
    Decorated = _define_opaque_model("CreateOpaque", "jcd_create_opaque")

    @fr.include_view(sync_client.app)
    class DecoratedView(fr.RestView):
        prefix = "/decorated"
        model = Decorated
        schema = _OpaqueSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])

    response = sync_client.post("/decorated", json={"payload": {"token": "created"}})

    assert response.status_code == 201
    with fr.open_session() as session:
        assert session.get(Decorated, 1).payload.token.value == "created"


def test_update_leaves_pydantic_serialization_to_a_type_decorator(sync_client):
    Decorated = _define_opaque_model("UpdateOpaque", "jcd_update_opaque")

    @fr.include_view(sync_client.app)
    class DecoratedView(fr.RestView):
        prefix = "/decorated"
        model = Decorated
        schema = _OpaqueSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    with fr.open_session() as session:
        session.add(Decorated(payload=_OpaquePayload(token=_OpaqueToken("old"))))
        session.commit()

    response = sync_client.patch("/decorated/1", json={"payload": {"token": "updated"}})

    assert response.status_code == 200
    with fr.open_session() as session:
        assert session.get(Decorated, 1).payload.token.value == "updated"


def test_the_free_object_helpers_dump_too(sync_client):
    """``fr.objects.make_new_object`` shares the translation, so a script
    writing outside a view gets the same value."""
    Doc = _define_model("HelperDoc", "jcd_helper_docs")
    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])

    with fr.open_session() as session:
        obj = fr.objects.make_new_object(
            session, Doc, DocSchema(id=1, title="t", address=Address(street="Main"))
        )
        assert isinstance(obj.address, dict)


class _PrivateDocumentType(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        assert isinstance(value, _PrivateDocument)
        return {"label": value.label, "secret": value.secret}


def test_type_decorator_owns_storage_of_excluded_document_fields(sync_client):
    class Decorated(fr.IDMixin, SharedBase):
        payload: Mapped[dict] = mapped_column(_PrivateDocumentType)

    class DocumentSchema(fr.IDSchema):
        payload: _PrivateDocument

    @fr.include_view(sync_client.app)
    class DocumentView(fr.RestView):
        prefix = "/private-documents"
        model = Decorated
        schema = DocumentSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    for method, url, secret in (
        (sync_client.post, "/private-documents", "created-secret"),
        (sync_client.patch, "/private-documents/1", "updated-secret"),
    ):
        body = method(url, json={"payload": {"secret": secret}}).json()
        assert body["payload"] == {"label": "visible"}
        with fr.open_session() as session:
            assert session.get(Decorated, body["id"]).payload == {
                "label": "visible",
                "secret": secret,
            }


def test_writeonly_marker_on_whole_json_column_is_supported(sync_client):
    class Document(fr.IDMixin, SharedBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    class DocumentSchema(fr.IDSchema):
        payload: fr.WriteOnly[Address]

    @fr.include_view(sync_client.app)
    class DocumentView(fr.RestView):
        prefix = "/private-documents"
        model = Document
        schema = DocumentSchema

    SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    body = sync_client.post(
        "/private-documents", json={"payload": {"street": "Main", "number": 4}}
    ).json()
    assert "payload" not in body
    with fr.open_session() as session:
        assert session.get(Document, body["id"]).payload == {
            "street": "Main",
            "number": 4,
        }


def test_constructed_document_keeps_pydantic_serialization(sync_db):
    class Document(fr.IDMixin, SharedBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    schema = pydantic.create_model("DocumentInput", payload=(Address, ...))
    engine, make_session = sync_db
    SharedBase.metadata.create_all(engine)
    address = Address.model_construct(number=4)
    with make_session() as session:
        obj = fr.objects.make_new_object(session, Document, schema(payload=address))
        session.add(obj)
        session.commit()
        assert session.get(Document, obj.id).payload == address.model_dump(mode="json")


def test_scalar_enums_in_documents_keep_pydantic_serialization(sync_db):
    class Status(Enum):
        OPEN = "open"
        CLOSED = "closed"

    class Label(str, Enum):
        PUBLIC = "public"

    class Priority(IntEnum):
        HIGH = 1

    class Payload(pydantic.BaseModel):
        status: Status
        label: Label = Label.PUBLIC
        priority: Priority = Priority.HIGH

    class Document(fr.IDMixin, SharedBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    schema = pydantic.create_model("DocumentInput", payload=(Payload, ...))
    engine, make_session = sync_db
    SharedBase.metadata.create_all(engine)
    with make_session() as session:
        row = fr.objects.make_new_object(
            session, Document, schema(payload=Payload(status=Status.OPEN))
        )
        session.add(row)
        for status in (Status.OPEN, Status.CLOSED):
            if status is Status.CLOSED:
                fr.objects.update_object(
                    session, row, schema(payload=Payload(status=status))
                )
            session.commit()
            session.expire_all()
            assert session.get(Document, row.id).payload == {
                "status": status.value,
                "label": "public",
                "priority": 1,
            }


@pytest.mark.parametrize("asynchronous", [True, False], ids=["async", "sync"])
def test_a_left_out_json_field_gets_the_column_default(request, asynchronous):
    """Restly calls the constructor with the fields that the client sent, as
    user code would, so the model's default applies to a field the client
    leaves out. Before, Restly passed ``None``, and a JSON column stored it
    as JSON ``null`` instead of the default. With a generated schema, whose
    field is typed ``dict``, the response then failed, and so did each later
    read of the row."""
    client = request.getfixturevalue("client" if asynchronous else "sync_client")

    class Sheet(fr.IDMixin, SharedBase):
        title: Mapped[str]
        data: Mapped[dict[str, Any]] = mapped_column(
            sqlalchemy.JSON, default_factory=dict
        )
        # The constructor requires it, so the create body requires it too.
        tags: Mapped[list[str]] = mapped_column(
            sqlalchemy.JSON, server_default=sqlalchemy.text("'[]'")
        )

    @fr.include_view(client.app)
    class SheetView(fr.AsyncRestView if asynchronous else fr.RestView):
        prefix = "/sheets"
        model = Sheet

    if asynchronous:
        create_tables()
    else:
        SharedBase.metadata.create_all(_fr_config.make_session.kw["bind"])
    response = client.post("/sheets/", json={"title": "a"}, assert_status_code=422)
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "tags"]]
    created = client.post("/sheets/", json={"title": "a", "tags": ["x"]}).json()
    assert (created["data"], created["tags"]) == ({}, ["x"])
    assert client.get(f"/sheets/{created['id']}").json() == created
    assert client.get("/sheets/").json()["data"] == [created]
