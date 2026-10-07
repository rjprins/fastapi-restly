"""A view's model is any mapped class, and only a mapped class."""

import asyncio

import pytest
from fastapi import FastAPI
from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import DeclarativeBase, registry, relationship

import fastapi_restly as fr
from fastapi_restly.testing._client import RestlyTestClient


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


def _legacy_owner_and_pet():
    Base = _legacy_base()

    class Owner(Base):
        __tablename__ = "legacy_ref_owner"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)

    class Pet(Base):
        __tablename__ = "legacy_ref_pet"
        id = Column(Integer, primary_key=True)
        name = Column(String, nullable=False)
        owner_id = Column(ForeignKey("legacy_ref_owner.id"))
        owner = relationship(Owner)

    return Base, Owner, Pet


@pytest.fixture(params=["async", "sync"])
def legacy_pets(request):
    """Owner and pet views on classes outside DeclarativeBase, per flavor."""
    Base, Owner, Pet = _legacy_owner_and_pet()

    class OwnerRead(fr.IDSchema):
        name: str

    class PetRead(fr.IDSchema):
        name: str
        owner: fr.IDRef[Owner] | None = None

    class PetNested(fr.IDSchema):
        name: str
        owner: OwnerRead | None = None

    if request.param == "async":
        client = request.getfixturevalue("client")
        base = fr.AsyncRestView

        async def create_tables():
            async with fr.db.get_async_engine().begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        def make_tables():
            asyncio.run(create_tables())

    else:
        engine, _ = request.getfixturevalue("sync_db")
        client = RestlyTestClient(FastAPI())
        base = fr.RestView

        def make_tables():
            Base.metadata.create_all(engine)

    for prefix, model, schema in [
        ("/owners", Owner, OwnerRead),
        ("/pets", Pet, PetRead),
        ("/nested-pets", Pet, PetNested),
    ]:
        view = type(
            f"{schema.__name__}View",
            (base,),
            {"prefix": prefix, "model": model, "schema": schema},
        )
        fr.include_view(client.app, view)
    make_tables()
    return client


def test_idref_reads_a_related_row_outside_declarative_base(legacy_pets):
    client = legacy_pets
    owner = client.post("/owners/", json={"name": "Ann"}).json()

    pet = client.post("/pets/", json={"name": "Rex", "owner": {"id": owner["id"]}})
    assert pet.json()["owner"] == owner["id"]

    fetched = client.get(f"/pets/{pet.json()['id']}")
    assert fetched.json()["owner"] == owner["id"]

    client.post("/pets/", json={"name": "Stray"})
    pets = client.get("/pets/").json()["data"]
    assert [p["owner"] for p in pets] == [owner["id"], None]


def test_idref_moves_and_clears_a_reference_outside_declarative_base(legacy_pets):
    client = legacy_pets
    first = client.post("/owners/", json={"name": "Ann"}).json()
    second = client.post("/owners/", json={"name": "Bob"}).json()
    pet = client.post(
        "/pets/", json={"name": "Rex", "owner": {"id": first["id"]}}
    ).json()

    moved = client.patch(f"/pets/{pet['id']}", json={"owner": {"id": second["id"]}})
    assert moved.json()["owner"] == second["id"]

    cleared = client.patch(f"/pets/{pet['id']}", json={"owner": None})
    assert cleared.json()["owner"] is None


def test_idref_to_a_missing_row_outside_declarative_base_is_404(legacy_pets):
    legacy_pets.post(
        "/pets/", json={"name": "Rex", "owner": {"id": 12345}}, assert_status_code=404
    )


def test_nested_schema_reads_a_related_row_outside_declarative_base(legacy_pets):
    client = legacy_pets
    owner = client.post("/owners/", json={"name": "Ann"}).json()
    pet = client.post(
        "/pets/", json={"name": "Rex", "owner": {"id": owner["id"]}}
    ).json()

    nested = client.get(f"/nested-pets/{pet['id']}").json()
    assert nested["owner"] == {"id": owner["id"], "name": "Ann"}


def test_openapi_resource_ref_for_a_class_outside_declarative_base(legacy_pets):
    schemas = legacy_pets.app.openapi()["components"]["schemas"]
    assert "owners" in str(schemas["PetRead"]["properties"]["owner"])


def test_foreign_key_checks_on_an_imperative_mapping(client):
    # An imperatively mapped class has no `registry` or `metadata` attribute.
    mapper_registry = registry()
    metadata = mapper_registry.metadata
    maker_table = Table(
        "imperative_maker",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
    )
    tool_table = Table(
        "imperative_tool",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
        Column("maker_id", ForeignKey("imperative_maker.id")),
    )

    class Maker:
        pass

    class Tool:
        pass

    mapper_registry.map_imperatively(Maker, maker_table)
    mapper_registry.map_imperatively(Tool, tool_table)
    assert not hasattr(Tool, "registry")

    class MakerRead(fr.IDSchema):
        name: str

    class ToolRead(fr.IDSchema):
        name: str
        maker_id: fr.MustExist[int] | None = None

    @fr.include_view(client.app)
    class MakerView(fr.AsyncRestView):
        prefix = "/makers"
        model = Maker
        schema = MakerRead

    @fr.include_view(client.app)
    class ToolView(fr.AsyncRestView):
        prefix = "/tools"
        model = Tool
        schema = ToolRead

    async def create_tables():
        async with fr.db.get_async_engine().begin() as conn:
            await conn.run_sync(metadata.create_all)

    asyncio.run(create_tables())

    maker = client.post("/makers/", json={"name": "Acme"}).json()
    tool = client.post("/tools/", json={"name": "Saw", "maker_id": maker["id"]})
    assert tool.json()["maker_id"] == maker["id"]
    client.post(
        "/tools/", json={"name": "Lost", "maker_id": 12345}, assert_status_code=404
    )

    tool_schema = client.app.openapi()["components"]["schemas"]["ToolRead"]
    assert "makers" in str(tool_schema["properties"]["maker_id"])
