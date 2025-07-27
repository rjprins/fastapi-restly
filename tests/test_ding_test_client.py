"""Test DingTestClient functionality."""

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from fastapi_ding.testing import DingTestClient


def test_ding_test_client_basic_functionality():
    """Test that DingTestClient works with default response codes."""
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
    
    @app.delete("/test", status_code=status.HTTP_204_NO_CONTENT)
    def test_delete():
        return {"message": "deleted"}
    
    client = DingTestClient(app)
    
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
    
    response = client.delete("/test")
    assert response.status_code == 204


def test_ding_test_client_custom_response_codes():
    """Test that DingTestClient works with custom response codes."""
    app = FastAPI()
    
    @app.get("/test")
    def test_get():
        return {"message": "success"}
    
    @app.post("/test", status_code=status.HTTP_201_CREATED)
    def test_post():
        return {"message": "created"}
    
    client = DingTestClient(app)
    
    # Test with custom response codes
    response = client.get("/test", response_code=200)
    assert response.status_code == 200
    
    response = client.post("/test", response_code=201)
    assert response.status_code == 201


def test_ding_test_client_skip_check():
    """Test that DingTestClient skips status code check when response_code is None."""
    app = FastAPI()
    
    @app.get("/test")
    def test_get():
        return {"message": "success"}
    
    client = DingTestClient(app)
    
    # Test with response_code=None - should not raise an error
    response = client.get("/test", response_code=None)
    assert response.status_code == 200


def test_ding_test_client_error_handling():
    """Test that DingTestClient provides clear error messages."""
    app = FastAPI()
    
    @app.get("/test")
    def test_get():
        return {"message": "success"}
    
    client = DingTestClient(app)
    
    # Test that it raises an error when status code doesn't match
    with pytest.raises(AssertionError) as exc_info:
        client.get("/test", response_code=404)
    
    error_message = str(exc_info.value)
    assert "Expected GET to return 404, got 200" in error_message
    assert "Response JSON:" in error_message


def test_ding_test_client_backward_compatibility():
    """Test that DingTestClient is backward compatible with existing code."""
    app = FastAPI()
    
    @app.get("/test")
    def test_get():
        return {"message": "success"}
    
    # Test that it works like a regular TestClient
    regular_client = TestClient(app)
    ding_client = DingTestClient(app)
    
    regular_response = regular_client.get("/test")
    ding_response = ding_client.get("/test")
    
    assert regular_response.status_code == ding_response.status_code
    assert regular_response.json() == ding_response.json() 