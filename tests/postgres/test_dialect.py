"""fznb.6 -- the dialect-divergent surface, pinned on real PostgreSQL.

Everything here is invisible on SQLite and only runs when the subtree is pointed
at PostgreSQL (see ``conftest.py``). Four things are covered:

* ``contains`` (``LIKE``) is case-sensitive and ``icontains`` (``ILIKE``) folds
  case, for ASCII *and* non-ASCII input. On SQLite ``LIKE`` is
  ASCII-case-insensitive and ``lower()`` is ASCII-only, so the two operators are
  indistinguishable and non-ASCII folding never happens.
* Default ``NULL`` ordering. The framework emits a plain ``ORDER BY`` with an id
  tiebreak and no explicit ``NULLS`` clause, so the placement is the dialect
  default -- ``NULLS LAST`` ascending / ``NULLS FIRST`` descending on PostgreSQL,
  the reverse of SQLite.
* Pagination stays stable across pages when many rows share a sort key, because
  the id tiebreak makes row order deterministic (PostgreSQL gives no stable
  order otherwise, unlike SQLite's rowid).
* The psycopg cross-session connection sharing documented for
  ``restly_session`` / ``restly_async_session`` -- the async session runs over
  the sync fixture's pinned connection, so a write through either is visible to
  the other within a test. This path only exists on a psycopg stack.
"""

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import CheckConstraint, ForeignKey, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

import fastapi_restly as fr
from fastapi_restly._exception_handlers import _build_integrity_detail

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


class PgWord(fr.IDBase):
    name: Mapped[str]


class PgWordSchema(fr.IDSchema):
    name: str


class PgRanked(fr.IDBase):
    label: Mapped[str]
    score: Mapped[int | None] = mapped_column(default=None)


class PgRankedSchema(fr.IDSchema):
    label: str
    score: int | None = None


class PgPaged(fr.IDBase):
    label: Mapped[str]
    bucket: Mapped[int]


class PgPagedSchema(fr.IDSchema):
    label: str
    bucket: int


class PgShared(fr.IDBase):
    name: Mapped[str]


class PgSharedSchema(fr.IDSchema):
    name: str


# Write-path models. Each carries a database constraint whose violation only
# raises a psycopg error (with a real SQLSTATE + diag) on PostgreSQL; SQLite
# either enforces it through a different, string-only error path or -- for
# foreign keys -- does not enforce it at all by default.
class PgUnique(fr.IDBase):
    code: Mapped[str] = mapped_column(unique=True)


class PgUniqueSchema(fr.IDSchema):
    code: str


class PgParent(fr.IDBase):
    name: Mapped[str]  # NOT NULL, reused for the not-null violation


class PgChild(fr.IDBase):
    # A plain scalar FK (no fr.MustExist), so existence is not pre-validated in
    # the app and the insert reaches the database constraint.
    parent_id: Mapped[int] = mapped_column(ForeignKey(PgParent.id))


class PgChildSchema(fr.IDSchema):
    parent_id: int


class PgChecked(fr.IDBase):
    amount: Mapped[int]
    __table_args__ = (CheckConstraint("amount >= 0", name="pg_checked_amount_nonneg"),)


class PgCheckedSchema(fr.IDSchema):
    amount: int


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
class PgPagedView(fr.AsyncRestView):
    prefix = "/paged"
    model = PgPaged
    schema = PgPagedSchema


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


_MODELS = [PgWord, PgRanked, PgPaged, PgShared, PgUnique, PgParent, PgChild, PgChecked]


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
        session.commit()


@pytest.fixture(scope="session", autouse=True)
def _pg_schema():
    """Build this module's tables once, seed them, and drop them afterwards.

    Only this module's tables are touched (``tables=``), so a shared registry or
    a leftover from an interrupted run cannot bleed in, and ``drop_all`` first
    clears any table left behind on the persistent server.
    """
    tables = [model.__table__ for model in _MODELS]
    metadata = fr.DataclassBase.metadata
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
    return sorted(row["name"] for row in response.json())


def _labels(response) -> list[str]:
    return [row["label"] for row in response.json()]


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


def test_null_ordering_ascending_places_nulls_last(restly_client):
    # PostgreSQL default: ascending sorts NULLs last. Non-nulls ascend
    # (5, 10, 20) then the NULL rows follow, ordered by the id tiebreak.
    labels = _labels(restly_client.get("/ranked/?sort=score"))
    assert labels == ["c", "a", "e", "b", "d"]


def test_null_ordering_descending_places_nulls_first(restly_client):
    # PostgreSQL default: descending sorts NULLs first. The NULL rows lead
    # (id tiebreak), then non-nulls descend (20, 10, 5).
    labels = _labels(restly_client.get("/ranked/?sort=-score"))
    assert labels == ["b", "d", "e", "a", "c"]


def test_pagination_is_stable_under_duplicate_sort_keys(restly_client):
    # Every row shares bucket=7, so without the id tiebreak PostgreSQL could
    # skip or repeat rows across pages. Paging must return each row exactly once.
    seen: list[int] = []
    for page in (1, 2, 3):
        rows = restly_client.get(f"/paged/?sort=bucket&page_size=2&page={page}").json()
        seen.extend(row["id"] for row in rows)
    assert len(seen) == 5
    assert len(set(seen)) == 5
    # The id tiebreak makes the page order deterministic (ascending id).
    assert seen == sorted(seen)


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
