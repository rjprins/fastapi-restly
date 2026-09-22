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

import dataclasses
import json
from collections import deque
from collections.abc import Iterable, Iterator
from typing import Any

import pydantic
import pytest
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.testing._client import RestlyTestClient

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
        (fr.IDBase,),
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
        (
            pydantic.RootModel[list[_PrivateDocument]],
            pydantic.RootModel[list[_PrivateDocument]]([_PrivateDocument()]),
        ),
    ],
)
def test_unsupported_documents_are_rejected_before_writing(
    sync_db, operation, document_type, document
):
    class Document(fr.IDBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    schema = pydantic.create_model("DocumentInput", payload=(document_type, ...))
    engine, make_session = sync_db
    fr.DataclassBase.metadata.create_all(engine)
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

    class Document(fr.IDBase):
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
        fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
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

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])

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

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
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
        (fr.IDBase,),
        {
            "__tablename__": table,
            "__annotations__": {"payload": Mapped[_OpaquePayload]},
            "payload": mapped_column(_OpaquePayloadType),
        },
    )


def test_a_type_decorator_still_receives_the_model(sync_client):
    class Decorated(fr.IDBase):
        __tablename__ = "jcd_decorated"
        address: Mapped[Address] = mapped_column(_PydanticAddress)

    class DecoratedSchema(fr.IDSchema):
        address: Address

    @fr.include_view(sync_client.app)
    class DecoratedView(fr.RestView):
        prefix = "/decorated"
        model = Decorated
        schema = DecoratedSchema

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])

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

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])

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

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
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
    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])

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
    class Decorated(fr.IDBase):
        payload: Mapped[dict] = mapped_column(_PrivateDocumentType)

    class DocumentSchema(fr.IDSchema):
        payload: _PrivateDocument

    @fr.include_view(sync_client.app)
    class DocumentView(fr.RestView):
        prefix = "/private-documents"
        model = Decorated
        schema = DocumentSchema

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
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
    class Document(fr.IDBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    class DocumentSchema(fr.IDSchema):
        payload: fr.WriteOnly[Address]

    @fr.include_view(sync_client.app)
    class DocumentView(fr.RestView):
        prefix = "/private-documents"
        model = Document
        schema = DocumentSchema

    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])
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
    class Document(fr.IDBase):
        payload: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    schema = pydantic.create_model("DocumentInput", payload=(Address, ...))
    engine, make_session = sync_db
    fr.DataclassBase.metadata.create_all(engine)
    address = Address.model_construct(number=4)
    with make_session() as session:
        obj = fr.objects.make_new_object(session, Document, schema(payload=address))
        session.add(obj)
        session.commit()
        assert session.get(Document, obj.id).payload == address.model_dump(mode="json")
