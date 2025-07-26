"""Test basic functionality of the framework."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Mapped

import fastapi_alchemy as fa


def test_crud_endpoints_exist():
    """Test that all CRUD endpoints are created."""
    fa.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = FastAPI()
    
    # Define a simple model
    class User(fa.IDBase):
        name: Mapped[str]
        email: Mapped[str]
    
    class UserSchema(fa.IDSchema[User]):
        name: str
        email: str
    
    @fa.include_view(app)
    class UserView(fa.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema
    
    client = TestClient(app)
    
    response = client.get("/openapi.json")
    assert response.status_code == 200
    
    spec = response.json()
    paths = spec["paths"]
    
    # Check that all expected endpoints exist
    assert "/users/" in paths
    assert "/users/{id}" in paths
    
    # Check HTTP methods
    user_paths = paths["/users/"]
    user_detail_paths = paths["/users/{id}"]
    
    assert "get" in user_paths  # List
    assert "post" in user_paths  # Create
    assert "get" in user_detail_paths  # Retrieve
    assert "put" in user_detail_paths  # Update
    assert "delete" in user_detail_paths  # Delete


def test_basic_crud_operations():
    """Test basic CRUD operations."""
    fa.setup_async_database_connection("sqlite+aiosqlite:///:memory:")
    
    app = FastAPI()
    
    # Define a simple model
    class Product(fa.IDBase):
        name: Mapped[str]
        price: Mapped[float]
    
    class ProductSchema(fa.IDSchema[Product]):
        name: str
        price: float
    
    @fa.include_view(app)
    class ProductView(fa.AsyncAlchemyView):
        prefix = "/products"
        model = Product
        schema = ProductSchema
    
    # Debug: Check what the update schema looks like
    print(f"Update schema fields: {ProductView.update_schema.model_fields.keys()}")
    print(f"Update schema required fields: {[name for name, field in ProductView.update_schema.model_fields.items() if field.is_required()]}")
    
    # Create tables using the same engine as the framework
    import asyncio
    
    async def create_tables():
        # Get the engine from the framework's session maker
        from fastapi_alchemy._globals import fa_globals
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fa.SQLBase.metadata.create_all)
    
    asyncio.run(create_tables())
    
    client = TestClient(app)
    
    # Test creating a product
    product_data = {"name": "Test Product", "price": 29.99}
    response = client.post("/products/", json=product_data)
    assert response.status_code == 201
    
    created_product = response.json()
    assert created_product["name"] == "Test Product"
    assert created_product["price"] == 29.99
    assert "id" in created_product
    
    # Test retrieving the product
    product_id = created_product["id"]
    response = client.get(f"/products/{product_id}")
    assert response.status_code == 200
    
    retrieved_product = response.json()
    assert retrieved_product["name"] == "Test Product"
    assert retrieved_product["price"] == 29.99
    
    # Test listing products
    response = client.get("/products/")
    assert response.status_code == 200
    products = response.json()
    assert len(products) >= 1
    
    # Test updating the product - this should now work without id in body
    update_data = {"name": "Updated Product", "price": 39.99}
    response = client.put(f"/products/{product_id}", json=update_data)
    if response.status_code != 200:
        print(f"PUT response: {response.status_code}")
        print(f"PUT response body: {response.text}")
    assert response.status_code == 200
    
    updated_product = response.json()
    assert updated_product["name"] == "Updated Product"
    assert updated_product["price"] == 39.99
    
    # Test deleting the product
    response = client.delete(f"/products/{product_id}")
    assert response.status_code == 204
    
    # Verify product is deleted
    response = client.get(f"/products/{product_id}")
    assert response.status_code == 404 