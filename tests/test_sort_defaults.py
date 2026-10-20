"""The sort when the client sends none (``default_sort``), and where a sort
puts NULLs (``sort_nulls``).

By default NULLs come last in both directions, on every database. The
databases differ: PostgreSQL puts NULLs last in an ascending sort, SQLite and
MySQL first.
"""

from typing import Any

import pydantic
import pytest
import sqlalchemy
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.dialects import mssql, mysql, oracle, postgresql, sqlite
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.datastructures import QueryParams

import fastapi_restly as fr
from fastapi_restly.exc import RestlyConfigurationError
from fastapi_restly.query import apply_list_params, derive_schema_list_params
from fastapi_restly.testing._client import RestlyTestClient

from ._models import SharedBase
from .conftest import create_tables


@pytest.fixture(params=["async", "sync"])
def views(request):
    """A client, the view base class and a function that creates the
    tables, for each kind of view."""
    if request.param == "async":
        return request.getfixturevalue("client"), fr.AsyncRestView, create_tables
    engine, _ = request.getfixturevalue("sync_db")
    client = RestlyTestClient(FastAPI())
    return client, fr.RestView, lambda: SharedBase.metadata.create_all(engine)


def spools(views, **attrs: Any) -> RestlyTestClient:
    """A client for ``/spools``: five spools, two of them never used."""
    client, view_base, make_tables = views

    class Spool(fr.IDMixin, SharedBase):
        name: Mapped[str]
        rank: Mapped[int]
        used: Mapped[int | None] = mapped_column(default=None)

    class SpoolSchema(fr.IDSchema):
        name: str
        rank: int
        used: int | None = None

    fr.include_view(
        client.app,
        type(
            "SpoolView",
            (view_base,),
            {"prefix": "/spools", "model": Spool, "schema": SpoolSchema, **attrs},
        ),
    )
    make_tables()
    for name, rank, used in [
        ("a", 3, 20),
        ("b", 1, None),
        ("c", 2, 10),
        ("d", 2, None),
        ("e", 1, 30),
    ]:
        client.post("/spools/", json={"name": name, "rank": rank, "used": used})
    return client


def names(client: RestlyTestClient, **params: str) -> list[str]:
    payload = client.get("/spools/", params=params).json()
    return [spool["name"] for spool in payload["data"]]


# ---------------------------------------------------------------------------
# default_sort
# ---------------------------------------------------------------------------


def test_the_default_sort_applies_when_the_client_sends_none(views):
    client = spools(views, default_sort="-rank,name")

    assert names(client) == ["a", "c", "d", "b", "e"]
    assert names(client, sort="") == ["a", "c", "d", "b", "e"]


def test_the_sort_of_the_client_replaces_the_default_sort(views):
    client = spools(views, default_sort="-rank")

    assert names(client, sort="name") == ["a", "b", "c", "d", "e"]


def test_openapi_shows_the_default_sort(views):
    client = spools(views, default_sort="-rank")

    operation = client.get("/openapi.json").json()["paths"]["/spools"]["get"]
    (sort,) = [p for p in operation["parameters"] if p["name"] == "sort"]
    assert sort["schema"]["default"] == "-rank"


def test_the_default_sort_can_use_a_key_that_the_client_cannot_send(views):
    client = spools(views, default_sort="-rank,name", exclude_sort_keys=("rank",))

    assert names(client) == ["a", "c", "d", "b", "e"]
    client.get("/spools/", params={"sort": "rank"}, assert_status_code=422)


def test_an_override_sees_the_default_sort_in_the_list_params(views):
    seen = []
    _, view_base, _ = views
    is_async = view_base is fr.AsyncRestView

    if is_async:

        async def apply_list_params(self, stmt, list_params):
            seen.append(list_params.sort)
            return await view_base.apply_list_params(self, stmt, list_params)

    else:

        def apply_list_params(self, stmt, list_params):
            seen.append(list_params.sort)
            return view_base.apply_list_params(self, stmt, list_params)

    client = spools(views, default_sort="-rank", apply_list_params=apply_list_params)
    names(client)
    names(client, sort="name")

    assert seen == ["-rank", "name"]


def test_a_default_sort_key_that_the_schema_does_not_have_fails(views):
    with pytest.raises(RestlyConfigurationError) as exc_info:
        spools(views, default_sort="-created_at")

    assert str(exc_info.value) == (
        "SpoolView.default_sort: 'created_at' is not a field of SpoolSchema "
        "to sort on. A key is a field as the client writes it in the sort "
        "parameter. The fields to sort on are: id, name, rank, used."
    )


def test_an_inherited_default_sort_without_its_keys_does_not_apply(views):
    _, view_base, _ = views

    class AppView(view_base):
        default_sort = "-created_at"

    client = spools((views[0], AppView, views[2]))

    assert names(client) == ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "must be sort keys separated by commas"),
        ("name,", "must be sort keys separated by commas"),
        ("-", "must be sort keys separated by commas"),
        (("name",), r"must be a string such as '-created_at,name', or None"),
    ],
)
def test_a_bad_default_sort_fails_when_the_class_is_defined(value, message):
    with pytest.raises(RestlyConfigurationError, match=message):

        class ItemView(fr.AsyncRestView):
            default_sort = value


def test_hand_written_list_params_take_no_default_sort():
    class SpoolParams(pydantic.BaseModel):
        sort: str = "-rank"

    class Spool(fr.IDMixin, SharedBase):
        rank: Mapped[int]

    class SpoolSchema(fr.IDSchema):
        rank: int

    class SpoolView(fr.AsyncRestView):
        prefix = "/spools"
        model = Spool
        schema = SpoolSchema
        schema_list_params = SpoolParams
        default_sort = "-rank"

    with pytest.raises(RestlyConfigurationError, match="is written by hand"):
        fr.include_view(FastAPI(), SpoolView)


@pytest.mark.parametrize("base", [fr.AsyncReactAdminView, fr.ReactAdminView])
def test_react_admin_views_reject_the_default_sort(base):
    class Spool(fr.IDMixin, SharedBase):
        rank: Mapped[int]

    class SpoolSchema(fr.IDSchema):
        rank: int

    attrs = {"prefix": "/spools", "model": Spool, "schema": SpoolSchema}
    view = type("SpoolAdmin", (base,), {**attrs, "default_sort": "-rank"})

    with pytest.raises(RestlyConfigurationError) as exc_info:
        fr.include_view(FastAPI(), view)

    assert str(exc_info.value) == (
        "SpoolAdmin sets default_sort = '-rank', which a react-admin view "
        "does not use. React-admin sends its filters and sort order in the "
        "'filter' and 'sort' parameters, as JSON. Remove the setting."
    )


def test_derive_schema_list_params_takes_the_default_sort():
    class Spool(fr.IDMixin, SharedBase):
        rank: Mapped[int]

    class SpoolSchema(fr.IDSchema):
        rank: int

    params = derive_schema_list_params(SpoolSchema, Spool, default_sort="-rank")
    stmt = apply_list_params(
        sqlalchemy.select(Spool), params(), Spool, SpoolSchema, sort_nulls=None
    )

    assert str(stmt).split("ORDER BY")[1].split("\n")[0].strip() == (
        f"{Spool.__tablename__}.rank DESC, {Spool.__tablename__}.id"
    )


# ---------------------------------------------------------------------------
# sort_nulls
# ---------------------------------------------------------------------------


def test_nulls_come_last_in_both_directions_by_default(views):
    client = spools(views)

    assert names(client, sort="used") == ["c", "a", "e", "b", "d"]
    assert names(client, sort="-used") == ["e", "a", "c", "b", "d"]


def test_nulls_come_first_in_both_directions(views):
    client = spools(views, sort_nulls="first")

    assert names(client, sort="used") == ["b", "d", "c", "a", "e"]
    assert names(client, sort="-used") == ["b", "d", "e", "a", "c"]


def test_none_leaves_the_nulls_where_the_database_puts_them(views):
    client = spools(views, sort_nulls=None)

    # SQLite: NULL is the smallest value
    assert names(client, sort="used") == ["b", "d", "c", "a", "e"]
    assert names(client, sort="-used") == ["e", "a", "c", "b", "d"]


def test_the_default_sort_puts_nulls_last_too(views):
    client = spools(views, default_sort="-used")

    assert names(client) == ["e", "a", "c", "b", "d"]


def test_a_bad_sort_nulls_fails_when_the_class_is_defined():
    with pytest.raises(RestlyConfigurationError, match="must be 'first', 'last'"):

        class ItemView(fr.AsyncRestView):
            sort_nulls = "end"  # type: ignore[assignment]


class _Base(DeclarativeBase):
    pass


class _Sorted(_Base):
    __tablename__ = "sorted_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    used: Mapped[int | None]
    rank: Mapped[int]


class _SortedSchema(fr.IDSchema):
    used: int | None
    rank: int


def order_by(dialect: Any, sort: str, sort_nulls: Any = "last") -> str:
    stmt = apply_list_params(
        sqlalchemy.select(_Sorted),
        QueryParams({"sort": sort}),
        _Sorted,
        _SortedSchema,
        pagination=None,
        sort_nulls=sort_nulls,
    )
    sql = str(stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    return sql.split("ORDER BY ")[1].split("\n")[0].strip()


@pytest.mark.parametrize(
    "dialect", [postgresql.dialect(), oracle.dialect()], ids=["postgresql", "oracle"]
)
def test_postgresql_and_oracle_get_nulls_last(dialect):
    assert (
        order_by(dialect, "-used") == "sorted_rows.used DESC NULLS LAST, sorted_rows.id"
    )
    assert order_by(dialect, "used", "first") == (
        "sorted_rows.used NULLS FIRST, sorted_rows.id"
    )


@pytest.mark.parametrize(
    "dialect", [mysql.dialect(), mssql.dialect()], ids=["mysql", "mssql"]
)
def test_mysql_and_sql_server_get_a_case_before_the_column(dialect):
    case = "CASE WHEN (sorted_rows.used IS NULL) THEN {} ELSE {} END"

    assert order_by(dialect, "-used") == (
        f"{case.format(1, 0)}, sorted_rows.used DESC, sorted_rows.id"
    )
    assert order_by(dialect, "used", "first") == (
        f"{case.format(0, 1)}, sorted_rows.used, sorted_rows.id"
    )


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ((3, 30, 0), "sorted_rows.used DESC NULLS LAST, sorted_rows.id"),
        (
            (3, 29, 0),
            "CASE WHEN (sorted_rows.used IS NULL) THEN 1 ELSE 0 END, "
            "sorted_rows.used DESC, sorted_rows.id",
        ),
    ],
)
def test_sqlite_gets_nulls_last_from_version_3_30(version, expected):
    dialect = sqlite.dialect()
    dialect.server_version_info = version

    assert order_by(dialect, "-used") == expected


def test_a_column_that_cannot_be_null_gets_a_plain_term():
    assert order_by(postgresql.dialect(), "-rank") == (
        "sorted_rows.rank DESC, sorted_rows.id"
    )


def test_none_gives_the_plain_term():
    assert order_by(postgresql.dialect(), "-used", None) == (
        "sorted_rows.used DESC, sorted_rows.id"
    )


@pytest.mark.parametrize("sort_nulls", ["last", "first"])
def test_the_statement_cache_tells_the_rules_apart(sort_nulls):
    other = "first" if sort_nulls == "last" else "last"

    def stmt(nulls: Any, sort: str = "-used") -> Any:
        return apply_list_params(
            sqlalchemy.select(_Sorted),
            QueryParams({"sort": sort}),
            _Sorted,
            _SortedSchema,
            pagination=None,
            sort_nulls=nulls,
        )

    key = stmt(sort_nulls)._generate_cache_key()
    assert key == stmt(sort_nulls)._generate_cache_key()
    assert key != stmt(other)._generate_cache_key()
    assert key != stmt(sort_nulls, "used")._generate_cache_key()


@pytest.mark.parametrize("base", [fr.AsyncReactAdminView, fr.ReactAdminView])
@pytest.mark.parametrize(
    ("sort_nulls", "expected"),
    [("last", ["e", "a", "c", "b", "d"]), ("first", ["b", "d", "e", "a", "c"])],
)
def test_a_react_admin_view_puts_nulls_where_the_view_says(
    request, base, sort_nulls, expected
):
    if base is fr.AsyncReactAdminView:
        client = request.getfixturevalue("client")
        make_tables = create_tables
    else:
        engine, _ = request.getfixturevalue("sync_db")
        client = RestlyTestClient(FastAPI())

        def make_tables():
            SharedBase.metadata.create_all(engine)

    class Spool(fr.IDMixin, SharedBase):
        name: Mapped[str]
        used: Mapped[int | None] = mapped_column(default=None)

    class SpoolSchema(fr.IDSchema):
        name: str
        used: int | None = None

    fr.include_view(
        client.app,
        type(
            "SpoolAdmin",
            (base,),
            {
                "prefix": "/spools",
                "model": Spool,
                "schema": SpoolSchema,
                "sort_nulls": sort_nulls,
            },
        ),
    )
    make_tables()
    for name, used in [("a", 20), ("b", None), ("c", 10), ("d", None), ("e", 30)]:
        client.post("/spools/", json={"name": name, "used": used})

    payload = client.get("/spools/", params={"sort": '["used","DESC"]'}).json()
    assert [spool["name"] for spool in payload] == expected


# ---------------------------------------------------------------------------
# A sort through a relationship
# ---------------------------------------------------------------------------


def test_a_sort_through_a_relationship_keeps_the_rows_without_one(views):
    client, view_base, make_tables = views

    class Vendor(fr.IDMixin, SharedBase):
        name: Mapped[str]

    class Filament(fr.IDMixin, SharedBase):
        name: Mapped[str]
        vendor_id: Mapped[int | None] = mapped_column(
            ForeignKey(Vendor.id), default=None
        )
        vendor: Mapped[Vendor | None] = relationship(default=None)

    class VendorSchema(fr.IDSchema):
        name: str

    class FilamentSchema(fr.IDSchema):
        name: str
        vendor_id: int | None = None
        vendor: fr.ReadOnly[VendorSchema | None] = None

    for prefix, model, schema, attrs in [
        ("/vendors", Vendor, VendorSchema, {}),
        ("/filaments", Filament, FilamentSchema, {"include_sort_keys": ("**",)}),
    ]:
        fr.include_view(
            client.app,
            type(
                "View",
                (view_base,),
                {"prefix": prefix, "model": model, "schema": schema, **attrs},
            ),
        )
    make_tables()
    acme = client.post("/vendors/", json={"name": "Acme"}).json()["id"]
    zeta = client.post("/vendors/", json={"name": "Zeta"}).json()["id"]
    for name, vendor in [("one", zeta), ("two", None), ("three", acme)]:
        client.post("/filaments/", json={"name": name, "vendor_id": vendor})

    for sort, expected in [
        ("vendor.name", ["three", "one", "two"]),
        ("-vendor.name", ["one", "three", "two"]),
    ]:
        payload = client.get("/filaments/", params={"sort": sort}).json()
        assert [filament["name"] for filament in payload["data"]] == expected
        assert payload["total_count"] == 3
