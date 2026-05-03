"""Tests for Pydantic alias functionality in FastAPI-Restly framework."""

from pydantic import Field
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from .conftest import create_tables


def test_get_requests_return_aliases(client):
    """Test that GET requests return data with aliases."""

    class User(fr.IDBase):
        user_name: Mapped[str]
        user_email: Mapped[str]
        phone_number: Mapped[str]

    class UserSchema(fr.IDSchema):
        user_name: str = Field(alias="userName")
        user_email: str = Field(alias="userEmail")
        phone_number: str = Field(alias="phoneNumber")

    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

    # Create a user
    response = client.post(
        "/users/",
        json={
            "userName": "John Doe",
            "userEmail": "john@example.com",
            "phoneNumber": "123-456-7890",
        },
    )
    created_user = response.json()
    user_id = created_user["id"]

    # Test GET single item returns aliases
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 200
    user = response.json()
    assert "userName" in user
    assert "userEmail" in user
    assert "phoneNumber" in user

    # Test GET list returns aliases
    response = client.get("/users/")
    assert response.status_code == 200
    users = response.json()
    assert len(users) == 1
    user = users[0]
    assert "userName" in user
    assert "userEmail" in user
    assert "phoneNumber" in user


def test_post_requests_accept_aliases(client):
    """Test that POST requests accept aliases."""

    class Product(fr.IDBase):
        product_name: Mapped[str]
        product_price: Mapped[float]
        is_active: Mapped[bool]

    class ProductSchema(fr.IDSchema):
        product_name: str = Field(alias="productName")
        product_price: float = Field(alias="productPrice")
        is_active: bool = Field(alias="isActive")

    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
        prefix = "/products"
        model = Product
        schema = ProductSchema

    create_tables()

    # Test POST with aliases succeeds
    response = client.post(
        "/products/",
        json={"productName": "Test Product", "productPrice": 29.99, "isActive": True},
    )
    assert response.status_code == 201

    # Test POST with field names fails (should only accept aliases)
    response = client.post(
        "/products/",
        json={
            "product_name": "Test Product 2",
            "product_price": 39.99,
            "is_active": False,
        },
        assert_status_code=422,
    )


def test_put_requests_accept_aliases(client):
    """Test that PUT requests accept aliases."""

    class Article(fr.IDBase):
        article_title: Mapped[str]
        article_content: Mapped[str]
        author_name: Mapped[str]

    class ArticleSchema(fr.IDSchema):
        article_title: str = Field(alias="articleTitle")
        article_content: str = Field(alias="articleContent")
        author_name: str = Field(alias="authorName")

    @fr.include_view(client.app)
    class ArticleView(fr.AsyncRestView):
        prefix = "/articles"
        model = Article
        schema = ArticleSchema

    create_tables()

    # Create an article
    response = client.post(
        "/articles/",
        json={
            "articleTitle": "Original Title",
            "articleContent": "Original content",
            "authorName": "Original Author",
        },
    )
    created_article = response.json()
    article_id = created_article["id"]

    # Test PUT with aliases succeeds
    response = client.patch(
        f"/articles/{article_id}",
        json={
            "articleTitle": "Updated Title",
            "articleContent": "Updated content",
            "authorName": "Updated Author",
        },
    )
    assert response.status_code == 200

    # Test PUT with field names also succeeds (populate_by_name=True allows both)
    response = client.patch(
        f"/articles/{article_id}",
        json={
            "article_title": "Updated Title 2",
            "article_content": "Updated content 2",
            "author_name": "Updated Author 2",
        },
    )
    assert response.status_code == 200


def test_query_modifiers_with_aliases(client):
    """Test that query modifiers work with aliases."""

    class Customer(fr.IDBase):
        customer_name: Mapped[str]
        customer_email: Mapped[str]
        registration_date: Mapped[str]

    class CustomerSchema(fr.IDSchema):
        customer_name: str = Field(alias="customerName")
        customer_email: str = Field(alias="customerEmail")
        registration_date: str = Field(alias="registrationDate")

    @fr.include_view(client.app)
    class CustomerView(fr.AsyncRestView):
        prefix = "/customers"
        model = Customer
        schema = CustomerSchema

    create_tables()

    # Create test data
    customers_data = [
        {
            "customerName": "John Doe",
            "customerEmail": "john@example.com",
            "registrationDate": "2024-01-01",
        },
        {
            "customerName": "Jane Smith",
            "customerEmail": "jane@example.com",
            "registrationDate": "2024-01-02",
        },
    ]

    for customer_data in customers_data:
        client.post("/customers/", json=customer_data)

    # Test query with aliases
    response = client.get("/customers/?customerName=John Doe")
    assert response.status_code == 200
    customers = response.json()
    assert len(customers) == 1
    assert customers[0]["customerName"] == "John Doe"

    # Test range queries with aliases
    response = client.get("/customers/?registrationDate__gte=2024-01-02")
    assert response.status_code == 200
    customers = response.json()
    assert len(customers) == 1
    assert customers[0]["customerName"] == "Jane Smith"


def test_validation_with_aliases(client):
    """Test that field validation works with aliases."""

    class User(fr.IDBase):
        user_name: Mapped[str]
        user_age: Mapped[int]
        user_email: Mapped[str]

    class UserSchema(fr.IDSchema):
        user_name: str = Field(alias="userName", min_length=2)
        user_age: int = Field(alias="userAge", ge=0, le=150)
        user_email: str = Field(alias="userEmail", pattern=r"^[^@]+@[^@]+\.[^@]+$")

    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

    # Test valid data with aliases
    response = client.post(
        "/users/",
        json={"userName": "John", "userAge": 25, "userEmail": "john@example.com"},
    )
    assert response.status_code == 201

    # Test invalid data with aliases
    response = client.post(
        "/users/",
        json={
            "userName": "J",  # Too short
            "userAge": 25,
            "userEmail": "john@example.com",
        },
        assert_status_code=422,
    )

    response = client.post(
        "/users/",
        json={
            "userName": "John",
            "userAge": 200,  # Too high
            "userEmail": "john@example.com",
        },
        assert_status_code=422,
    )

    response = client.post(
        "/users/",
        json={
            "userName": "John",
            "userAge": 25,
            "userEmail": "invalid-email",  # Invalid email
        },
        assert_status_code=422,
    )


def test_optional_fields_with_aliases(client):
    """Test that optional fields work with aliases."""

    class Profile(fr.IDBase):
        profile_name: Mapped[str]
        profile_bio: Mapped[str]
        profile_website: Mapped[str | None]

    class ProfileSchema(fr.IDSchema):
        profile_name: str = Field(alias="profileName")
        profile_bio: str = Field(alias="profileBio")
        profile_website: str | None = Field(alias="profileWebsite", default=None)

    @fr.include_view(client.app)
    class ProfileView(fr.AsyncRestView):
        prefix = "/profiles"
        model = Profile
        schema = ProfileSchema

    create_tables()

    # Test POST with optional field using alias
    response = client.post(
        "/profiles/",
        json={
            "profileName": "John Doe",
            "profileBio": "Software developer",
            "profileWebsite": "https://johndoe.com",
        },
    )
    assert response.status_code == 201

    # Test POST without optional field
    response = client.post(
        "/profiles/", json={"profileName": "Jane Smith", "profileBio": "Designer"}
    )
    assert response.status_code == 201


def test_auto_generated_schema_works_without_aliases(client):
    """Test that auto-generated schemas work without aliases."""

    class Comment(fr.IDBase):
        comment_text: Mapped[str]
        author_name: Mapped[str]

    @fr.include_view(client.app)
    class CommentView(fr.AsyncRestView):
        prefix = "/comments"
        model = Comment

    create_tables()

    # Test that auto-generated schema works (without aliases)
    response = client.post(
        "/comments/", json={"comment_text": "Great post!", "author_name": "John Doe"}
    )
    assert response.status_code == 201
    created_comment = response.json()
    assert created_comment["comment_text"] == "Great post!"
    assert created_comment["author_name"] == "John Doe"

    # Test GET returns field names (no aliases in auto-generated schema)
    comment_id = created_comment["id"]
    response = client.get(f"/comments/{comment_id}")
    assert response.status_code == 200
    comment = response.json()
    assert comment["comment_text"] == "Great post!"
    assert comment["author_name"] == "John Doe"


def test_complex_alias_scenarios(client):
    """Test complex scenarios with multiple aliases."""

    class Order(fr.IDBase):
        order_number: Mapped[str]
        total_amount: Mapped[float]
        shipping_address: Mapped[str]
        billing_address: Mapped[str]
        order_status: Mapped[str]

    class OrderSchema(fr.IDSchema):
        order_number: str = Field(alias="orderNumber")
        total_amount: float = Field(alias="totalAmount")
        shipping_address: str = Field(alias="shippingAddress")
        billing_address: str = Field(alias="billingAddress")
        order_status: str = Field(alias="orderStatus")

    @fr.include_view(client.app)
    class OrderView(fr.AsyncRestView):
        prefix = "/orders"
        model = Order
        schema = OrderSchema

    create_tables()

    # Test POST with all aliases
    response = client.post(
        "/orders/",
        json={
            "orderNumber": "ORD-001",
            "totalAmount": 99.99,
            "shippingAddress": "123 Main St",
            "billingAddress": "123 Main St",
            "orderStatus": "pending",
        },
    )
    assert response.status_code == 201

    created_order = response.json()
    order_id = created_order["id"]

    # Test GET returns aliases
    response = client.get(f"/orders/{order_id}")
    assert response.status_code == 200
    order = response.json()
    assert "orderNumber" in order
    assert "totalAmount" in order
    assert "shippingAddress" in order
    assert "billingAddress" in order
    assert "orderStatus" in order


def test_documentation_example(client):
    """Test the example from the documentation."""

    class User(fr.IDBase):
        name: Mapped[str]
        email: Mapped[str]
        phone_number: Mapped[str]

    class UserSchema(fr.IDSchema):
        name: str
        email: str
        phone_number: str = Field(alias="phoneNumber")

    @fr.include_view(client.app)
    class UserView(fr.AsyncRestView):
        prefix = "/users"
        model = User
        schema = UserSchema

    create_tables()

    # Test CREATE with alias
    response = client.post(
        "/users/",
        json={
            "name": "John Doe",
            "email": "john@example.com",
            "phoneNumber": "123-456-7890",
        },
    )
    assert response.status_code == 201

    created_user = response.json()
    user_id = created_user["id"]

    # Test GET returns with alias
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 200
    user = response.json()
    assert "phoneNumber" in user
    assert user["name"] == "John Doe"
    assert user["email"] == "john@example.com"

    # Test UPDATE with alias
    response = client.patch(
        f"/users/{user_id}",
        json={
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phoneNumber": "098-765-4321",
        },
    )
    assert response.status_code == 200
