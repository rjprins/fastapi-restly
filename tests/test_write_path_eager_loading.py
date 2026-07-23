"""Writes eager-load the relationships the response schema names.

Reads build loader options from the response schema and apply them in
``get_one`` / ``get_many``. Writes used to flush-and-refresh with none, so
``to_response_schema``'s ``getattr`` reached an unloaded relationship at
serialization time: a lazy load in the endpoint coroutine, where SQLAlchemy's
asyncio layer has no greenlet to suspend into. Every create and update whose
response schema embedded a relationship returned 500 for a row that had already
committed (y2zx).

``save_object`` now applies the same loader options reads use. It fills only
what is *unloaded*: a relationship the caller left loaded keeps its value,
because the reload runs without ``populate_existing``.
"""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import ForeignKey, event, select
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Mapped, joinedload, mapped_column, relationship, selectinload
from sqlalchemy.orm.attributes import set_committed_value

import fastapi_restly as fr
from fastapi_restly.db._globals import _fr_globals
from fastapi_restly.testing._client import RestlyTestClient
from fastapi_restly.views._base import (
    _relationship_reload_statement,
    _schema_relationships_are_loaded,
)

from .conftest import create_tables


@pytest.fixture
def sync_client(sync_db) -> Iterator[RestlyTestClient]:
    app = FastAPI()
    yield RestlyTestClient(app)


def _create_sync_tables():
    fr.DataclassBase.metadata.create_all(_fr_globals.make_session.kw["bind"])


# ---------------------------------------------------------------------------
# Company <- Owner <- Doc, so nesting is two levels deep behind the schema.
# ---------------------------------------------------------------------------


def _define_models():
    class Company(fr.IDBase):
        name: Mapped[str]

    class Owner(fr.IDBase):
        name: Mapped[str]
        company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), init=False)
        company: Mapped[Company] = relationship(init=False)

    class Doc(fr.IDBase):
        title: Mapped[str]
        owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id"), init=False)
        owner: Mapped[Owner] = relationship(init=False)

    return Company, Owner, Doc


def _define_schemas():
    class CompanyRef(fr.IDSchema):
        name: str

    class OwnerRead(fr.IDSchema):
        name: str
        company: CompanyRef  # second level -- exercises the recursive options

    class DocRead(fr.IDSchema):
        title: str
        owner: OwnerRead

    class DocCreate(fr.BaseSchema):
        title: str

    return DocRead, DocCreate


def _assert_nested(body: dict, *, title: str):
    assert body["title"] == title
    assert body["owner"]["name"] == "Alice"
    assert body["owner"]["company"]["name"] == "Initech"


def _build_nested_app(app):
    """Doc whose ``owner`` is populated only by stamping the FK column.

    The relationship object is never assigned, so it is unloaded from birth and
    a plain ``refresh`` cannot fill it.
    """
    Company, Owner, Doc = _define_models()
    DocRead, DocCreate = _define_schemas()
    owner_id: dict[str, int] = {}

    @fr.include_view(app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocRead
        schema_create = DocCreate

        async def make_new_object(self, schema_obj) -> Any:
            obj: Any = await super().make_new_object(schema_obj)
            obj.owner_id = owner_id["id"]
            return obj

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            company = Company(name="Initech")
            session.add(company)
            await session.flush()
            owner = Owner(name="Alice")
            owner.company_id = company.id
            session.add(owner)
            await session.flush()
            owner_id["id"] = owner.id
            doc = Doc(title="seeded")
            doc.owner_id = owner.id
            session.add(doc)
            await session.commit()

    asyncio.run(seed())


def test_async_create_serializes_nested_relationship(client):
    """The y2zx regression: POST used to 500 after committing the row."""
    _build_nested_app(client.app)

    response = client.post("/docs/", json={"title": "created"})

    assert response.status_code == 201, response.text
    _assert_nested(response.json(), title="created")


def test_async_update_serializes_nested_relationship(client):
    _build_nested_app(client.app)

    response = client.patch("/docs/1", json={"title": "renamed"})

    assert response.status_code == 200, response.text
    _assert_nested(response.json(), title="renamed")


def test_async_get_one_still_serializes_nested_relationship(client):
    """Reads already worked; guards against the write fix regressing them."""
    _build_nested_app(client.app)

    response = client.get("/docs/1")

    assert response.status_code == 200, response.text
    _assert_nested(response.json(), title="seeded")


def test_sync_create_serializes_nested_relationship(sync_client):
    """Sync views never raised, but paid hidden per-field SELECTs. Parity."""
    Company, Owner, Doc = _define_models()
    DocRead, DocCreate = _define_schemas()
    owner_id: dict[str, int] = {}

    @fr.include_view(sync_client.app)
    class DocView(fr.RestView):
        prefix = "/docs"
        model = Doc
        schema = DocRead
        schema_create = DocCreate

        def make_new_object(self, schema_obj) -> Any:
            obj: Any = super().make_new_object(schema_obj)
            obj.owner_id = owner_id["id"]
            return obj

    _create_sync_tables()
    with _fr_globals.make_session() as session:
        company = Company(name="Initech")
        session.add(company)
        session.flush()
        owner = Owner(name="Alice")
        owner.company_id = company.id
        session.add(owner)
        session.flush()
        owner_id["id"] = owner.id
        session.commit()

    response = sync_client.post("/docs/", json={"title": "created"})

    assert response.status_code == 201, response.text
    _assert_nested(response.json(), title="created")


# ---------------------------------------------------------------------------
# Reference-field shapes
# ---------------------------------------------------------------------------


def test_async_create_serializes_flat_idref(client):
    """``fr.IDRef`` renders a relationship as a bare id, and still needs it loaded."""

    class Author(fr.IDBase):
        name: Mapped[str]

    class Post(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("author.id"), init=False)
        author: Mapped[Author] = relationship(init=False)

    class PostRead(fr.IDSchema):
        title: str
        author: fr.IDRef[Author]

    class PostCreate(fr.BaseSchema):
        title: str
        author: fr.IDRef[Author]

    @fr.include_view(client.app)
    class PostView(fr.AsyncRestView):
        prefix = "/posts"
        model = Post
        schema = PostRead
        schema_create = PostCreate

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            session.add(Author(name="Ann"))
            await session.commit()

    asyncio.run(seed())

    response = client.post("/posts/", json={"title": "hello", "author": 1})

    assert response.status_code == 201, response.text
    assert response.json()["author"] == 1


def test_async_create_serializes_assigned_collection(client):
    """A to-many assigned during create is loaded at flush and unloaded by the
    refresh that follows it. The reload has to put it back."""

    class Tag(fr.IDBase):
        label: Mapped[str]
        doc_id: Mapped[int | None] = mapped_column(
            ForeignKey("tagged_doc.id"), init=False, nullable=True
        )

    class TaggedDoc(fr.IDBase):
        title: Mapped[str]
        tags: Mapped[list[Tag]] = relationship(init=False)

    class TagRef(fr.IDSchema):
        label: str

    class DocRead(fr.IDSchema):
        title: str
        tags: list[TagRef]

    class DocCreate(fr.BaseSchema):
        title: str

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = TaggedDoc
        schema = DocRead
        schema_create = DocCreate

        async def make_new_object(self, schema_obj) -> Any:
            obj: Any = await super().make_new_object(schema_obj)
            tags = (await self.session.scalars(select(Tag))).all()
            obj.tags = list(tags)
            return obj

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            session.add_all([Tag(label="a"), Tag(label="b")])
            await session.commit()

    asyncio.run(seed())

    response = client.post("/docs/", json={"title": "created"})

    assert response.status_code == 201, response.text
    assert sorted(t["label"] for t in response.json()["tags"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# Cost and preservation: the two properties that make this safe
# ---------------------------------------------------------------------------


def _count_statements(engine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    return statements


def test_relationship_free_schema_issues_no_reload(sync_client):
    """No relationship in the response schema -> no loader options -> no extra
    statement. The reload must not become a tax on plain writes."""

    class Plain(fr.IDBase):
        title: Mapped[str]

    class PlainRead(fr.IDSchema):
        title: str

    class PlainCreate(fr.BaseSchema):
        title: str

    @fr.include_view(sync_client.app)
    class PlainView(fr.RestView):
        prefix = "/plain"
        model = Plain
        schema = PlainRead
        schema_create = PlainCreate

    _create_sync_tables()
    engine = _fr_globals.make_session.kw["bind"]
    statements = _count_statements(engine)

    response = sync_client.post("/plain/", json={"title": "x"})

    assert response.status_code == 201, response.text
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    # exactly one: the post-flush refresh
    assert len(selects) == 1, selects


def _seed_doc_with_two_tags(sync_db):
    """A doc with tags ``keep`` and ``drop``, plus the model pair."""

    class Tag(fr.IDBase):
        label: Mapped[str]
        doc_id: Mapped[int | None] = mapped_column(
            ForeignKey("scoped_doc.id"), init=False, nullable=True
        )

    class ScopedDoc(fr.IDBase):
        title: Mapped[str]
        tags: Mapped[list[Tag]] = relationship(init=False)

    _create_sync_tables()
    _, make_session = sync_db
    with make_session() as session:
        doc = ScopedDoc(title="seeded")
        session.add(doc)
        session.flush()
        for label in ("keep", "drop"):
            tag = Tag(label=label)
            tag.doc_id = doc.id
            session.add(tag)
        session.commit()

    return ScopedDoc, Tag, make_session


def test_reload_preserves_an_already_loaded_relationship(sync_db):
    """The reload runs without ``populate_existing``, so a relationship whose
    in-memory value differs from a fresh read keeps that value.

    That is what makes applying the full options safe: they can name a
    relationship the caller has already populated (an eager loader in
    ``build_query``, a hand-set value) without overwriting it.
    """
    ScopedDoc, Tag, make_session = _seed_doc_with_two_tags(sync_db)

    with make_session() as session:
        doc = session.scalars(select(ScopedDoc)).one()
        keep = session.scalars(select(Tag).where(Tag.label == "keep")).one()
        # How an eager loader populates a relationship: loaded, not dirty.
        set_committed_value(doc, "tags", [keep])

        statement = _relationship_reload_statement(doc, [selectinload(ScopedDoc.tags)])
        session.scalars(statement).unique().all()

        assert [t.label for t in doc.tags] == ["keep"]


def test_indirection_over_a_named_relationship_rides_along(client):
    """A ``@property`` or ``association_proxy`` is invisible to loader options,
    but resolves anyway when it walks a relationship the schema *does* name."""
    Company, Owner, Doc = _define_models()

    Doc.proxy_owner_name = association_proxy("owner", "name")
    Doc.owner_name = property(lambda self: self.owner.name)

    class CompanyRef(fr.IDSchema):
        name: str

    class OwnerRead(fr.IDSchema):
        name: str
        company: CompanyRef

    class DocRead(fr.IDSchema):
        title: str
        owner: OwnerRead
        owner_name: fr.ReadOnly[str]
        proxy_owner_name: fr.ReadOnly[str]

    class DocCreate(fr.BaseSchema):
        title: str

    owner_id: dict[str, int] = {}

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocRead
        schema_create = DocCreate

        async def make_new_object(self, schema_obj) -> Any:
            obj: Any = await super().make_new_object(schema_obj)
            obj.owner_id = owner_id["id"]
            return obj

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            company = Company(name="Initech")
            session.add(company)
            await session.flush()
            owner = Owner(name="Alice")
            owner.company_id = company.id
            session.add(owner)
            await session.commit()
            owner_id["id"] = owner.id

    asyncio.run(seed())

    body = client.post("/docs/", json={"title": "created"}).json()

    assert body["owner_name"] == "Alice"
    assert body["proxy_owner_name"] == "Alice"


def test_indirection_over_an_unnamed_relationship_fails_alike_on_read_and_write(client):
    """The boundary this fix draws: loader options follow relationships the
    response schema *names*, so a property reaching past that set still raises.

    It raises identically on GET and POST. That symmetry is the point -- writes
    used to fail where reads succeeded; now they agree.
    """

    class Owner(fr.IDBase):
        name: Mapped[str]

    class Doc(fr.IDBase):
        title: Mapped[str]
        owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id"), init=False)
        owner: Mapped[Owner] = relationship(init=False)

        @property
        def owner_name(self) -> str:
            return self.owner.name

    class DocRead(fr.IDSchema):
        title: str
        owner_name: fr.ReadOnly[str]  # `owner` itself is never named

    class DocCreate(fr.BaseSchema):
        title: str

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocRead
        schema_create = DocCreate

        async def make_new_object(self, schema_obj) -> Any:
            obj: Any = await super().make_new_object(schema_obj)
            obj.owner_id = 1
            return obj

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            session.add(Owner(name="Alice"))
            await session.flush()
            doc = Doc(title="seeded")
            doc.owner_id = 1
            session.add(doc)
            await session.commit()

    asyncio.run(seed())

    with pytest.raises(MissingGreenlet):
        client.get("/docs/1")
    with pytest.raises(MissingGreenlet):
        client.post("/docs/", json={"title": "created"})


def test_awaitable_attrs_reaches_a_relationship_the_schema_never_names(client):
    """``AsyncAttrs`` covers what eager loading structurally cannot.

    Loader options follow relationships the response schema *names*. A hook or
    business method that reaches past that set runs in plain async context,
    where a bare attribute access raises ``MissingGreenlet``.
    """

    class Note(fr.IDBase):
        body: Mapped[str]
        doc_id: Mapped[int | None] = mapped_column(
            ForeignKey("noted_doc.id"), init=False, nullable=True
        )

    class NotedDoc(fr.IDBase):
        title: Mapped[str]
        notes: Mapped[list[Note]] = relationship(init=False)

    class DocRead(fr.IDSchema):
        title: str  # `notes` deliberately absent -- nothing eager-loads it

    class DocCreate(fr.BaseSchema):
        title: str

    seen: dict[str, Any] = {}

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = NotedDoc
        schema = DocRead
        schema_create = DocCreate

        async def after_commit(self, action, new: Any, old=None) -> None:
            with pytest.raises(MissingGreenlet):
                _ = new.notes
            seen["notes"] = [n.body for n in await new.awaitable_attrs.notes]

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            doc = NotedDoc(title="seeded")
            session.add(doc)
            await session.flush()
            note = Note(body="hello")
            note.doc_id = doc.id
            session.add(note)
            await session.commit()

    asyncio.run(seed())

    response = client.patch("/docs/1", json={"title": "renamed"})

    assert response.status_code == 200, response.text
    assert seen["notes"] == ["hello"]


def test_reload_fills_an_unloaded_relationship(sync_db):
    """Companion to the test above: the same statement is not a no-op."""
    ScopedDoc, _Tag, make_session = _seed_doc_with_two_tags(sync_db)

    with make_session() as session:
        doc = session.scalars(select(ScopedDoc)).one()
        assert "tags" in sa_inspect(doc).unloaded

        statement = _relationship_reload_statement(doc, [selectinload(ScopedDoc.tags)])
        session.scalars(statement).unique().all()

        assert "tags" not in sa_inspect(doc).unloaded
        assert sorted(t.label for t in doc.tags) == ["drop", "keep"]


# ---------------------------------------------------------------------------
# The gate must key its recursion on (instance, schema), not the instance
# alone -- the same object can be reached under two sub-schemas naming
# different relationships.
# ---------------------------------------------------------------------------


def test_divergent_schemas_same_instance_reload(client):
    """One instance reached under two nested schemas that name *different*
    relationships. The gate has to check both pairings; deduping the instance
    away after the first lets the second schema's unloaded relationship slip
    through, and the async serializer 500s on it -- the y2zx regression, this
    time inside the reload gate rather than the write path as a whole.
    """

    class Company(fr.IDBase):
        name: Mapped[str]

    class Department(fr.IDBase):
        name: Mapped[str]

    class Person(fr.IDBase):
        name: Mapped[str]
        company_id: Mapped[int] = mapped_column(ForeignKey("company.id"), init=False)
        # eager: survives the write's refresh, so the gate sees it loaded
        company: Mapped[Company] = relationship(init=False, lazy="joined")
        department_id: Mapped[int] = mapped_column(
            ForeignKey("department.id"), init=False
        )
        # default lazy: unloaded after refresh -- the one the gate must not skip
        department: Mapped[Department] = relationship(init=False)

    class Doc(fr.IDBase):
        title: Mapped[str]
        author_id: Mapped[int] = mapped_column(ForeignKey("person.id"), init=False)
        author: Mapped[Person] = relationship(
            init=False, lazy="joined", foreign_keys=[author_id]
        )
        reviewer_id: Mapped[int] = mapped_column(ForeignKey("person.id"), init=False)
        reviewer: Mapped[Person] = relationship(
            init=False, lazy="joined", foreign_keys=[reviewer_id]
        )

    class CompanyRef(fr.IDSchema):
        name: str

    class DepartmentRef(fr.IDSchema):
        name: str

    class AuthorRead(fr.IDSchema):
        name: str
        company: CompanyRef  # this branch names company

    class ReviewerRead(fr.IDSchema):
        name: str
        department: DepartmentRef  # this branch names department

    class DocRead(fr.IDSchema):
        title: str
        author: AuthorRead
        reviewer: ReviewerRead

    class DocCreate(fr.BaseSchema):
        title: str

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = Doc
        schema = DocRead
        schema_create = DocCreate

        async def make_new_object(self, schema_obj) -> Any:
            obj: Any = await super().make_new_object(schema_obj)
            # author and reviewer are the *same* person row
            obj.author_id = 1
            obj.reviewer_id = 1
            return obj

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            session.add(Company(name="Initech"))
            session.add(Department(name="Research"))
            await session.flush()
            person = Person(name="Alice")
            person.company_id = 1
            person.department_id = 1
            session.add(person)
            await session.commit()

    asyncio.run(seed())

    response = client.post("/docs/", json={"title": "created"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["author"]["company"]["name"] == "Initech"
    assert body["reviewer"]["department"]["name"] == "Research"


def test_sync_create_serializes_assigned_collection(sync_client):
    """Sync parity for the async collection reload. Sync never raises -- the
    serializer would just lazy-load the collection -- so response content alone
    cannot tell a reload from a lazy load. Assert the collection is already
    loaded when the response is built, which only the reload guarantees."""

    load_state: dict[str, Any] = {}

    class Tag(fr.IDBase):
        label: Mapped[str]
        doc_id: Mapped[int | None] = mapped_column(
            ForeignKey("sync_tagged_doc.id"), init=False, nullable=True
        )

    class SyncTaggedDoc(fr.IDBase):
        title: Mapped[str]
        tags: Mapped[list[Tag]] = relationship(init=False)

    class TagRef(fr.IDSchema):
        label: str

    class DocRead(fr.IDSchema):
        title: str
        tags: list[TagRef]

    class DocCreate(fr.BaseSchema):
        title: str

    @fr.include_view(sync_client.app)
    class DocView(fr.RestView):
        prefix = "/docs"
        model = SyncTaggedDoc
        schema = DocRead
        schema_create = DocCreate

        def make_new_object(self, schema_obj) -> Any:
            obj: Any = super().make_new_object(schema_obj)
            obj.tags = list(self.session.scalars(select(Tag)).all())
            return obj

        def to_response(self, obj_or_list, *args, **kwargs) -> Any:
            # snapshot load state after save_object, before serialization reads it
            if isinstance(obj_or_list, SyncTaggedDoc):
                load_state["unloaded"] = set(sa_inspect(obj_or_list).unloaded)
            return super().to_response(obj_or_list, *args, **kwargs)

    _create_sync_tables()
    with _fr_globals.make_session() as session:
        session.add_all([Tag(label="a"), Tag(label="b")])
        session.commit()

    response = sync_client.post("/docs/", json={"title": "created"})

    assert response.status_code == 201, response.text
    assert sorted(t["label"] for t in response.json()["tags"]) == ["a", "b"]
    # the reload eager-loaded the collection: it was already loaded when the
    # response was built, not lazy-loaded during serialization
    assert "tags" not in load_state["unloaded"]


def test_write_reload_unique_guards_a_joinedload_collection(client):
    """``save_object``'s ``.unique()`` defends against a loader-options override
    that returns a ``joinedload`` against a collection: the row fan-out would
    otherwise make ``.all()`` raise. The default ``selectinload`` never fans, so
    only an override exercises this -- exactly what the inline comment claims.
    """

    class Tag(fr.IDBase):
        label: Mapped[str]
        doc_id: Mapped[int | None] = mapped_column(
            ForeignKey("joined_doc.id"), init=False, nullable=True
        )

    class JoinedDoc(fr.IDBase):
        title: Mapped[str]
        tags: Mapped[list[Tag]] = relationship(init=False)

    class TagRef(fr.IDSchema):
        label: str

    class DocRead(fr.IDSchema):
        title: str
        tags: list[TagRef]

    class DocCreate(fr.BaseSchema):
        title: str

    @fr.include_view(client.app)
    class DocView(fr.AsyncRestView):
        prefix = "/docs"
        model = JoinedDoc
        schema = DocRead
        schema_create = DocCreate

        def get_relationship_loader_options(self) -> list[Any]:
            # a joinedload-to-many fans out rows; without .unique() the reload's
            # .all() would raise InvalidRequestError
            return [joinedload(JoinedDoc.tags)]

        async def make_new_object(self, schema_obj) -> Any:
            obj: Any = await super().make_new_object(schema_obj)
            obj.tags = list((await self.session.scalars(select(Tag))).all())
            return obj

    create_tables()

    async def seed():
        async with fr.open_async_session() as session:
            session.add_all([Tag(label="a"), Tag(label="b")])
            await session.commit()

    asyncio.run(seed())

    response = client.post("/docs/", json={"title": "created"})

    assert response.status_code == 201, response.text
    assert sorted(t["label"] for t in response.json()["tags"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# Gate / reload-statement unit branches
# ---------------------------------------------------------------------------


def test_transient_object_yields_no_reload_statement():
    """A transient (never-flushed) object has no identity, so there is nothing
    to reload by primary key: the statement builder returns None."""

    class Widget(fr.IDBase):
        name: Mapped[str]

    transient = Widget(name="unsaved")

    assert sa_inspect(transient).identity is None
    assert _relationship_reload_statement(transient, []) is None


def test_composite_pk_reload_statement_targets_all_key_columns(sync_db):
    """The PK-targeted reload zips every primary-key column against the object's
    identity, so a composite key produces one WHERE term per column."""

    class Ledger(fr.DataclassBase):
        tenant: Mapped[str] = mapped_column(primary_key=True)
        sku: Mapped[str] = mapped_column(primary_key=True)
        name: Mapped[str]

    _create_sync_tables()
    _, make_session = sync_db
    with make_session() as session:
        session.add(Ledger(tenant="acme", sku="x1", name="Widget"))
        session.commit()
        row = session.scalars(select(Ledger)).one()
        statement = _relationship_reload_statement(row, [])
        # inspect the WHERE clause specifically: the column names appear in the
        # SELECT list regardless, so only their presence in WHERE is meaningful
        where = str(
            statement.whereclause.compile(compile_kwargs={"literal_binds": True})
        )

    assert "tenant" in where and "sku" in where
    assert "acme" in where and "x1" in where


def test_gate_skips_a_nullable_relationship_loaded_as_none(sync_db):
    """A to-one relationship loaded as None needs no reload: it is present (just
    null), so the gate reports it satisfied rather than asking for a reload."""

    class Owner(fr.IDBase):
        name: Mapped[str]

    class Doc(fr.IDBase):
        title: Mapped[str]
        owner_id: Mapped[int | None] = mapped_column(
            ForeignKey("owner.id"), init=False, nullable=True
        )
        owner: Mapped[Owner | None] = relationship(init=False)

    class OwnerRef(fr.IDSchema):
        name: str

    class DocRead(fr.IDSchema):
        title: str
        owner: OwnerRef | None = None

    _create_sync_tables()
    _, make_session = sync_db
    with make_session() as session:
        doc = Doc(title="orphan")
        session.add(doc)
        session.flush()
        set_committed_value(doc, "owner", None)  # loaded, and it is None

        assert "owner" not in sa_inspect(doc).unloaded
        assert _schema_relationships_are_loaded(doc, Doc, DocRead) is True


def test_gate_terminates_on_a_self_referential_cycle(sync_db):
    """The (instance, schema) dedup still breaks a genuine cycle: an object
    reached again under the *same* schema is not re-descended, so a node that is
    its own parent does not recurse forever."""

    class Node(fr.IDBase):
        name: Mapped[str]
        parent_id: Mapped[int | None] = mapped_column(
            ForeignKey("node.id"), init=False, nullable=True
        )
        parent: Mapped["Node | None"] = relationship(init=False, remote_side="Node.id")

    class NodeRead(fr.IDSchema):
        name: str
        parent: "NodeRead | None" = None

    NodeRead.model_rebuild()

    _create_sync_tables()
    _, make_session = sync_db
    with make_session() as session:
        node = Node(name="root")
        session.add(node)
        session.flush()
        set_committed_value(node, "parent", node)  # its own parent -> a cycle

        # Must terminate rather than RecursionError; parent is loaded (itself).
        assert _schema_relationships_are_loaded(node, Node, NodeRead) is True
