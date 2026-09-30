"""A view's model is any mapped class, and only a mapped class."""

import asyncio

import pytest
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase, registry

import fastapi_restly as fr


def _legacy_base():
    # SQLModel tables and legacy declarative_base() classes are mapped
    # without subclassing DeclarativeBase.
    return registry().generate_base()


def test_view_accepts_a_mapped_class_outside_declarative_base(client):
    Base = _legacy_base()

    class Gadget(Base):
        __tablename__ = "legacy_gadget"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    assert not issubclass(Gadget, DeclarativeBase)

    class GadgetRead(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class GadgetView(fr.AsyncRestView):
        prefix = "/legacy-gadgets"
        model = Gadget
        schema = GadgetRead

    async def create_tables():
        async with fr.db.get_async_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())

    created = client.post("/legacy-gadgets/", json={"name": "Widget"})
    assert created.status_code == 201
    fetched = client.get(f"/legacy-gadgets/{created.json()['id']}")
    assert fetched.json()["name"] == "Widget"


def test_resolve_scope_accepts_a_mapped_class_outside_declarative_base():
    Base = _legacy_base()

    class Gizmo(Base):
        __tablename__ = "legacy_gizmo"
        id = Column(Integer, primary_key=True)

    assert fr.resolve_scope(Gizmo) is fr.clauses.UNSCOPED


def test_view_rejects_an_unmapped_model_at_class_definition():
    class NotMapped:
        pass

    with pytest.raises(fr.exc.RestlyConfigurationError, match="NotMapped.*no mapper"):

        class BadView(fr.AsyncRestView):
            model = NotMapped


def test_sync_view_rejects_an_unmapped_model_at_class_definition():
    class NotMapped:
        pass

    with pytest.raises(fr.exc.RestlyConfigurationError, match="NotMapped.*no mapper"):

        class BadView(fr.RestView):
            model = NotMapped


def test_view_rejects_the_declarative_base_itself():
    class Base(DeclarativeBase):
        pass

    with pytest.raises(fr.exc.RestlyConfigurationError, match="no mapper"):

        class BadView(fr.AsyncRestView):
            model = Base


def test_view_rejects_a_model_that_is_not_a_class():
    with pytest.raises(fr.exc.RestlyConfigurationError, match="no mapper"):

        class BadView(fr.AsyncRestView):
            model = "Gadget"


def test_resolve_scope_rejects_an_unmapped_class():
    class NotMapped:
        pass

    with pytest.raises(TypeError, match="mapped model class"):
        fr.resolve_scope(NotMapped)
