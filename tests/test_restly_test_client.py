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


def test_restly_test_client_skip_check():
    """Test that RestlyTestClient skips status code check when assert_status_code is None."""
    app = FastAPI()

    @app.get("/test")
    def test_get():
        return {"message": "success"}

    client = RestlyTestClient(app)

    # Test with assert_status_code=None - should not raise an error
    response = client.get("/test", assert_status_code=None)
    assert response.status_code == 200


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


def test_assert_status_code_none_still_rejects_an_error_status():
    """None relaxes the check to "any status below 400"; it does not skip it.

    The five method docstrings said "skip the assertion", and the only test for
    None used a 200, which passes either way.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from fastapi_restly.testing import RestlyTestClient

    app = FastAPI()

    @app.get("/gone")
    def gone():
        return JSONResponse({"detail": "nope"}, status_code=404)

    @app.get("/fine")
    def fine():
        return {"ok": True}

    client = RestlyTestClient(app)
    with client:
        assert client.get("/fine", assert_status_code=None).status_code == 200
        with pytest.raises(AssertionError):
            client.get("/gone", assert_status_code=None)


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
