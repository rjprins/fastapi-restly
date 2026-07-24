"""fznb.5: prove the shipped ``restly_async_session`` fixture actually isolates
each test and shares one connection.

``conftest.py`` wires an autouse ``_isolate_every_test(restly_async_session)``, so
the whole suite now runs on Restly's own testing story instead of a fresh
database per test. These tests fail loudly if either property regresses, which is
what makes the dog-fooding meaningful.
"""

# Two tests create an organization with the SAME unique slug. Both succeed only
# because each test's writes roll back at teardown (savepoint isolation). If
# isolation regressed, the second POST would hit the unique-slug constraint and
# the client's default 201 assertion would fail with a 409.
_SHARED_SLUG = "dogfood-isolation-org"


def test_isolation_first_claim_of_a_slug(client):
    client.post("/organizations/", json={"name": "First", "slug": _SHARED_SLUG})


def test_isolation_slug_is_free_again_in_the_next_test(client):
    client.post("/organizations/", json={"name": "Second", "slug": _SHARED_SLUG})


def test_write_is_visible_to_a_later_request(client):
    # Session-sharing: the POST commits on the pinned connection; the follow-up
    # GET is a separate request and session that must still see the row. Without
    # a shared connection the GET would 404 and fail the default 200 assertion.
    created = client.post(
        "/organizations/", json={"name": "Visible", "slug": "dogfood-visibility-org"}
    )
    org_id = created.json()["id"]
    fetched = client.get(f"/organizations/{org_id}")
    assert fetched.json()["slug"] == "dogfood-visibility-org"
