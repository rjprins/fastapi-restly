"""Request authentication does not bind Current in the test body's context."""

from dataclasses import asdict

import pytest
from app.current import Current, SetCurrentContextDep
from app.users.roles import UserRole

from .conftest import SYSTEM_ADMIN_ID, app


def test_sync_client_requires_request_binding(current_probes, client, actor):
    assert client.get("/_test/current").json() == _unbound()
    assert client.get("/_test/bound-current").json() == asdict(actor)
    assert client.get("/_test/current").json() == _unbound()
    assert _read_current() == _unbound()


async def test_async_client_requires_request_binding(
    current_probes, async_client, async_actor
):
    assert (await async_client.get("/_test/current")).json() == _unbound()
    assert (await async_client.get("/_test/bound-current")).json() == asdict(
        async_actor
    )
    assert (await async_client.get("/_test/current")).json() == _unbound()
    assert _read_current() == _unbound()


def test_auth_overrides_restore_without_binding_current(
    current_probes, client, actor, auth_context, as_admin, anonymous
):
    with auth_context(role=UserRole.MEMBER):
        assert client.get("/_test/bound-current").json() == {
            **asdict(actor),
            "role": UserRole.MEMBER,
        }
        with as_admin(actor.org_id):
            assert client.get("/_test/bound-current").json() == {
                **asdict(actor),
                "user_id": SYSTEM_ADMIN_ID,
                "is_admin": True,
            }
            assert client.get("/_test/current").json() == _unbound()
        with anonymous():
            client.get("/_test/bound-current", assert_status_code=401)
            assert client.get("/_test/current").json() == _unbound()
        assert client.get("/_test/bound-current").json()["role"] == "member"
    assert client.get("/_test/bound-current").json() == asdict(actor)
    assert _read_current() == _unbound()


@pytest.fixture
def current_probes(monkeypatch):
    """Install two test-only routes, one with the binding dependency."""
    monkeypatch.setattr(app.router, "routes", list(app.router.routes))

    async def current():
        return _read_current()

    app.add_api_route("/_test/current", current)
    app.add_api_route(
        "/_test/bound-current", current, dependencies=[SetCurrentContextDep]
    )


def _read_current():
    values = {}
    for name in ("org_id", "user_id", "role", "is_admin"):
        try:
            values[name] = getattr(Current, name)()
        except LookupError:
            values[name] = None
    return values


def _unbound():
    return dict.fromkeys(("org_id", "user_id", "role", "is_admin"))
