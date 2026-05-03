import enum
import types
from datetime import date, datetime, time
from typing import Any, Optional, Union, get_args, get_origin
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import Boolean, Date, DateTime, Float, Integer, String, Text, Time

import fastapi_restly as fr
from fastapi_restly.schemas import (
    BaseSchema,
    create_model_with_optional_fields,
    create_model_without_read_only_fields,
)
from fastapi_restly.schemas._base import (
    async_resolve_ids_to_sqlalchemy_objects,
    get_writable_inputs,
    getattrs,
    is_field_writeonly,
    is_readonly_field,
    rebase_with_model_config,
    resolve_ids_to_sqlalchemy_objects,
    set_schema_title,
)
from fastapi_restly.schemas._generator import (
    auto_generate_schema_for_view,
    convert_sqlalchemy_type_to_pydantic,
    create_schema_from_model,
    get_model_fields,
    get_relationship_target_model,
    get_sqlalchemy_field_type,
    is_relationship_field,
)


def test_idschema_coerces_primary_key_types_and_preserves_untyped_ids():
    class User(fr.IDBase):
        name: Mapped[str]

    assert fr.IDSchema[User](id="123").id == 123

    untyped = fr.IDSchema(id="123")
    assert untyped.id == "123"
    assert untyped.get_sql_model_annotation() is None


def test_idref_accepts_both_wire_forms_and_serializes_as_scalar():
    class IDRefInputUser(fr.IDBase):
        name: Mapped[str]

    scalar = fr.IDRef[IDRefInputUser](5)
    keyword = fr.IDRef[IDRefInputUser](id="6")
    payload = fr.IDRef[IDRefInputUser]({"id": "7"})

    assert scalar.id == 5
    assert keyword.id == 6
    assert payload.id == 7
    assert scalar.model_dump() == 5
    assert scalar.model_dump_json() == "5"


def test_idref_fields_generate_scalar_json_schema_in_both_modes():
    class IDRefSchemaUser(fr.IDBase):
        name: Mapped[str]

    class SchemaWithIDRef(BaseSchema):
        user_id: fr.IDRef[IDRefSchemaUser]

    validation_schema = SchemaWithIDRef.model_json_schema(mode="validation")
    serialization_schema = SchemaWithIDRef.model_json_schema(mode="serialization")

    for schema in (validation_schema, serialization_schema):
        ref = schema["properties"]["user_id"]["$ref"]
        ref_name = ref.removeprefix("#/$defs/")
        assert schema["$defs"][ref_name] == {"type": "integer"}


def test_idschema_reference_still_serializes_as_nested_dict():
    class IDSchemaNestedUser(fr.IDBase):
        name: Mapped[str]

    value = fr.IDSchema[IDSchemaNestedUser](id="5")

    assert value.id == 5
    assert value.model_dump() == {"id": 5}
    assert value.model_dump_json() == '{"id":5}'


def test_idref_fields_serialize_as_scalars_through_to_response_schema():
    class ToResponseUser(fr.IDBase):
        name: Mapped[str]

    class ToResponseTask(fr.IDBase):
        title: Mapped[str]
        owner_id: Mapped[int]

    class TaskSchema(fr.IDSchema):
        title: str
        owner_id: fr.IDRef[ToResponseUser]

    class TaskView(fr.AsyncRestView):
        model = ToResponseTask
        schema = TaskSchema

    task = ToResponseTask(title="Write tests", owner_id=5)
    task.id = 1

    payload = TaskView().to_response_schema(task).model_dump(mode="json")

    assert payload["owner_id"] == 5
    assert isinstance(payload["owner_id"], int)


def test_idref_fields_serialize_cleanly_through_fastapi_response_model(client):
    class ResponseModelUser(fr.IDBase):
        name: Mapped[str]

    class ResponseModelTask(fr.IDBase):
        title: Mapped[str]
        owner_id: Mapped[int]

    class TaskSchema(fr.IDSchema):
        title: str
        owner_id: fr.IDRef[ResponseModelUser]

    @fr.include_view(client.app)
    class TaskView(fr.AsyncRestView):
        prefix = "/idref-response"
        model = ResponseModelTask
        schema = TaskSchema

        @fr.post("/make", response_model=TaskSchema)
        async def make(self):
            task = ResponseModelTask(title="Write tests", owner_id=5)
            task.id = 1
            return self.to_response_schema(task)

    response = client.post("/idref-response/make")

    assert response.status_code == 201
    task = response.json()
    assert task["owner_id"] == 5
    assert isinstance(task["owner_id"], int)


def test_resolve_ids_to_sqlalchemy_objects_handles_missing_single_and_list_entries():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    class Author(fr.IDBase):
        name: Mapped[str]

    class SingleRefSchema(BaseSchema):
        author_id: fr.IDSchema[Author]

    class ListRefSchema(BaseSchema):
        authors: list[fr.IDSchema[Author]]

    fr.DataclassBase.metadata.create_all(engine)

    with Session(engine) as session:
        author = Author(name="Alice")
        session.add(author)
        session.commit()

        single_payload = SingleRefSchema(author_id={"id": author.id})
        resolve_ids_to_sqlalchemy_objects(session, single_payload)
        assert isinstance(single_payload.author_id, Author)
        assert single_payload.author_id.id == author.id

        list_payload = ListRefSchema(authors=[{"id": author.id}])
        resolve_ids_to_sqlalchemy_objects(session, list_payload)
        assert isinstance(list_payload.authors[0], Author)

        with pytest.raises(HTTPException, match="Id not found for author_id"):
            resolve_ids_to_sqlalchemy_objects(
                session,
                SingleRefSchema(author_id={"id": author.id + 999}),
            )

        with pytest.raises(HTTPException, match="Id not found for authors"):
            resolve_ids_to_sqlalchemy_objects(
                session,
                ListRefSchema(authors=[{"id": author.id}, {"id": author.id + 999}]),
            )

    engine.dispose()


@pytest.mark.asyncio
async def test_async_resolve_ids_to_sqlalchemy_objects_handles_missing_entries():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    class Team(fr.IDBase):
        name: Mapped[str]

    class TeamRefSchema(BaseSchema):
        team_id: fr.IDSchema[Team]
        teams: list[fr.IDSchema[Team]]

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with AsyncSession(bind=async_engine, expire_on_commit=False) as session:
            team = Team(name="Core")
            session.add(team)
            await session.commit()

            payload = TeamRefSchema(team_id={"id": team.id}, teams=[{"id": team.id}])
            await async_resolve_ids_to_sqlalchemy_objects(session, payload)
            assert isinstance(payload.team_id, Team)
            assert isinstance(payload.teams[0], Team)

            with pytest.raises(HTTPException, match="Id not found for team_id"):
                await async_resolve_ids_to_sqlalchemy_objects(
                    session,
                    TeamRefSchema(
                        team_id={"id": team.id + 1},
                        teams=[{"id": team.id}],
                    ),
                )

            with pytest.raises(HTTPException, match="Id not found for teams"):
                await async_resolve_ids_to_sqlalchemy_objects(
                    session,
                    TeamRefSchema(
                        team_id={"id": team.id},
                        teams=[{"id": team.id}, {"id": team.id + 1}],
                    ),
                )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_async_resolve_ids_to_sqlalchemy_objects_handles_idref_missing_entries():
    async_engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    class IDRefResolverTeam(fr.IDBase):
        name: Mapped[str]

    class TeamRefSchema(BaseSchema):
        team_id: fr.IDRef[IDRefResolverTeam]

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(fr.DataclassBase.metadata.create_all)

        async with AsyncSession(bind=async_engine, expire_on_commit=False) as session:
            team = IDRefResolverTeam(name="Core")
            session.add(team)
            await session.commit()

            payload = TeamRefSchema(team_id=team.id)
            await async_resolve_ids_to_sqlalchemy_objects(session, payload)
            assert isinstance(payload.team_id, IDRefResolverTeam)

            with pytest.raises(HTTPException, match="Id not found for team_id"):
                await async_resolve_ids_to_sqlalchemy_objects(
                    session,
                    TeamRefSchema(team_id=team.id + 1),
                )
    finally:
        await async_engine.dispose()


def test_schema_helper_utilities_cover_readonly_optional_and_config_rebasing():
    class DemoSchema(BaseSchema):
        model_config = {"populate_by_name": True}

        id: fr.ReadOnly[int]
        password: fr.WriteOnly[str]
        name: str | int
        email: str | None = None

    assert is_readonly_field(DemoSchema(id=1, password="pw", name="x"), "id") is True
    assert is_field_writeonly(DemoSchema(id=1, password="pw", name="x"), "password")
    assert (
        getattrs(
            type("Nested", (), {"child": type("Leaf", (), {"value": 7})()})(),
            "child",
            "value",
        )
        == 7
    )
    assert getattrs(object(), "missing", default="fallback") == "fallback"

    rebased = rebase_with_model_config((BaseSchema,), DemoSchema)
    assert rebased.model_config["populate_by_name"] is True

    set_schema_title(DemoSchema)
    assert DemoSchema.model_config["title"] == "DemoSchema"

    writable = get_writable_inputs(
        DemoSchema(id=2, password="pw", name="name"),
        DemoSchema,
    )
    assert "id" not in writable
    assert writable["password"] == "pw"

    update_schema = create_model_with_optional_fields(DemoSchema)
    assert update_schema().name is None
    assert type(None) in get_args(update_schema.model_fields["name"].annotation)
    assert type(None) in get_args(update_schema.model_fields["email"].annotation)

    create_schema = create_model_without_read_only_fields(DemoSchema)
    assert "id" not in create_schema.model_fields
    assert "password" in create_schema.model_fields


def test_schema_generator_helpers_cover_relationships_defaults_and_type_conversion():
    class Customer(fr.IDBase):
        name: Mapped[str]
        orders: Mapped[list["Order"]] = relationship(
            back_populates="customer",
            default_factory=list,
        )

    class Order(fr.IDBase):
        item_name: Mapped[str]
        quantity: Mapped[int | None]
        notes: Mapped[str] = mapped_column(default="none")
        customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
        customer: Mapped[Customer] = relationship(
            back_populates="orders",
            default=None,
        )

    class Node(fr.IDBase):
        name: Mapped[str]
        parent_id: Mapped[int | None] = mapped_column(
            ForeignKey("node.id"),
            nullable=True,
        )
        parent: Mapped["Node | None"] = relationship(
            remote_side="Node.id",
            back_populates="children",
            default=None,
        )
        children: Mapped[list["Node"]] = relationship(
            back_populates="parent",
            default_factory=list,
        )

    Node.__annotations__["parent"] = Mapped[Node | None]
    Node.__annotations__["children"] = Mapped[list[Node]]

    customer_field = Customer.orders.property
    assert is_relationship_field(customer_field) is True
    assert get_relationship_target_model(customer_field) is Order
    assert get_sqlalchemy_field_type(type("FieldWithType", (), {"type": String})()) is String
    assert get_sqlalchemy_field_type(list[int]) is list
    assert get_sqlalchemy_field_type(object()) is Any

    model_fields = get_model_fields(Order)
    assert model_fields["quantity"]["is_optional"] is True
    assert model_fields["notes"]["is_optional"] is True
    assert model_fields["customer"]["is_relationship"] is True
    assert model_fields["customer"]["target_model"] is Customer

    schema = create_schema_from_model(Order, include_relationships=True)
    assert schema.model_fields["id"].is_required() is True
    assert schema.model_fields["id"].json_schema_extra["readOnly"] is True
    assert "customer" in schema.model_fields
    assert type(None) in get_args(schema.model_fields["customer"].annotation)

    nested_customer = next(
        arg
        for arg in get_args(schema.model_fields["customer"].annotation)
        if arg is not type(None)
    )
    assert hasattr(nested_customer, "model_fields")
    assert "orders" not in nested_customer.model_fields

    node_schema = create_schema_from_model(Node, include_relationships=True)
    assert "parent" not in node_schema.model_fields
    assert "children" not in node_schema.model_fields

    class AutoOrderView:
        __name__ = "AutoOrderView"

    view_schema = auto_generate_schema_for_view(AutoOrderView, Order)
    assert "customer" not in view_schema.model_fields
    assert "customer_id" in view_schema.model_fields

    class NoIdModel(fr.PlainBase):
        __tablename__ = "no_id_model"

        slug: Mapped[str] = mapped_column(primary_key=True)

    no_id_schema = create_schema_from_model(NoIdModel)
    assert "id" not in no_id_schema.model_fields

    customer_schema = create_schema_from_model(Customer, include_relationships=True)
    orders_annotation = customer_schema.model_fields["orders"].annotation
    if get_origin(orders_annotation) in (Union, types.UnionType):
        list_annotation = next(
            arg for arg in get_args(orders_annotation) if arg is not type(None)
        )
    else:
        list_annotation = orders_annotation

    assert get_origin(list_annotation) is list
    nested_order_schema = get_args(list_annotation)[0]
    assert hasattr(nested_order_schema, "model_fields")
    assert "customer" not in nested_order_schema.model_fields

    class Priority(enum.Enum):
        HIGH = "high"

    assert convert_sqlalchemy_type_to_pydantic(String) is str
    assert convert_sqlalchemy_type_to_pydantic(Text) is str
    assert convert_sqlalchemy_type_to_pydantic(Integer) is int
    assert convert_sqlalchemy_type_to_pydantic(Float) is float
    assert convert_sqlalchemy_type_to_pydantic(Boolean) is bool
    assert convert_sqlalchemy_type_to_pydantic(DateTime) is datetime
    assert convert_sqlalchemy_type_to_pydantic(Date) is date
    assert convert_sqlalchemy_type_to_pydantic(Time) is time
    assert convert_sqlalchemy_type_to_pydantic(Any) is Any
    assert convert_sqlalchemy_type_to_pydantic(Priority) is Priority
    assert convert_sqlalchemy_type_to_pydantic(Customer) is Customer
    assert convert_sqlalchemy_type_to_pydantic(dict[str, Any], is_optional=True) == Optional[dict[str, Any]]

    class UnsupportedType:
        __name__ = "UnsupportedType"

    with pytest.raises(TypeError, match="Unsupported field type"):
        convert_sqlalchemy_type_to_pydantic(UnsupportedType)

    custom_named_schema = auto_generate_schema_for_view(
        AutoOrderView,
        Order,
        schema_name="CustomNamedSchema",
    )
    assert custom_named_schema.__name__ == "CustomNamedSchema"


def test_schema_generator_fallback_paths_for_relationship_detection_and_annotation_skips():
    class Child(fr.IDBase):
        name: Mapped[str]

    class Parent(fr.IDBase):
        child_id: Mapped[int | None] = mapped_column(ForeignKey("child.id"), nullable=True)
        child: Mapped[Child | None] = relationship(default=None)
        legacy_optional_name: Mapped[Optional[str]] = mapped_column(nullable=True)

    Parent.__annotations__["_private"] = Mapped[str]
    Parent.__annotations__["plain_value"] = str
    Parent.__annotations__["empty_mapped"] = Mapped

    assert is_relationship_field(object()) is False
    assert get_relationship_target_model(object()) is None

    class RelationshipWrapper:
        property = Parent.child.property

    assert get_relationship_target_model(RelationshipWrapper()) is Child

    with patch("fastapi_restly.schemas._generator.is_relationship_field", return_value=True):
        class ListFallbackField:
            type = list[Child]

        class SingleFallbackField:
            type = Child

        class UnknownFallbackField:
            type = list[Any]

        assert get_relationship_target_model(ListFallbackField()) is Child
        assert get_relationship_target_model(SingleFallbackField()) is Child
        assert get_relationship_target_model(UnknownFallbackField()) is Any

    model_fields = get_model_fields(Parent)
    assert "_private" not in model_fields
    assert "plain_value" not in model_fields
    assert "empty_mapped" not in model_fields
    assert model_fields["legacy_optional_name"]["is_optional"] is True
    assert model_fields["legacy_optional_name"]["type"] is str
