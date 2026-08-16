from fastapi_restly.testing import RestlyTestClient


def test_create_and_list(restly_client: RestlyTestClient) -> None:
    created = restly_client.post(
        "/users", json={"email": "ada@example.com", "name": "Ada"}
    ).json()

    assert created["email"] == "ada@example.com"
    assert created["name"] == "Ada"

    listing = restly_client.get("/users").json()

    assert [user["id"] for user in listing["data"]] == [created["id"]]
    assert listing["total_count"] == 1


def test_get_one(restly_client: RestlyTestClient) -> None:
    created = restly_client.post(
        "/users", json={"email": "grace@example.com", "name": "Grace"}
    ).json()

    assert restly_client.get(f"/users/{created['id']}").json() == created


def test_update(restly_client: RestlyTestClient) -> None:
    created = restly_client.post(
        "/users", json={"email": "alan@example.com", "name": "Alan"}
    ).json()

    updated = restly_client.patch(
        f"/users/{created['id']}", json={"name": "Alan Turing"}
    ).json()

    assert updated["name"] == "Alan Turing"
    assert updated["email"] == "alan@example.com"


def test_delete(restly_client: RestlyTestClient) -> None:
    created = restly_client.post(
        "/users", json={"email": "temp@example.com", "name": "Temporary"}
    ).json()

    restly_client.delete(f"/users/{created['id']}")

    restly_client.get(f"/users/{created['id']}", assert_status_code=404)


def test_id_is_read_only(restly_client: RestlyTestClient) -> None:
    created = restly_client.post(
        "/users", json={"id": 4242, "email": "not@mine.example", "name": "Not Mine"}
    ).json()

    assert created["id"] != 4242


def test_health(restly_client: RestlyTestClient) -> None:
    assert restly_client.get("/health").json() == {"status": "ok"}
