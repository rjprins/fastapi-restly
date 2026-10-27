"""The dialect-divergent surface, pinned on real PostgreSQL.

Everything here is invisible on SQLite and only runs when the subtree is pointed
at PostgreSQL (see ``conftest.py``). Seven things are covered:

* ``contains`` (``LIKE``) is case-sensitive and ``icontains`` (``ILIKE``) folds
  case, for ASCII *and* non-ASCII input. On SQLite ``LIKE`` is
  ASCII-case-insensitive and ``lower()`` is ASCII-only, so the two operators are
  indistinguishable and non-ASCII folding never happens.
* ``NULL`` ordering. By default a sort puts NULLs last in both directions,
  with ``NULLS LAST``. With ``sort_nulls = None`` the placement is the dialect
  default -- ``NULLS LAST`` ascending / ``NULLS FIRST`` descending on
  PostgreSQL, the reverse of SQLite.
* Pagination stays stable across pages when many rows share a sort key, because
  the id tiebreak makes row order deterministic (PostgreSQL gives no stable
  order otherwise, unlike SQLite's rowid).
* The count of a paginated list of a model with a ``json`` column.
  PostgreSQL cannot compare ``json`` values (it can compare ``jsonb``
  values), so ``DISTINCT`` over such a column fails. The count selects only
  the primary key.
* A native ENUM column. PostgreSQL rejects a filter value that is no label
  with an error, and it has no ``LIKE`` for an enum, so both would be a 500.
  Restly checks the value against the labels, and gives such a column no
  ``__contains``.
* The psycopg cross-session connection sharing documented for
  ``restly_session`` / ``restly_async_session`` -- the async session runs over
  the sync fixture's pinned connection, so a write through either is visible to
  the other within a test. This path only exists on a psycopg stack.
* Times: a time without an offset is UTC. psycopg leaves the time zone to
  PostgreSQL, which reads such a time in the time zone of the session, so
  these tests give the session another time zone.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    and_,
    create_engine,
    select,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

import fastapi_restly as fr
from fastapi_restly._integrity import _build_integrity_detail

from .._models import SharedBase
from .conftest import PG_URL

# This module is only imported when the subtree is collected, which conftest
# gates on a PostgreSQL URL, so PG_URL is always set here. The read tests reuse
# this one module-level async engine across the sync TestClient's per-request
# event loops; that is safe on psycopg 3 (its async I/O binds to the running
# loop per wait) but would need a per-test engine on a loop-bound driver.
_sync_engine = create_engine(PG_URL)
_async_engine = create_async_engine(PG_URL)
_make_session = sessionmaker(bind=_sync_engine, expire_on_commit=False)
_async_make_session = async_sessionmaker(bind=_async_engine, expire_on_commit=False)

# Configure both legs on psycopg: the query tests drive the async app, and the
# cross-session sharing test needs a sync and an async factory on one DBAPI.
fr.configure(make_session=_make_session, async_make_session=_async_make_session)

_app = FastAPI()


class PgWord(fr.IDMixin, SharedBase):
    name: Mapped[str]


class PgWordSchema(fr.IDSchema):
    name: str


class PgRanked(fr.IDMixin, SharedBase):
    label: Mapped[str]
    score: Mapped[int | None] = mapped_column(default=None)


class PgRankedSchema(fr.IDSchema):
    label: str
    score: int | None = None


class PgPaged(fr.IDMixin, SharedBase):
    label: Mapped[str]
    bucket: Mapped[int]


class PgPagedSchema(fr.IDSchema):
    label: str
    bucket: int


class PgDoc(fr.IDMixin, SharedBase):
    title: Mapped[str]
    # sqlalchemy.JSON is ``json`` on PostgreSQL, not ``jsonb``
    data: Mapped[dict] = mapped_column(JSON, default_factory=dict)


class PgDocSchema(fr.IDSchema):
    title: str
    data: dict = {}


class PgLabelled(fr.IDMixin, SharedBase):
    # a native ENUM type on PostgreSQL
    status: Mapped[str] = mapped_column(
        Enum("draft", "published", name="pg_labelled_status")
    )


class PgLabelledSchema(fr.IDSchema):
    status: str


class PgShared(fr.IDMixin, SharedBase):
    name: Mapped[str]


class PgSharedSchema(fr.IDSchema):
    name: str


# Write-path models. Each carries a database constraint whose violation only
# raises a psycopg error (with a real SQLSTATE + diag) on PostgreSQL; SQLite
# either enforces it through a different, string-only error path or -- for
# foreign keys -- does not enforce it at all by default.
class PgUnique(fr.IDMixin, SharedBase):
    code: Mapped[str] = mapped_column(unique=True)


class PgUniqueSchema(fr.IDSchema):
    code: str


class PgParent(fr.IDMixin, SharedBase):
    name: Mapped[str]  # NOT NULL, reused for the not-null violation


class PgChild(fr.IDMixin, SharedBase):
    # A plain scalar FK (no fr.MustExist), so existence is not pre-validated in
    # the app and the insert reaches the database constraint.
    parent_id: Mapped[int] = mapped_column(ForeignKey(PgParent.id))


class PgChildSchema(fr.IDSchema):
    parent_id: int


class PgChecked(fr.IDMixin, SharedBase):
    amount: Mapped[int]
    __table_args__ = (CheckConstraint("amount >= 0", name="pg_checked_amount_nonneg"),)


class PgCheckedSchema(fr.IDSchema):
    amount: int


class PgMeeting(fr.IDMixin, SharedBase):
    name: Mapped[str]
    # a column without a time zone
    starts_at: Mapped[datetime] = mapped_column(DateTime())
    # a column with a time zone, from fr.ModelDefaults
    ends_at: Mapped[datetime]
    # a time zone only on PostgreSQL, from a variant
    held_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(postgresql.TIMESTAMP(timezone=True), "postgresql"),
        default=None,
    )


class PgMeetingSchema(fr.IDSchema):
    name: str
    starts_at: datetime
    ends_at: datetime
    held_at: datetime | None = None


class PgSpan(fr.IDMixin, SharedBase):
    name: Mapped[str]
    # a native INTERVAL on PostgreSQL, with a generated schema
    duration: Mapped[timedelta]


@fr.include_view(_app)
class PgWordView(fr.AsyncRestView):
    prefix = "/words"
    model = PgWord
    schema = PgWordSchema


@fr.include_view(_app)
class PgRankedView(fr.AsyncRestView):
    prefix = "/ranked"
    model = PgRanked
    schema = PgRankedSchema


@fr.include_view(_app)
class PgRankedByDatabaseView(fr.AsyncRestView):
    prefix = "/ranked-by-database"
    model = PgRanked
    schema = PgRankedSchema
    sort_nulls = None


@fr.include_view(_app)
class PgRankedFirstView(fr.RestView):
    prefix = "/sync/ranked-first"
    model = PgRanked
    schema = PgRankedSchema
    sort_nulls = "first"


@fr.include_view(_app)
class PgPagedView(fr.AsyncRestView):
    prefix = "/paged"
    model = PgPaged
    schema = PgPagedSchema


@fr.include_view(_app)
class PgDocView(fr.AsyncRestView):
    prefix = "/docs"
    model = PgDoc
    schema = PgDocSchema


@fr.include_view(_app)
class PgDocAdminView(fr.views.AsyncReactAdminView):
    prefix = "/admin/docs"
    model = PgDoc
    schema = PgDocSchema


@fr.include_view(_app)
class PgDocSyncView(fr.RestView):
    prefix = "/sync/docs"
    model = PgDoc
    schema = PgDocSchema


@fr.include_view(_app)
class PgDocSyncAdminView(fr.views.ReactAdminView):
    prefix = "/sync/admin/docs"
    model = PgDoc
    schema = PgDocSchema


@fr.include_view(_app)
class PgLabelledView(fr.AsyncRestView):
    prefix = "/labelled"
    model = PgLabelled
    schema = PgLabelledSchema


@fr.include_view(_app)
class PgLabelledAdminView(fr.views.AsyncReactAdminView):
    prefix = "/admin/labelled"
    model = PgLabelled
    schema = PgLabelledSchema


@fr.include_view(_app)
class PgUniqueView(fr.AsyncRestView):
    prefix = "/unique"
    model = PgUnique
    schema = PgUniqueSchema


@fr.include_view(_app)
class PgChildView(fr.AsyncRestView):
    prefix = "/children"
    model = PgChild
    schema = PgChildSchema


@fr.include_view(_app)
class PgCheckedView(fr.AsyncRestView):
    prefix = "/checked"
    model = PgChecked
    schema = PgCheckedSchema


@fr.include_view(_app)
class PgMeetingView(fr.AsyncRestView):
    prefix = "/meetings"
    model = PgMeeting
    schema = PgMeetingSchema


@fr.include_view(_app)
class PgMeetingAdminView(fr.views.AsyncReactAdminView):
    prefix = "/admin/meetings"
    model = PgMeeting
    schema = PgMeetingSchema


@fr.include_view(_app)
class PgSpanView(fr.AsyncRestView):
    prefix = "/spans"
    model = PgSpan


_MODELS = [
    PgWord,
    PgRanked,
    PgPaged,
    PgDoc,
    PgLabelled,
    PgShared,
    PgUnique,
    PgParent,
    PgChild,
    PgChecked,
    PgMeeting,
    PgSpan,
]


def _seed() -> None:
    with _make_session() as session:
        # LIKE/ILIKE fixtures: capitalised ASCII and non-ASCII so a lowercase
        # search only matches through case folding, never a plain substring.
        session.add_all([PgWord(name=n) for n in ("Alice", "Oxford", "Öland")])
        # NULL-ordering fixture: non-null scores out of order, with holes.
        session.add_all(
            [
                PgRanked(label="a", score=10),
                PgRanked(label="b", score=None),
                PgRanked(label="c", score=5),
                PgRanked(label="d", score=None),
                PgRanked(label="e", score=20),
            ]
        )
        # Pagination fixture: five rows sharing one sort key.
        session.add_all([PgPaged(label=f"p{i}", bucket=7) for i in range(5)])
        # json fixture: three rows, more than one page of two.
        session.add_all([PgDoc(title=f"d{n}", data={"n": n}) for n in range(3)])
        session.add_all([PgLabelled(status=s) for s in ("draft", "published")])
        session.commit()


@pytest.fixture(scope="session", autouse=True)
def _pg_schema():
    """Build this module's tables once, seed them, and drop them afterwards.

    Only this module's tables are touched (``tables=``), so a shared registry or
    a leftover from an interrupted run cannot bleed in, and ``drop_all`` first
    clears any table left behind on the persistent server.
    """
    tables = [model.__table__ for model in _MODELS]
    metadata = SharedBase.metadata
    metadata.drop_all(_sync_engine, tables=tables)
    metadata.create_all(_sync_engine, tables=tables)
    _seed()
    try:
        yield
    finally:
        metadata.drop_all(_sync_engine, tables=tables)
        _sync_engine.dispose()
        asyncio.run(_async_engine.dispose())


@pytest.fixture
def restly_app() -> FastAPI:
    """The app ``restly_client`` wraps."""
    return _app


def _names(response) -> list[str]:
    return sorted(row["name"] for row in response.json()["data"])


def _labels(response) -> list[str]:
    return [row["label"] for row in response.json()["data"]]


def test_contains_is_case_sensitive_for_ascii(restly_client):
    # LIKE is case-sensitive on PostgreSQL, so a lowercase needle misses the
    # capitalised row -- the exact case where SQLite's LIKE would still match.
    assert _names(restly_client.get("/words/?name__contains=ali")) == []
    assert _names(restly_client.get("/words/?name__contains=Ali")) == ["Alice"]


def test_icontains_folds_ascii_case(restly_client):
    assert _names(restly_client.get("/words/?name__icontains=ali")) == ["Alice"]


def test_contains_is_case_sensitive_for_non_ascii(restly_client):
    # "Öland" contains "Öl", not "öl": LIKE will not fold the umlaut.
    response = restly_client.get("/words/", params={"name__contains": "öl"})
    assert _names(response) == []


def test_icontains_folds_non_ascii_case(restly_client):
    # ILIKE folds Ö <-> ö through the database collation -- impossible on SQLite,
    # whose ASCII-only lower() leaves the umlaut untouched. The fold is
    # LC_CTYPE-dependent; it holds on the postgres:17 image's UTF-8 default (and
    # would fail loudly, not silently, on a C-locale database).
    response = restly_client.get("/words/", params={"name__icontains": "öl"})
    assert _names(response) == ["Öland"]


def test_a_value_outside_the_labels_of_a_native_enum_is_a_400(restly_client):
    response = restly_client.get("/labelled/", params={"status": "draft"})
    assert [row["status"] for row in response.json()["data"]] == ["draft"]
    response = restly_client.get(
        "/labelled/", params={"status": "bogus"}, assert_status_code=400
    )
    assert response.json()["detail"] == (
        "Invalid filter value for 'status': 'bogus'. Use one of: draft, published."
    )
    restly_client.get("/labelled/?status__contains=dr", assert_status_code=422)
    response = restly_client.get(
        "/admin/labelled/",
        params={"filter": '{"status": "bogus"}'},
        assert_status_code=400,
    )
    assert response.json()["detail"] == (
        "Invalid filter value for 'status': 'bogus'. Use one of: draft, published."
    )


def test_nulls_come_last_in_both_directions_by_default(restly_client):
    # Non-nulls ascend (5, 10, 20) or descend, then the NULL rows follow,
    # ordered by the id tiebreak.
    assert _labels(restly_client.get("/ranked/?sort=score")) == [
        "c",
        "a",
        "e",
        "b",
        "d",
    ]
    assert _labels(restly_client.get("/ranked/?sort=-score")) == [
        "e",
        "a",
        "c",
        "b",
        "d",
    ]


def test_nulls_come_first_in_both_directions(restly_client):
    assert _labels(restly_client.get("/sync/ranked-first/?sort=score")) == [
        "b",
        "d",
        "c",
        "a",
        "e",
    ]
    assert _labels(restly_client.get("/sync/ranked-first/?sort=-score")) == [
        "b",
        "d",
        "e",
        "a",
        "c",
    ]


def test_null_ordering_of_the_database(restly_client):
    # PostgreSQL default: ascending sorts NULLs last, descending first.
    assert _labels(restly_client.get("/ranked-by-database/?sort=score")) == [
        "c",
        "a",
        "e",
        "b",
        "d",
    ]
    assert _labels(restly_client.get("/ranked-by-database/?sort=-score")) == [
        "b",
        "d",
        "e",
        "a",
        "c",
    ]


def test_pagination_is_stable_under_duplicate_sort_keys(restly_client):
    # Every row shares bucket=7, so without the id tiebreak PostgreSQL could
    # skip or repeat rows across pages. Paging must return each row exactly once.
    seen: list[int] = []
    for page in (1, 2, 3):
        payload = restly_client.get(
            f"/paged/?sort=bucket&page_size=2&page={page}"
        ).json()
        seen.extend(row["id"] for row in payload["data"])
    assert len(seen) == 5
    assert len(set(seen)) == 5
    # The id tiebreak makes the page order deterministic (ascending id).
    assert seen == sorted(seen)


def test_the_doc_column_is_json_not_jsonb():
    with _sync_engine.connect() as conn:
        data_type = conn.scalar(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'pg_doc' AND column_name = 'data'"
            )
        )
    assert data_type == "json"


@pytest.mark.parametrize("prefix", ["/docs", "/sync/docs"])
def test_paginated_list_of_a_json_model(restly_client, prefix):
    # The count ran DISTINCT over every column, and PostgreSQL has no equality
    # operator for json, so this list failed with a ProgrammingError.
    payload = restly_client.get(f"{prefix}/", params={"page_size": 2}).json()
    assert payload["total_count"] == 3
    assert [doc["data"] for doc in payload["data"]] == [{"n": 0}, {"n": 1}]


@pytest.mark.parametrize("prefix", ["/admin/docs", "/sync/admin/docs"])
def test_react_admin_list_of_a_json_model(restly_client, prefix):
    response = restly_client.get(f"{prefix}/", params={"range": "[0,1]"})
    assert response.status_code == 200, response.text
    assert response.headers["content-range"] == "items 0-1/3"


@pytest.mark.asyncio
async def test_psycopg_cross_session_sharing(restly_session, restly_async_session):
    """The documented psycopg sharing: async session runs over the sync fixture's
    pinned connection, so writes cross between them within one test and roll back
    at teardown. This path does not exist on SQLite.
    """
    # Write through the async session; commit releases its savepoint onto the
    # shared connection.
    restly_async_session.add(PgShared(name="from_async"))
    await restly_async_session.commit()

    # The sync fixture session, on the same pinned connection, sees it.
    from_sync_view = restly_session.execute(select(PgShared.name)).scalars().all()
    assert "from_async" in from_sync_view

    # And the reverse: a sync write is visible to the async session.
    restly_session.add(PgShared(name="from_sync"))
    restly_session.commit()
    result = await restly_async_session.execute(select(PgShared.name))
    assert set(result.scalars().all()) == {"from_async", "from_sync"}


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------
#
# The read tests above run over a committed seed, but the most dialect-divergent
# behaviour is on writes: a real constraint violation on PostgreSQL raises a
# psycopg error whose SQLSTATE and ``diag`` drive the 409 detail. Until now the
# only "postgres" in the suite fabricated that error shape
# (``SimpleNamespace(pgcode=..., diag=...)``) and never touched a server, so
# nothing proved psycopg actually produces it. These tests drive the app's
# create endpoint against real constraints and assert the end-to-end 409, under
# the savepoint fixtures so every write rolls back. They use an async HTTP
# client because RestlyTestClient is sync and cannot share the async fixture's
# pinned connection.


@asynccontextmanager
async def _async_client():
    transport = httpx.ASGITransport(app=_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# The assertions below check the exact *classified* detail, not a substring of
# the raw driver text. The raw psycopg message also contains "unique" etc., so a
# substring check would pass even when the classifier degrades to the generic
# fallback -- the very psycopg3 `sqlstate`-vs-`pgcode` bug this leg surfaced.


@pytest.mark.asyncio
async def test_unique_violation_returns_409(restly_async_session):
    async with _async_client() as client:
        first = await client.post("/unique/", json={"code": "dup"})
        assert first.status_code == 201
        conflict = await client.post("/unique/", json={"code": "dup"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Unique constraint violated: pg_unique_code_key"


@pytest.mark.asyncio
async def test_session_recovers_after_integrity_conflict(restly_async_session):
    """A 409 leaves the connection usable for the next request.

    On PostgreSQL an IntegrityError aborts the transaction; without a rollback the
    next statement raises InFailedSqlTransaction and the follow-up request would
    500. The framework rolls the per-request session back at request end (here, to
    its savepoint), so a distinct valid POST after the conflict still succeeds.
    This recovery is invisible on SQLite, which has no failed-transaction state.
    """
    async with _async_client() as client:
        assert (await client.post("/unique/", json={"code": "rec"})).status_code == 201
        conflict = await client.post("/unique/", json={"code": "rec"})
        assert conflict.status_code == 409
        # The real assertion: the connection recovered from the aborted insert, so
        # a distinct valid row still goes in on the same request flow.
        recovered = await client.post("/unique/", json={"code": "rec-2"})
        assert recovered.status_code == 201
        listed = await client.get("/unique/")
    assert listed.status_code == 200
    # Only the two successful writes survive; the conflicting insert left nothing.
    assert sorted(row["code"] for row in listed.json()["data"]) == ["rec", "rec-2"]


@pytest.mark.asyncio
async def test_foreign_key_violation_returns_409(restly_async_session):
    # SQLite does not enforce foreign keys by default, so this path only fires on
    # PostgreSQL. No parent row exists, so the insert violates the FK.
    async with _async_client() as client:
        response = await client.post("/children/", json={"parent_id": 999999})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail == "Foreign key constraint violated: pg_child_parent_id_fkey"


@pytest.mark.asyncio
async def test_check_constraint_violation_returns_409(restly_async_session):
    async with _async_client() as client:
        response = await client.post("/checked/", json={"amount": -1})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail == "Check constraint violated: pg_checked_amount_nonneg"


@pytest.mark.asyncio
async def test_not_null_violation_detail_from_real_error(restly_async_session):
    # PgParent has no registered view on purpose: a NOT NULL column becomes a
    # required request field, so any app path would 422 before the database sees
    # the NULL. Drive the violation through the session instead, to confirm the
    # real psycopg error carries the SQLSTATE and column the classifier
    # (unit-tested with a fabricated error) expects.
    restly_async_session.add(PgParent(name=None))  # type: ignore[arg-type]
    with pytest.raises(IntegrityError) as excinfo:
        await restly_async_session.flush()
    assert excinfo.value.orig.sqlstate == "23502"  # type: ignore[union-attr]
    detail = _build_integrity_detail(excinfo.value)
    assert detail == "Not-null constraint violated on column 'name'"


# ---------------------------------------------------------------------------
# A time without an offset is UTC. psycopg leaves the time zone to
# PostgreSQL, which reads such a time in the time zone of the session.
# ---------------------------------------------------------------------------


async def _set_session_time_zone(session, name: str) -> None:
    """Give the pinned connection of the test another time zone, until the
    rollback at the end of the test."""
    await session.execute(text(f"SET LOCAL TIME ZONE '{name}'"))
    await session.commit()


@pytest.mark.asyncio
async def test_a_body_time_without_an_offset_is_utc_on_psycopg(restly_async_session):
    await _set_session_time_zone(restly_async_session, "America/New_York")
    async with _async_client() as client:
        created = await client.post(
            "/meetings/",
            json={
                "name": "standup",
                "starts_at": "2024-01-01T09:30:00",
                "ends_at": "2024-01-01T09:45:00",
            },
        )
        listed = await client.get(
            "/meetings/",
            params={
                "ends_at__gte": "2024-01-01T09:45:00",
                "ends_at__lte": "2024-01-01T09:45:00",
            },
        )
    assert created.status_code == 201, created.text
    ends_at = datetime.fromisoformat(created.json()["ends_at"])
    assert ends_at == datetime(2024, 1, 1, 9, 45, tzinfo=timezone.utc)
    assert created.json()["starts_at"] == "2024-01-01T09:30:00"
    assert [row["name"] for row in listed.json()["data"]] == ["standup"]


@pytest.mark.asyncio
async def test_a_time_with_an_offset_is_utc_for_a_column_without_one_on_psycopg(
    restly_async_session,
):
    await _set_session_time_zone(restly_async_session, "America/New_York")
    async with _async_client() as client:
        for name, starts_at in [
            ("early", "2024-01-01T08:30:00Z"),
            ("late", "2024-01-01T10:30:00+01:00"),
        ]:
            created = await client.post(
                "/meetings/",
                json={
                    "name": name,
                    "starts_at": starts_at,
                    "ends_at": "2024-01-01T12:00:00Z",
                },
            )
            assert created.status_code == 201, created.text
        with_offset = await client.get(
            "/meetings/", params={"starts_at__gte": "2024-01-01T10:15:00+01:00"}
        )
        without_offset = await client.get(
            "/meetings/", params={"starts_at__gte": "2024-01-01T09:15:00"}
        )
    assert created.json()["starts_at"] == "2024-01-01T09:30:00"
    assert [row["name"] for row in with_offset.json()["data"]] == ["late"]
    assert [row["name"] for row in without_offset.json()["data"]] == ["late"]


@pytest.mark.asyncio
async def test_a_type_with_a_variant_keeps_the_offset_on_psycopg(restly_async_session):
    """The variant stores a time zone on PostgreSQL, where the type itself has
    none. Restly passes the time on with its offset, so PostgreSQL stores the
    right moment, whatever the time zone of the session."""
    await _set_session_time_zone(restly_async_session, "America/New_York")
    async with _async_client() as client:
        created = await client.post(
            "/meetings/",
            json={
                "name": "standup",
                "starts_at": "2024-01-01T09:30:00",
                "ends_at": "2024-01-01T09:45:00Z",
                "held_at": "2024-01-01T10:30:00+01:00",
            },
        )
    assert created.status_code == 201, created.text
    held_at = datetime.fromisoformat(created.json()["held_at"])
    assert held_at == datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_time_out_of_range_in_utc_is_a_client_error_on_psycopg(
    restly_async_session,
):
    """psycopg sends a time with an offset as it is, and PostgreSQL stores a
    year after 9999, which Python cannot read back. Restly rejects the time
    first."""
    late = "9999-12-31T23:30:00-01:00"
    async with _async_client() as client:
        created = await client.post(
            "/meetings/",
            json={"name": "late", "starts_at": "2024-01-01T09:30:00", "ends_at": late},
        )
        listed = await client.get("/meetings/", params={"ends_at__gte": late})
    assert created.status_code == 422, created.text
    assert listed.status_code == 400, listed.text


@pytest.mark.asyncio
async def test_a_react_admin_filter_time_is_utc_on_psycopg(restly_async_session):
    await _set_session_time_zone(restly_async_session, "America/New_York")

    async def names(client: httpx.AsyncClient, **filter_: str) -> list[str]:
        params = {"filter": json.dumps(filter_)}
        response = await client.get("/admin/meetings/", params=params)
        assert response.status_code == 200, response.text
        return [row["name"] for row in response.json()]

    async with _async_client() as client:
        created = await client.post(
            "/meetings/",
            json={
                "name": "standup",
                "starts_at": "2024-01-01T09:30:00Z",
                "ends_at": "2024-01-01T09:45:00Z",
            },
        )
        assert created.status_code == 201, created.text
        assert await names(client, starts_at="2024-01-01T10:30:00+01:00") == ["standup"]
        assert await names(client, ends_at="2024-01-01T09:45:00") == ["standup"]


@pytest.mark.asyncio
async def test_a_timedelta_column_is_a_native_interval_on_psycopg(restly_async_session):
    """A generated schema gives an ``Interval`` column a ``timedelta`` field.
    On PostgreSQL the column is a native ``INTERVAL``, and its range filters
    compare durations."""
    assert str(PgSpan.__table__.c.duration.type.compile(postgresql.dialect())) == (
        "INTERVAL"
    )
    async with _async_client() as client:
        for name, duration in [("long", "PT1H30M"), ("short", "PT10M")]:
            created = await client.post(
                "/spans/", json={"name": name, "duration": duration}
            )
            assert created.status_code == 201, created.text
        listed = await client.get("/spans/", params={"duration__gte": "PT1H"})
    assert listed.status_code == 200, listed.text
    assert [(row["name"], row["duration"]) for row in listed.json()["data"]] == [
        ("long", "PT1H30M")
    ]


# ---------------------------------------------------------------------------
# Context members embedded in a clause: a placeholder is a numbered bind, and
# psycopg sends numbered parameters its own way.
# ---------------------------------------------------------------------------


class _PgFloor(fr.ContextNamespace):
    score: fr.ContextParam[int]


class _PgCeiling(fr.ContextNamespace):
    """A second namespace that picks the same member name."""

    score: fr.ContextParam[int]


_in_band = fr.where_clause(
    and_(PgRanked.score >= _PgFloor.score, PgRanked.score <= _PgCeiling.score)
)


def _band_stmt():
    # bound tightly: the statement runs after both binds have ended
    with _PgFloor.bind(score=6), _PgCeiling.bind(score=15):
        return fr.apply_clauses(select(PgRanked.label), _in_band)


def test_same_name_members_bind_separately_on_psycopg():
    with _make_session() as session:
        assert session.scalars(_band_stmt()).all() == ["a"]


@pytest.mark.asyncio
async def test_same_name_members_bind_separately_on_async_psycopg():
    async with _async_make_session() as session:
        assert (await session.scalars(_band_stmt())).all() == ["a"]
