"""Fixtures for the SaaS suite.

``client`` and ``async_client`` override the auth sources to sign requests
in as Alice, the owner of a fresh organization named Acme. ``actor`` and
``async_actor`` expose that identity. ``new_tenant`` creates another identity
whose ``acting()`` method overrides the sources for a block. ``as_admin``
selects the seeded admin. ``anonymous`` removes the overrides.

These helpers do not bind ``Current``. A request must run
``SetCurrentContextDep`` to bind its own identity. Direct database checks
bind explicitly with ``Current.bind(**asdict(actor))`` around the session.

The admin is seeded by the migration in ``alembic/versions`` and is the
only user without a creator. Signing in creates the organization and Alice
through the API as the admin acting in the new organization, the way a
signup flow would. The suite runs in Restly's rollback mode, so the seeded
rows survive every test; delete mode would empty ``organization`` and
``user`` before each test and nothing would put the admin back.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from app.current import (
    get_current_org_id,
    get_current_role,
    get_current_user_id,
    get_is_admin,
)
from app.main import create_app
from app.settings import Settings
from app.users.roles import UserRole
from sqlalchemy import make_url

import fastapi_restly as fr
from fastapi_restly.testing import AsyncRestlyTestClient, RestlyTestClient

# The framework under test must live in this checkout, else a leaked VIRTUAL_ENV
# (e.g. the main framework .venv) silently validates the wrong source in a worktree.
_checkout = Path(__file__).resolve().parents[3]
_frl = Path(fr.__file__).resolve()
if _checkout not in _frl.parents:
    raise RuntimeError(
        f"fastapi_restly under test is {_frl}, outside this checkout ({_checkout}). "
        f"This example's venv isn't synced to this tree — run `uv sync` here."
    )

# Select the dedicated test service. The application is built by a factory, so
# the suite passes the test database explicitly instead of mutating the process
# environment before an import. CI can point the suite at its PostgreSQL
# service through SAAS_TEST_DATABASE_URL or RESTLY_TEST_DATABASE_URL.
_raw_test_url = os.environ.get(
    "SAAS_TEST_DATABASE_URL",
    os.environ.get(
        "RESTLY_TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5433/saas_test",
    ),
)
_test_url = make_url(_raw_test_url)
if _test_url.get_backend_name() != "postgresql":
    raise pytest.UsageError("The SaaS test suite requires a PostgreSQL database URL")
_test_url = _test_url.set(drivername="postgresql+asyncpg")

# Disable the local .env file so omitted settings do not pick up a developer's
# dotenv values. An exported shell variable (e.g. DB_POOL_SIZE) still applies,
# the same as it would in production, since only the file source is disabled.
Settings.use(
    Settings(
        database_url=_test_url.render_as_string(hide_password=False), _env_file=None
    )
)
app = create_app()

# Dog-food the migration-backed setup against the database the suite installed.
fr.testing.configure_tests(
    app=app,
    base=fr.DataclassBase,
    alembic_upgrade=True,
    db_cleanup_exclude=("country",),
)

# Seeded by alembic/versions/a8d5d13dea9a_seed_system_admin.py.
SYSTEM_ORG_ID = 1
SYSTEM_ADMIN_ID = 1

_AUTH_SOURCES = (
    get_current_org_id,
    get_current_user_id,
    get_current_role,
    get_is_admin,
)


@dataclass
class Tenant:
    """An identity a test can act as: a user in an organization."""

    org_id: int
    user_id: int
    role: UserRole = UserRole.OWNER
    is_admin: bool = False

    def acting(self) -> Iterator[None]:
        """Authenticate requests as this identity without binding Current."""
        return _override_auth(
            org_id=self.org_id,
            user_id=self.user_id,
            role=self.role,
            is_admin=self.is_admin,
        )


@contextmanager
def _override_auth(
    *,
    org_id: int | None = None,
    user_id: int | None = None,
    role: UserRole | None = None,
    is_admin: bool | None = None,
) -> Iterator[None]:
    """Override named auth sources without supplying a Current binding."""
    sources = {
        get_current_org_id: org_id,
        get_current_user_id: user_id,
        get_current_role: role,
        get_is_admin: is_admin,
    }
    previous = app.dependency_overrides.copy()
    for source, value in sources.items():
        if value is not None:
            app.dependency_overrides[source] = lambda value=value: value
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _as_admin(org_id: int) -> Iterator[None]:
    """Act as the seeded platform admin inside ``org_id``."""
    return _override_auth(
        org_id=org_id, user_id=SYSTEM_ADMIN_ID, role=UserRole.OWNER, is_admin=True
    )


_ACME = {"name": "Acme", "slug": "acme"}
_ALICE = {"email": "alice@acme.test", "name": "Alice", "role": "owner"}


@pytest.fixture
def actor(restly_client: RestlyTestClient) -> Iterator[Tenant]:
    """Alice, owner of Acme: the identity ``client`` acts as."""
    org = restly_client.post("/organizations", json=_ACME).json()
    # The first user of an organization is created by the admin acting in it.
    with _as_admin(org["id"]):
        alice = restly_client.post("/users", json=_ALICE).json()
    identity = Tenant(org_id=org["id"], user_id=alice["id"])
    with identity.acting():
        yield identity


@pytest.fixture
def client(restly_client: RestlyTestClient, actor: Tenant) -> RestlyTestClient:
    """The isolated Restly test client, signed in as ``actor``."""
    return restly_client


@pytest.fixture
async def _async_identity(restly_async_client: AsyncRestlyTestClient) -> Tenant:
    org = (await restly_async_client.post("/organizations", json=_ACME)).json()
    with _as_admin(org["id"]):
        alice = (await restly_async_client.post("/users", json=_ALICE)).json()
    return Tenant(org_id=org["id"], user_id=alice["id"])


@pytest.fixture
def async_actor(_async_identity: Tenant) -> Iterator[Tenant]:
    """Authenticate async-client requests as ``_async_identity``."""
    with _async_identity.acting():
        yield _async_identity


@pytest.fixture
async def async_client(
    restly_async_client: AsyncRestlyTestClient, async_actor: Tenant
) -> AsyncRestlyTestClient:
    """The isolated async client, signed in as ``async_actor``."""
    return restly_async_client


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    """Ensure dependency overrides do not leak between tests."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def auth_context() -> Callable[..., Iterator[None]]:
    """Override the auth sources for a block: org_id, user_id, role, is_admin."""
    return _override_auth


@pytest.fixture
def as_admin() -> Callable[[int], Iterator[None]]:
    """Act as the seeded platform admin inside an organization for a block."""
    return _as_admin


@pytest.fixture
def anonymous() -> Callable[[], Iterator[None]]:
    """Drop the identity for a block: the placeholder auth sources run instead."""

    @contextmanager
    def override() -> Iterator[None]:
        previous = app.dependency_overrides.copy()
        for source in _AUTH_SOURCES:
            app.dependency_overrides.pop(source, None)
        try:
            yield
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)

    return override


@pytest.fixture
def new_tenant(client: RestlyTestClient) -> Callable[..., Tenant]:
    """Make another organization with one user; act as them with ``.acting()``."""

    def make(slug: str, role: UserRole = UserRole.OWNER) -> Tenant:
        org = client.post(
            "/organizations", json={"name": slug.title(), "slug": slug}
        ).json()
        with _as_admin(org["id"]):
            user = client.post(
                "/users",
                json={
                    "email": f"{role.value}@{slug}.test",
                    "name": slug.title(),
                    "role": role.value,
                },
            ).json()
        return Tenant(org_id=org["id"], user_id=user["id"], role=role)

    return make
