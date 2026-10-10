"""Test RestlyTestClient functionality."""

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from fastapi_restly.testing import AsyncRestlyTestClient, RestlyTestClient


def test_restly_test_client_basic_functionality():
    """Test that RestlyTestClient works with default response codes."""
    app = FastAPI()

    @app.get("/test")
    def test_get():
        return {"message": "success"}

    @app.post("/test", status_code=status.HTTP_201_CREATED)
    def test_post():
        return {"message": "created"}

    @app.put("/test")
    def test_put():
        return {"message": "updated"}

    @app.patch("/test")
    def test_patch():
        return {"message": "patched"}

    @app.delete("/test", status_code=status.HTTP_204_NO_CONTENT)
    def test_delete():
        return {"message": "deleted"}

    client = RestlyTestClient(app)

    # Test with default response codes
    response = client.get("/test")
    assert response.status_code == 200
    assert response.json()["message"] == "success"

    response = client.post("/test")
    assert response.status_code == 201
    assert response.json()["message"] == "created"

    response = client.put("/test")
    assert response.status_code == 200
    assert response.json()["message"] == "updated"

    response = client.patch("/test")
    assert response.status_code == 200
    assert response.json()["message"] == "patched"

    response = client.delete("/test")
    assert response.status_code == 204


def test_restly_test_client_custom_assert_status_codes():
    """Test that RestlyTestClient works with custom response codes."""
    app = FastAPI()

    @app.get("/test")
    def test_get():
        return {"message": "success"}

    @app.post("/test", status_code=status.HTTP_201_CREATED)
    def test_post():
        return {"message": "created"}

    client = RestlyTestClient(app)

    # Test with custom response codes
    response = client.get("/test", assert_status_code=200)
    assert response.status_code == 200

    response = client.post("/test", assert_status_code=201)
    assert response.status_code == 201


def test_restly_test_client_error_handling():
    """Test that RestlyTestClient provides clear error messages."""
    app = FastAPI()

    @app.get("/test")
    def test_get():
        return {"message": "success"}

    client = RestlyTestClient(app)

    # Test that it raises an error when status code doesn't match
    with pytest.raises(AssertionError) as exc_info:
        client.get("/test", assert_status_code=404)

    error_message = str(exc_info.value)
    assert "Expected GET" in error_message
    assert "/test" in error_message
    assert "to return 404, got 200" in error_message
    assert "Response JSON:" in error_message


def test_restly_test_client_backward_compatibility():
    """Test that RestlyTestClient is backward compatible with existing code."""
    app = FastAPI()

    @app.get("/test")
    def test_get():
        return {"message": "success"}

    # Test that it works like a regular TestClient
    regular_client = TestClient(app)
    restly_client = RestlyTestClient(app)

    regular_response = regular_client.get("/test")
    restly_response = restly_client.get("/test")

    assert regular_response.status_code == restly_response.status_code
    assert regular_response.json() == restly_response.json()


def _status_app() -> FastAPI:
    """An app whose routes answer with a fixed status code."""
    from fastapi.responses import JSONResponse, RedirectResponse

    app = FastAPI()

    @app.post("/publish")
    def publish():
        return {"published": True}  # an action: 200, not 201

    @app.get("/gone")
    def gone():
        return JSONResponse({"detail": "nope"}, status_code=404)

    @app.get("/broken")
    def broken():
        return JSONResponse({"detail": "boom"}, status_code=500)

    @app.get("/moved")
    def moved():
        return RedirectResponse("/publish", status_code=302)

    return app


def test_default_accepts_any_success_status():
    """A POST action that answers 200 passes without assert_status_code."""
    client = RestlyTestClient(_status_app())

    assert client.post("/publish").status_code == 200


def test_default_rejects_an_error_and_a_redirect():
    """The default is a success status (2xx), not merely "below 400"."""
    client = RestlyTestClient(_status_app(), follow_redirects=False)

    with pytest.raises(AssertionError, match="to return 2xx, got 404"):
        client.get("/gone")
    with pytest.raises(AssertionError, match="to return 2xx, got 302"):
        client.get("/moved")


def test_an_int_requires_that_exact_status():
    client = RestlyTestClient(_status_app())

    with pytest.raises(AssertionError, match="to return 201, got 200"):
        client.post("/publish", assert_status_code=201)


def test_assert_status_code_none_skips_the_check():
    client = RestlyTestClient(_status_app())

    assert client.get("/gone", assert_status_code=None).status_code == 404
    assert client.get("/broken", assert_status_code=None).status_code == 500


@pytest.mark.asyncio
async def test_async_client_checks_status_like_the_sync_client():
    async with AsyncRestlyTestClient(_status_app()) as client:
        assert (await client.post("/publish")).status_code == 200
        with pytest.raises(AssertionError, match="to return 2xx, got 404"):
            await client.get("/gone")
        response = await client.get("/gone", assert_status_code=None)
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_async_restly_test_client_matches_sync_status_assertions():
    app = FastAPI()

    @app.get("/test")
    async def test_get():
        return {"method": "get"}

    @app.post("/test", status_code=201)
    async def test_post():
        return {"method": "post"}

    @app.put("/test")
    async def test_put():
        return {"method": "put"}

    @app.patch("/test")
    async def test_patch():
        return {"method": "patch"}

    @app.delete("/test", status_code=204)
    async def test_delete():
        return None

    async with AsyncRestlyTestClient(app) as client:
        assert (await client.get("/test")).json() == {"method": "get"}
        assert (await client.post("/test")).json() == {"method": "post"}
        assert (await client.put("/test")).json() == {"method": "put"}
        assert (await client.patch("/test")).json() == {"method": "patch"}
        assert (await client.delete("/test")).status_code == 204

        with pytest.raises(AssertionError, match="to return 404, got 200"):
            await client.get("/test", assert_status_code=404)


@pytest.mark.asyncio
async def test_async_client_follows_redirects_like_the_sync_client():
    app = FastAPI()

    @app.get("/items/")
    async def items():
        return {"items": []}

    async with AsyncRestlyTestClient(app) as client:
        response = await client.get("/items")

    assert response.status_code == 200
    assert response.json() == {"items": []}
