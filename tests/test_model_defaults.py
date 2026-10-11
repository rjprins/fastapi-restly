"""Tests for ``fr.ModelDefaults`` on a declarative base of the user's own."""

import asyncio
import dataclasses
import enum
import inspect
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import DateTime, MetaData, Numeric
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    registry,
)

import fastapi_restly as fr


class Color(enum.Enum):
    RED = "red"


def test_each_base_has_its_own_registry_and_metadata():
    class BaseA(fr.ModelDefaults, DeclarativeBase):
        pass

    class BaseB(fr.ModelDefaults, DeclarativeBase):
        pass

    class User(fr.IDMixin, BaseA):
        name: Mapped[str]

    # The same class and table name on another base does not clash.
    class User(fr.IDMixin, BaseB):  # type: ignore[no-redef]  # noqa: F811
        email: Mapped[str]

    assert BaseA.registry is not BaseB.registry
    assert list(BaseA.metadata.tables) == ["user"]
    assert list(BaseB.metadata.tables) == ["user"]
    assert "user" not in fr.DataclassBase.metadata.tables


def test_models_get_restly_defaults():
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class BlogPost(fr.IDMixin, Base):
        version: Mapped[int] = mapped_column(default=1)
        title: Mapped[str]
        published_at: Mapped[datetime]
        color: Mapped[Color]

    table = BlogPost.__table__
    assert table.name == "blog_post"
    assert table.c.published_at.type.timezone is True
    assert table.c.color.type.native_enum is False
    assert table.c.color.type.length == 64

    # A keyword-only dataclass constructor: a required field may follow a
    # field with a default.
    assert dataclasses.is_dataclass(BlogPost)
    parameters = inspect.signature(BlogPost.__init__).parameters
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY
        for name, p in parameters.items()
        if name != "self"
    )
    post = BlogPost(title="Hello", published_at=datetime.now(), color=Color.RED)
    assert hasattr(post, "awaitable_attrs")


def test_own_type_annotation_map_entries_win():
    class Base(fr.ModelDefaults, DeclarativeBase):
        type_annotation_map = {Decimal: Numeric(12, 2), datetime: DateTime()}

    class Invoice(fr.IDMixin, Base):
        amount: Mapped[Decimal]
        issued_at: Mapped[datetime]
        color: Mapped[Color]

    table = Invoice.__table__
    assert table.c.amount.type.precision == 12
    assert table.c.issued_at.type.timezone is False
    # Restly's entry for the key the base leaves out still applies.
    assert table.c.color.type.native_enum is False


def test_entries_of_an_explicit_registry_win():
    class Base(fr.ModelDefaults, DeclarativeBase):
        registry = registry(type_annotation_map={datetime: DateTime()})

    class Event(fr.IDMixin, Base):
        happened_at: Mapped[datetime]

    assert Event.__table__.c.happened_at.type.timezone is False


def test_own_metadata_is_used():
    app_metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"}, schema="app")

    class Base(fr.ModelDefaults, DeclarativeBase):
        metadata = app_metadata

    class Item(fr.IDMixin, Base):
        name: Mapped[str]

    assert Item.__table__.metadata is app_metadata
    assert Item.__table__.schema == "app"
    assert Item.__table__.primary_key.name == "pk_item"


def test_order_of_the_bases_does_not_matter():
    class Base(DeclarativeBase, fr.ModelDefaults):
        pass

    class Reading(fr.IDMixin, Base):
        taken_at: Mapped[datetime]

    assert Reading.__table__.c.taken_at.type.timezone is True


@pytest.mark.parametrize("mixin", [fr.IDMixin, fr.TimestampsMixin])
def test_model_mixins_refuse_a_plain_base(mixin):
    class Base(DeclarativeBase):
        pass

    with pytest.raises(fr.exc.RestlyConfigurationError, match="fr.ModelDefaults"):

        class Widget(mixin, Base):
            __tablename__ = "widget"
            key: Mapped[int] = mapped_column(primary_key=True)


def test_model_mixins_accept_a_dataclass_base_without_restly_defaults():
    class Base(MappedAsDataclass, DeclarativeBase):
        pass

    class Widget(fr.TimestampsMixin, fr.IDMixin, Base):
        __tablename__ = "widget"
        name: Mapped[str]

    assert Widget(name="w").name == "w"


def test_views_serve_a_model_on_its_own_base(client):
    class Base(fr.ModelDefaults, DeclarativeBase):
        pass

    class Gadget(fr.TimestampsMixin, fr.IDMixin, Base):
        name: Mapped[str]

    @fr.include_view(client.app)
    class GadgetView(fr.AsyncRestView):
        prefix = "/gadgets"
        model = Gadget

    asyncio.run(fr.db.async_create_all(Base))

    created = client.post("/gadgets/", json={"name": "Lamp"}).json()
    assert created["name"] == "Lamp"
    assert "created_at" in created
    assert client.get(f"/gadgets/{created['id']}").json()["name"] == "Lamp"
