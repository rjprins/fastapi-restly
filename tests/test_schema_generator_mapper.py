"""The generator takes its fields from the mapper, not from ``Mapped`` annotations.

Annotations only refine a column's type. A column with neither a usable
annotation nor a Python column type raises instead of dropping out.
"""

import asyncio
import sys
from typing import get_args

import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, func
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
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
    class Person(fr.IDBase):
        name = Column(String, nullable=False)
        shout = column_property(func.upper(name, type_=String))

    schema = fr_schemas.create_schema_from_model(Person)
    assert schema.model_fields["shout"].annotation is str
    assert schema.model_fields["shout"].json_schema_extra["readOnly"] is True


def test_untypeable_column_raises_naming_the_attribute(client):
    class Opaque(UserDefinedType):
        cache_ok = True

        def get_col_spec(self, **kw):
            return "OPAQUE"

    class Blob(fr.IDBase):
        data = Column(Opaque())

    with pytest.raises(TypeError, match=r"Blob\.data .*schema="):

        @fr.include_view(client.app)
        class BlobView(fr.AsyncRestView):
            prefix = "/blobs"
            model = Blob


def test_annotation_nearest_the_model_wins():
    class Code(fr.IDBase):
        id: Mapped[str] = mapped_column(primary_key=True)
        name: Mapped[str]

    schema = fr_schemas.create_schema_from_model(Code)
    assert schema.model_fields["id"].annotation is str


def test_relationship_without_annotation_comes_from_the_mapper():
    class Owner(fr.IDBase):
        name: Mapped[str]

    class Pet(fr.IDBase):
        owner_id = Column(ForeignKey("owner.id"))
        owner = relationship(Owner)

    schema = fr_schemas.create_schema_from_model(Pet, include_relationships=True)
    assert "owner" in schema.model_fields


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

    schema = fr_schemas.create_schema_from_model(Pet, include_relationships=True)
    owner = next(
        arg
        for arg in get_args(schema.model_fields["owner"].annotation)
        if arg is not type(None)
    )
    assert set(owner.model_fields) == {"id", "name"}


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

    schema = fr_schemas.create_schema_from_model(Parent, include_relationships=True)
    children = schema.model_fields["children"].annotation
    assert Child.__name__ in repr(children)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Deferred annotations need 3.14")
def test_deferred_annotation_naming_an_undefined_class_on_a_dataclass_base(client):
    # Below SQLAlchemy 2.0.45, defining Writer raises NameError on 3.14.
    # Declared in a function so the registry cleanup treats it as test-local.
    writer_namespace = {
        "fr": fr,
        "Mapped": Mapped,
        "relationship": relationship,
        "__name__": __name__,
    }
    exec(
        "def declare():\n"
        "    class Writer(fr.IDBase):\n"
        "        name: Mapped[str]\n"
        "        novels: Mapped[list[Novel]] = relationship(\n"
        "            back_populates='writer', default_factory=list\n"
        "        )\n"
        "    return Writer\n",
        writer_namespace,
    )
    Writer = writer_namespace["declare"]()

    class Novel(fr.IDBase):
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

    schema = fr_schemas.create_schema_from_model(Writer, include_relationships=True)
    novels = schema.model_fields["novels"].annotation
    assert Novel.__name__ in repr(novels)
