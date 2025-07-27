"""Test Pydantic alias feature handling in FastAPI-Ding framework."""

import asyncio
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import Field
from sqlalchemy.orm import Mapped

import fastapi_ding as fd
from fastapi_ding._globals import fa_globals


def reset_metadata():
    """Reset SQLAlchemy metadata to prevent table redefinition conflicts."""
    fd.SQLBase.metadata.clear()


def test_pydantic_alias_feature(client):
    """Test that the framework correctly handles Pydantic's alias feature."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")

    app = client.app

    # Define a model with fields that have aliases
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        phone_number: Mapped[str]

    # Create a schema with Pydantic aliases
    class UserSchema(fd.IDSchema[User]):
        name: str
        email: str
        phone_number: str = Field(alias="phoneNumber")  # Using Pydantic alias

    # Create a view
    @fd.include_view(app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema

    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)

    asyncio.run(create_tables())

    # Test CREATE with alias - should accept both alias and field name
    # Using the alias (phoneNumber)
    response = client.post(
        "/users/",
        json={
            "name": "John Doe",
            "email": "john@example.com",
            "phoneNumber": "123-456-7890",  # Using alias
        },
    )
    
    created_user = response.json()
    assert "id" in created_user
    assert created_user["name"] == "John Doe"
    assert created_user["email"] == "john@example.com"
    assert (
        created_user["phoneNumber"] == "123-456-7890"
    )  # Should be returned with alias

    user_id = created_user["id"]

    # Test GET - should return with alias, not field name
    response = client.get(f"/users/{user_id}")
    
    user = response.json()
    assert user["name"] == "John Doe"
    assert user["email"] == "john@example.com"
    assert user["phoneNumber"] == "123-456-7890"  # Should be alias, not field name

    # Test UPDATE with alias
    # Note: The framework currently has an issue where it doesn't properly handle
    # aliases in UPDATE operations. The framework should map phoneNumber (alias) 
    # back to phone_number (field name) when updating the SQLAlchemy object.
    response = client.put(f"/users/{user_id}", json={
        "name": "Jane Doe",
        "email": "jane@example.com", 
        "phoneNumber": "098-765-4321"  # Using alias
    })
    
    updated_user = response.json()
    assert updated_user["name"] == "Jane Doe"
    assert updated_user["email"] == "jane@example.com"
    assert updated_user["phoneNumber"] == "098-765-4321"  # Should be alias


def test_pydantic_alias_with_multiple_aliases(client):
    """Test that the framework handles multiple aliased fields correctly."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")

    app = client.app

    # Define a model with multiple aliased fields
    class Product(fd.IDBase):
        name: Mapped[str]
        price: Mapped[float]
        description: Mapped[str]
        is_active: Mapped[bool]

    # Create a schema with multiple Pydantic aliases
    class ProductSchema(fd.IDSchema[Product]):
        name: str
        price: float
        description: str = Field(alias="productDescription")
        is_active: bool = Field(alias="isActive")

    # Create a view
    @fd.include_view(app)
    class ProductView(fd.AsyncAlchemyView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)

    asyncio.run(create_tables())

    # Test CREATE with multiple aliases
    response = client.post(
        "/products/",
        json={
            "name": "Test Product",
            "price": 29.99,
            "productDescription": "A great test product",  # Using alias
            "isActive": True,  # Using alias
        },
    )
    
    created_product = response.json()
    assert "id" in created_product
    assert created_product["name"] == "Test Product"
    assert created_product["price"] == 29.99
    assert created_product["productDescription"] == "A great test product"  # Alias
    assert created_product["isActive"] == True  # Alias

    product_id = created_product["id"]

    # Test GET - should return with aliases
    response = client.get(f"/products/{product_id}")
    
    product = response.json()
    assert product["name"] == "Test Product"
    assert product["price"] == 29.99
    assert product["productDescription"] == "A great test product"  # Alias
    assert product["isActive"] == True  # Alias


def test_pydantic_alias_with_auto_generated_schema(client):
    """Test that auto-generated schemas work correctly with aliases."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")

    app = client.app

    # Define a model with aliased fields
    class Article(fd.IDBase):
        title: Mapped[str]
        content: Mapped[str]
        author_name: Mapped[str]

    # Create a view WITHOUT specifying a schema - should be auto-generated
    @fd.include_view(app)
    class ArticleView(fd.AsyncAlchemyView):
        prefix = "/articles"
        model = Article
        # No schema specified - should be auto-generated!

    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)

    asyncio.run(create_tables())

    # Test that auto-generated schema works (without aliases)
    response = client.post(
        "/articles/",
        json={
            "title": "Test Article",
            "content": "Test content",
            "author_name": "Test Author",
        },
    )
    
    created_article = response.json()
    assert created_article["title"] == "Test Article"
    assert created_article["content"] == "Test content"
    assert created_article["author_name"] == "Test Author"


def test_pydantic_alias_with_query_parameters(client):
    """Test that query parameters work correctly with aliased fields."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")

    app = client.app

    # Define a model with aliased fields
    class Article(fd.IDBase):
        title: Mapped[str]
        content: Mapped[str]
        author_name: Mapped[str]
        publish_date: Mapped[str]

    # Create a schema with aliases
    class ArticleSchema(fd.IDSchema[Article]):
        title: str
        content: str
        author_name: str = Field(alias="authorName")
        publish_date: str = Field(alias="publishDate")

    # Create a view
    @fd.include_view(app)
    class ArticleView(fd.AsyncAlchemyView):
        prefix = "/articles"
        model = Article
        schema = ArticleSchema

    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)

    asyncio.run(create_tables())

    # Create some test data
    articles_data = [
        {
            "title": "First Article",
            "content": "Content 1",
            "authorName": "John Doe",
            "publishDate": "2024-01-01",
        },
        {
            "title": "Second Article",
            "content": "Content 2",
            "authorName": "Jane Smith",
            "publishDate": "2024-01-02",
        },
    ]

    for article_data in articles_data:
        response = client.post("/articles/", json=article_data)
    
    # Test query parameters - should work with field names, not aliases
    response = client.get("/articles/?author_name=John Doe")
    
    articles = response.json()
    assert len(articles) == 1
    assert articles[0]["title"] == "First Article"
    assert articles[0]["authorName"] == "John Doe"  # Alias, not field name


def test_pydantic_alias_with_field_validation(client):
    """Test that field validation works correctly with aliases."""
    reset_metadata()
    fd.setup_async_database_connection("sqlite+aiosqlite:///:memory:")

    app = client.app

    # Define a model with validation
    class User(fd.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        age: Mapped[int]

    # Create a schema with aliases and validation
    class UserSchema(fd.IDSchema[User]):
        name: str
        email: str = Field(alias="userEmail")
        age: int = Field(alias="userAge", ge=0, le=150)

    # Create a view
    @fd.include_view(app)
    class UserView(fd.AsyncAlchemyView):
        prefix = "/users"
        model = User
        schema = UserSchema

    # Create tables
    async def create_tables():
        engine = fa_globals.async_make_session.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fd.SQLBase.metadata.create_all)

    asyncio.run(create_tables())

    # Test CREATE with valid data using aliases
    response = client.post(
        "/users/",
        json={
            "name": "John Doe",
            "userEmail": "john@example.com",  # Using alias
            "userAge": 25,  # Using alias with validation
        },
    )
    
    created_user = response.json()
    assert created_user["name"] == "John Doe"
    assert created_user["userEmail"] == "john@example.com"  # Alias
    assert created_user["userAge"] == 25  # Alias

    # Test CREATE with invalid age (should fail validation)
    response = client.post(
        "/users/",
        json={
            "name": "Invalid User",
            "userEmail": "invalid@example.com",
            "userAge": 200,  # Invalid age (too high)
        },
    )
    assert response.status_code == 422  # Validation error

    # Test CREATE with negative age (should fail validation)
    response = client.post(
        "/users/",
        json={
            "name": "Invalid User",
            "userEmail": "invalid@example.com",
            "userAge": -5,  # Invalid age (negative)
        },
    )
    assert response.status_code == 422  # Validation error
