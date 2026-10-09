"""A WriteOnly field, or one with ``exclude=True``, is never a filter or sort key.

Responses leave a WriteOnly field out, but list views used to generate the
standard filters for it: ``?pin=1234`` returned the matching row, and
``pin__gte`` with ``sort=pin`` let a client narrow the value down.
"""

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import pydantic
import pytest
from fastapi import FastAPI
from pydantic import Field
from sqlalchemy import ForeignKey, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.exc import BadQueryParam
from fastapi_restly.query import apply_list_params, derive_schema_list_params
from fastapi_restly.testing import RestlyTestClient


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "writeonly_owner"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    pin: Mapped[str]


class Account(Base):
    __tablename__ = "writeonly_account"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    pin: Mapped[str]
    recovery_code: Mapped[str]
    owner_id: Mapped[int] = mapped_column(ForeignKey(Owner.id))
    owner: Mapped[Owner] = relationship()


class OwnerSchema(fr.IDSchema):
    name: str
    pin: fr.WriteOnly[str]


class AccountSchema(fr.IDSchema):
    name: str
    pin: fr.WriteOnly[str]
    recovery_code: fr.WriteOnly[str] = Field(alias="recoveryCode")
    owner: OwnerSchema


# The base names of every query key that would reach a WriteOnly column.
_SECRET_KEYS = {"pin", "recoveryCode", "recovery_code", "owner.pin"}


def _secret_params(names: Any) -> set[str]:
    return {name for name in names if name.split("__")[0] in _SECRET_KEYS}


def test_list_params_schema_has_no_writeonly_params():
    fields = derive_schema_list_params(AccountSchema, Account).model_fields

    assert {"name", "name__gte", "owner.name", "owner.name__contains"} <= set(fields)
    assert _secret_params(fields) == set()


def test_writeonly_field_may_use_a_reserved_name():
    # It never becomes a query key, so it cannot collide with ``page``.
    class TicketBase(DeclarativeBase):
        pass

    class Ticket(TicketBase):
        __tablename__ = "writeonly_ticket"
        id: Mapped[int] = mapped_column(primary_key=True)
        page: Mapped[str]

    class TicketSchema(fr.IDSchema):
        page: fr.WriteOnly[str]

    fields = derive_schema_list_params(TicketSchema, Ticket).model_fields

    assert "page" in fields
    assert fields["page"].annotation is int
    assert not [name for name in fields if name.startswith("page__")]


@pytest.mark.parametrize(
    "query",
    [
        "pin=1234",
        "pin__in=1234,9876",
        "pin__ne=1234",
        "pin__isnull=false",
        "pin__gte=5",
        "pin__lt=5",
        "pin__contains=23",
        "pin__icontains=23",
        "recoveryCode=r-aaa",
        "recovery_code=r-aaa",
        "owner.pin=1111",
        "owner.pin__gte=5",
        "sort=pin",
        "sort=-pin",
        "sort=recoveryCode",
        "sort=owner.pin",
        "sort=name,-owner.pin",
    ],
)
def test_apply_list_params_rejects_writeonly_keys(query):
    # Raw query params skip the generated schema, so the resolver itself
    # must refuse the field.
    with pytest.raises(BadQueryParam):
        apply_list_params(select(Account), QueryParams(query), Account, AccountSchema)


class Token(Base):
    __tablename__ = "writeonly_token"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    token: Mapped[str]


class TokenSchema(fr.IDSchema):
    name: str
    token: str = Field("", exclude=True)


@pytest.mark.parametrize("query", ["token=a", "token__contains=a", "sort=-token"])
def test_a_field_with_exclude_is_not_a_filter_or_sort_key(query):
    fields = derive_schema_list_params(TokenSchema, Token).model_fields

    assert "name" in fields
    assert not [name for name in fields if name.startswith("token")]
    with pytest.raises(BadQueryParam):
        apply_list_params(select(Token), QueryParams(query), Token, TokenSchema)


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def make_client(
    request: pytest.FixtureRequest,
) -> Iterator[Callable[..., RestlyTestClient]]:
    rows = [
        Owner(id=1, name="Olga", pin="1111"),
        Owner(id=2, name="Piet", pin="9999"),
        Account(id=1, name="alice", pin="1234", recovery_code="r-aaa", owner_id=1),
        Account(id=2, name="bob", pin="9876", recovery_code="r-zzz", owner_id=2),
    ]
    if request.param is fr.RestView:
        engine, make_session = request.getfixturevalue("sync_db")
        Base.metadata.create_all(engine)
        with make_session() as session:
            session.add_all(rows)
            session.commit()
    else:

        async def prepare():
            engine = fr.db.get_async_engine()
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine)() as session:
                session.add_all(rows)
                await session.commit()

        asyncio.run(prepare())

    def make(**view_attrs: Any) -> RestlyTestClient:
        app = FastAPI()
        view = type(
            "AccountView",
            (request.param,),
            {
                "prefix": "/accounts",
                "model": Account,
                "schema": AccountSchema,
                **view_attrs,
            },
        )
        fr.include_view(app, view)
        return RestlyTestClient(app)

    yield make


@pytest.fixture
def accounts(make_client: Callable[..., RestlyTestClient]) -> RestlyTestClient:
    return make_client()


@pytest.mark.parametrize(
    "key",
    [
        "pin",
        "pin__in",
        "pin__ne",
        "pin__isnull",
        "pin__gte",
        "pin__lt",
        "pin__contains",
        "pin__icontains",
        "recoveryCode",
        "recovery_code",
        "owner.pin",
        "owner.pin__gte",
        # A field that does not exist gets the same answer.
        "nope",
    ],
)
def test_writeonly_filter_is_an_unknown_query_param(accounts, key):
    value = "false" if key.endswith("__isnull") else "1234"
    detail = accounts.get(
        "/accounts/", params={key: value}, assert_status_code=422
    ).json()["detail"]

    assert detail == [
        {
            "type": "extra_forbidden",
            "loc": ["query", key],
            "msg": f"Unknown query parameter {key!r}",
            "input": value,
        }
    ]


@pytest.mark.parametrize(
    ("sort", "path"),
    [
        ("pin", "pin"),
        ("-pin", "pin"),
        ("recoveryCode", "recoveryCode"),
        ("owner.pin", "owner.pin"),
        ("-owner.pin", "owner.pin"),
        ("name,pin", "pin"),
        # A field that does not exist gets the same answer.
        ("nope", "nope"),
        ("owner.nope", "owner.nope"),
    ],
)
def test_writeonly_sort_answers_like_an_unknown_field(accounts, sort, path):
    response = accounts.get("/accounts/", params={"sort": sort}, assert_status_code=400)

    assert response.json() == {"detail": f"Invalid attribute in URL query: {path}"}


def test_readable_fields_still_filter_and_sort(accounts):
    payload = accounts.get(
        "/accounts/", params={"owner.name__in": "Olga,Piet", "sort": "-owner.name"}
    ).json()

    assert [row["name"] for row in payload["data"]] == ["bob", "alice"]
    assert "pin" not in payload["data"][0]
    assert "pin" not in payload["data"][0]["owner"]


def test_openapi_lists_no_writeonly_params(accounts):
    operation = accounts.get("/openapi.json").json()["paths"]["/accounts"]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}

    assert {"name", "owner.name"} <= names
    assert _secret_params(names) == set()


@pytest.mark.parametrize("query", [{"pin": "1234"}, {"sort": "-pin"}])
def test_custom_schema_list_params_cannot_reach_writeonly_column(make_client, query):
    # Hand-written list params pass the unknown-key guard, so the
    # resolver must still refuse the field.
    class LeakyParams(pydantic.BaseModel):
        pin: str | None = None
        sort: str | None = None

    client = make_client(schema_list_params=LeakyParams)

    response = client.get("/accounts/", params=query, assert_status_code=400)
    assert response.json() == {"detail": "Invalid attribute in URL query: pin"}
