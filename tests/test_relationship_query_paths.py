"""Dotted filters and sorts preserve the role of every relationship path."""

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from pydantic import Field
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import fastapi_restly as fr
from fastapi_restly.testing import RestlyTestClient


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"manager.name": "Alice", "sort": "-name"}, [15, 12, 10]),
        (
            {"id__in": "10,11,12,13,14,15", "sort": "-manager.name,name"},
            [13, 11, 10, 12, 15],
        ),
        (
            {
                "manager.manager.name": "Chief",
                "sort": "-manager.manager.name,manager.name,name",
            },
            [10, 12, 15, 11],
        ),
        (
            {"id__in": "10,11,12,13,14,15", "sort": "manager.manager.name,-name"},
            [15, 12, 11, 10, 13],
        ),
    ],
    ids=["filter-manager", "sort-manager", "filter-two-hops", "sort-two-hops"],
)
def test_self_relationship_paths(path_client, params, expected):
    payload = path_client.get("/employees/", params=params).json()

    assert [row["id"] for row in payload["data"]] == expected
    assert payload["total_count"] == len(expected)


def test_related_primary_key_sort_keeps_root_id_pagination_order(path_client):
    ids = []
    for page in range(1, 4):
        payload = path_client.get(
            "/employees/",
            params={
                "manager.id": "2",
                "sort": "manager.id",
                "page_size": "1",
                "page": str(page),
            },
        ).json()
        ids.extend(row["id"] for row in payload["data"])
        assert len(payload["data"]) == 1
        assert payload["total_count"] == 3
        assert payload["total_pages"] == 3

    assert ids == [10, 12, 15]


def test_two_relationships_to_same_model_filter_independently(path_client):
    payload = path_client.get(
        "/employees/",
        params={
            "homeCity.cityName": "Amsterdam",
            "workCity.cityName": "Berlin",
            "sort": "-name",
        },
    ).json()

    assert [row["id"] for row in payload["data"]] == [15, 10]
    assert payload["total_count"] == 2
    assert payload["data"][0]["homeCity"]["cityName"] == "Amsterdam"
    assert payload["data"][0]["workCity"]["cityName"] == "Berlin"


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("homeCity.cityName,-workCity.cityName,name", [12, 10, 15, 11, 13]),
        ("workCity.cityName,-homeCity.cityName,name", [11, 13, 10, 15, 12]),
    ],
)
def test_two_relationships_to_same_model_sort_independently(
    path_client, sort, expected
):
    payload = path_client.get("/employees/", params={"sort": sort}).json()

    assert [row["id"] for row in payload["data"]] == expected
    assert payload["total_count"] == len(expected)


def test_converging_paths_and_shared_prefixes_preserve_pagination(path_client):
    params = {
        "homeCity.country.countryCode": "NL",
        "workCity.country.countryCode": "DE",
        "homeCity.cityName__in": "Amsterdam,Delft",
        "sort": (
            "-homeCity.country.countryCode,homeCity.cityName,"
            "-workCity.country.countryCode,name"
        ),
        "page_size": "1",
    }
    ids = []
    for page in range(1, 4):
        payload = path_client.get(
            "/employees/", params={**params, "page": str(page)}
        ).json()
        ids.extend(row["id"] for row in payload["data"])
        assert len(payload["data"]) == 1
        assert payload["total_count"] == 3
        assert payload["total_pages"] == 3
        assert payload["page_size"] == 1
        assert payload["page"] == page

    assert ids == [10, 15, 13]


def test_missing_nullable_relationships_keep_inner_join_behavior(path_client):
    employee = path_client.get("/employees/14").json()
    assert employee["homeCity"] is None
    assert employee["manager"] is None

    payload = path_client.get(
        "/employees/", params={"id": "14", "sort": "homeCity.cityName"}
    ).json()
    assert payload["data"] == []
    assert payload["total_count"] == 0

    payload = path_client.get(
        "/employees/", params={"homeCity.cityName__isnull": "true"}
    ).json()
    assert payload["data"] == []
    assert payload["total_count"] == 0


def test_openapi_advertises_public_paths_and_unknown_paths_are_rejected(path_client):
    spec = path_client.get("/openapi.json").json()
    operation = spec["paths"]["/employees"]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert {
        "manager.name",
        "manager.manager.name",
        "homeCity.cityName",
        "workCity.cityName",
        "homeCity.country.countryCode",
        "workCity.country.countryCode",
    } <= names

    for unknown in (
        "home_city.cityName",
        "homeCity.name",
        "homeCity.country.code",
        "manager.manager.manager.name",
    ):
        assert unknown not in names
        response = path_client.get(
            "/employees/", params={unknown: "missing"}, assert_status_code=422
        )
        assert ["query", unknown] in [
            error["loc"] for error in response.json()["detail"]
        ]
        path_client.get("/employees/", params={"sort": unknown}, assert_status_code=400)


@pytest.fixture(params=[fr.RestView, fr.AsyncRestView], ids=["sync", "async"])
def path_client(request: pytest.FixtureRequest) -> Iterator[RestlyTestClient]:
    class Base(DeclarativeBase):
        pass

    class Country(Base):
        __tablename__ = "path_country"
        id: Mapped[int] = mapped_column(primary_key=True)
        code: Mapped[str]

    class City(Base):
        __tablename__ = "path_city"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]
        country_id: Mapped[int] = mapped_column(ForeignKey(Country.id))
        country: Mapped[Country] = relationship()

    class Employee(Base):
        __tablename__ = "path_employee"
        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]
        manager_id: Mapped[int | None] = mapped_column(ForeignKey("path_employee.id"))
        manager: Mapped["Employee | None"] = relationship(remote_side=[id])
        home_city_id: Mapped[int | None] = mapped_column(ForeignKey(City.id))
        home_city: Mapped[City | None] = relationship(foreign_keys=[home_city_id])
        work_city_id: Mapped[int | None] = mapped_column(ForeignKey(City.id))
        work_city: Mapped[City | None] = relationship(foreign_keys=[work_city_id])

    class CountrySchema(fr.IDSchema):
        code: str = Field(alias="countryCode")

    class CitySchema(fr.IDSchema):
        name: str = Field(alias="cityName")
        country: CountrySchema

    class EmployeeName(fr.IDSchema):
        name: str

    class ManagerSchema(EmployeeName):
        manager: EmployeeName | None = None

    class EmployeeSchema(EmployeeName):
        manager: ManagerSchema | None = None
        home_city: CitySchema | None = Field(default=None, alias="homeCity")
        work_city: CitySchema | None = Field(default=None, alias="workCity")

    app = FastAPI()

    @fr.include_view(app)
    class EmployeeView(request.param):
        prefix = "/employees"
        model = Employee
        schema = EmployeeSchema

    rows = [
        Country(id=1, code="NL"),
        Country(id=2, code="DE"),
        City(id=1, name="Amsterdam", country_id=1),
        City(id=2, name="Berlin", country_id=2),
        City(id=3, name="Delft", country_id=1),
        Employee(id=1, name="Chief"),
        Employee(id=2, name="Alice", manager_id=1),
        Employee(id=3, name="Bob", manager_id=1),
        Employee(id=4, name="Other chief"),
        Employee(id=5, name="Zoe", manager_id=4),
    ]
    rows.extend(
        Employee(
            id=id, name=name, manager_id=manager, home_city_id=home, work_city_id=work
        )
        for id, name, manager, home, work in (
            (10, "Ada", 2, 1, 2),
            (11, "Ben", 3, 2, 1),
            (12, "Cara", 2, 1, 3),
            (13, "Drew", 5, 3, 2),
            (14, "Eli", None, None, 2),
            (15, "Finn", 2, 1, 2),
        )
    )
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

    with RestlyTestClient(app) as client:
        yield client
