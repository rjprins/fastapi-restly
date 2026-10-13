"""The generator takes its fields from the mapper, not from ``Mapped`` annotations.

Annotations only refine a column's type. A column with neither a usable
annotation nor a Python column type raises instead of dropping out.
"""

import asyncio
import sys

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, func
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    column_property,
    mapped_column,
    registry,
    relationship,
)
from sqlalchemy.types import UserDefinedType

import fastapi_restly as fr
import fastapi_restly.schemas as fr_schemas
from fastapi_restly.schemas._base import (
    _is_unresolved,
    _model_id_type,
    _own_annotations,
)

from ._models import SharedBase
from .conftest import create_tables


def test_unannotated_columns_get_fields(client):
    class Base(DeclarativeBase):
        pass

    # The shape of a SQLModel table: no Mapped annotations.
    class Legacy(Base):
        __tablename__ = "legacy"

        id = Column(Integer, primary_key=True)
        title = Column(String(50), nullable=False)
        note = Column(String)
        rank = Column(Integer, default=0)

    @fr.include_view(client.app)
    class LegacyView(fr.AsyncRestView):
        prefix = "/legacy"
        model = Legacy

    fields = LegacyView.schema.model_fields
    assert set(fields) == {"id", "title", "note", "rank"}
    assert fields["title"].annotation is str
    assert fields["title"].is_required() is True
    assert fields["note"].is_required() is False
    assert fields["rank"].is_required() is False
    assert set(LegacyView.schema_create.model_fields) == {"title", "note", "rank"}

    async def create_legacy_table():
        async with fr.db.get_async_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_legacy_table())
    created = client.post("/legacy/", json={"title": "Old"}).json()
    assert created == {"id": created["id"], "title": "Old", "note": None, "rank": 0}


def test_expression_column_property_is_read_only():
    class Person(fr.IDMixin, SharedBase):
        name = Column(String, nullable=False)
        shout = column_property(func.upper(name, type_=String))

    schema = fr_schemas.derive_schema(Person)
    assert schema.model_fields["shout"].annotation is str
    assert schema.model_fields["shout"].json_schema_extra["readOnly"] is True


def test_a_column_outside_the_constructor_is_read_only(client):
    """The model sets a column with ``init=False``, such as a tenant column.
    Before, the generated schema let the client write it: a create could
    choose another tenant, and the column default did not run."""

    class Ledger(fr.IDMixin, SharedBase):
        title: Mapped[str]
        tenant_id: Mapped[int] = mapped_column(init=False, insert_default=lambda: 1)
        status: Mapped[str] = mapped_column(init=False, insert_default="draft")

    @fr.include_view(client.app)
    class LedgerView(fr.AsyncRestView):
        prefix = "/ledgers"
        model = Ledger

    for name in ("tenant_id", "status"):
        field = LedgerView.schema.model_fields[name]
        assert field.json_schema_extra["readOnly"] is True, name
    assert set(LedgerView.schema_create.model_fields) == {"title"}
    assert set(LedgerView.schema_update.model_fields) == {"title"}

    create_tables()
    body = {"title": "a", "tenant_id": 2, "status": "paid"}
    created = client.post("/ledgers/", json=body).json()
    assert (created["tenant_id"], created["status"]) == (1, "draft")
    patched = client.patch(f"/ledgers/{created['id']}", json=body).json()
    assert (patched["tenant_id"], patched["status"]) == (1, "draft")


def test_a_foreign_key_outside_the_constructor_is_read_only(client):
    """The rule covers a foreign key with ``init=False``, also when it comes
    from a ``MappedAsDataclass`` mixin. A client that sends it gets no error:
    Restly ignores the value, as for every ReadOnly field."""

    class Org(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class OrgOwned(MappedAsDataclass, kw_only=True, eq=False):
        org_id: Mapped[int | None] = mapped_column(
            ForeignKey("org.id"), init=False, default=None
        )

    class Page(OrgOwned, fr.IDMixin, SharedBase):
        title: Mapped[str]

    @fr.include_view(client.app)
    class PageView(fr.AsyncRestView):
        prefix = "/pages"
        model = Page

    assert PageView.schema.model_fields["org_id"].json_schema_extra["readOnly"]
    assert set(PageView.schema_create.model_fields) == {"title"}

    create_tables()
    created = client.post("/pages/", json={"title": "a", "org_id": 1}).json()
    assert created["org_id"] is None
    patched = client.patch(f"/pages/{created['id']}", json={"org_id": 1}).json()
    assert patched["org_id"] is None


def test_untypeable_column_raises_naming_the_attribute(client):
    class Opaque(UserDefinedType):
        cache_ok = True

        def get_col_spec(self, **kw):
            return "OPAQUE"

    class Blob(fr.IDMixin, SharedBase):
        data = Column(Opaque())

    with pytest.raises(TypeError, match=r"Blob\.data .*schema="):

        @fr.include_view(client.app)
        class BlobView(fr.AsyncRestView):
            prefix = "/blobs"
            model = Blob


def test_annotation_nearest_the_model_wins():
    class Code(fr.IDMixin, SharedBase):
        id: Mapped[str] = mapped_column(primary_key=True)
        name: Mapped[str]

    schema = fr_schemas.derive_schema(Code)
    assert schema.model_fields["id"].annotation is str


def test_relationship_without_annotation_is_left_out():
    class Owner(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Pet(fr.IDMixin, SharedBase):
        owner_id = Column(ForeignKey("owner.id"))
        owner = relationship(Owner)

    schema = fr_schemas.derive_schema(Pet)
    assert set(schema.model_fields) == {"id", "owner_id"}


def test_relationship_to_a_class_outside_declarative_base():
    # SQLModel tables and legacy declarative_base() classes are mapped
    # without subclassing DeclarativeBase.
    Base = registry().generate_base()

    class Owner(Base):
        __tablename__ = "legacy_owner"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    class Pet(Base):
        __tablename__ = "legacy_pet"
        id = Column(Integer, primary_key=True)
        owner_id = Column(ForeignKey("legacy_owner.id"))
        owner = relationship(Owner)

    schema = fr_schemas.derive_schema(Pet)
    assert set(schema.model_fields) == {"id", "owner_id"}


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Deferred annotations need 3.14")
def test_deferred_annotation_naming_an_undefined_class(client):
    class Base(DeclarativeBase):
        pass

    # The usual cross-module layout: Child is imported only under
    # TYPE_CHECKING, so the name is undefined when annotations evaluate.
    parent_namespace = {
        "Base": Base,
        "Mapped": Mapped,
        "mapped_column": mapped_column,
        "relationship": relationship,
        "__name__": __name__,
    }
    exec(
        "class Parent(Base):\n"
        "    __tablename__ = 'deferred_parent'\n"
        "    id: Mapped[int] = mapped_column(primary_key=True)\n"
        "    name: Mapped[str]\n"
        "    children: Mapped[list[Child]] = relationship(back_populates='parent')\n",
        parent_namespace,
    )
    Parent = parent_namespace["Parent"]

    class Child(Base):
        __tablename__ = "deferred_child"
        id: Mapped[int] = mapped_column(primary_key=True)
        parent_id: Mapped[int] = mapped_column(ForeignKey("deferred_parent.id"))
        parent: Mapped[Parent] = relationship(back_populates="children")

    with pytest.raises(NameError):
        Parent.__annotations__

    assert _model_id_type(Parent) is int

    @fr.include_view(client.app)
    class ParentView(fr.AsyncRestView):
        prefix = "/parents"
        model = Parent

    assert set(ParentView.schema.model_fields) == {"id", "name"}


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Deferred annotations need 3.14")
def test_deferred_annotation_naming_an_undefined_class_on_a_dataclass_base(client):
    # Below SQLAlchemy 2.0.45, defining Writer raises NameError on 3.14.
    # Declared in a function so the registry cleanup treats it as test-local.
    writer_namespace = {
        "fr": fr,
        "Base": SharedBase,
        "Mapped": Mapped,
        "relationship": relationship,
        "__name__": __name__,
    }
    exec(
        "def declare():\n"
        "    class Writer(fr.IDMixin, Base):\n"
        "        name: Mapped[str]\n"
        "        novels: Mapped[list[Novel]] = relationship(\n"
        "            back_populates='writer', default_factory=list\n"
        "        )\n"
        "    return Writer\n",
        writer_namespace,
    )
    Writer = writer_namespace["declare"]()

    class Novel(fr.IDMixin, SharedBase):
        title: Mapped[str]
        writer_id: Mapped[int] = mapped_column(ForeignKey("writer.id"))
        writer: Mapped[Writer] = relationship(back_populates="novels", default=None)

    # SQLAlchemy leaves Novel behind as a forward reference.
    assert _is_unresolved(_own_annotations(Writer)["novels"])

    assert _model_id_type(Writer) is int

    @fr.include_view(client.app)
    class WriterView(fr.AsyncRestView):
        prefix = "/writers"
        model = Writer

    assert set(WriterView.schema.model_fields) == {"id", "name"}
