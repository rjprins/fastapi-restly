"""Tests for non-dataclass declarative base support."""

import asyncio

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr


def test_custom_declarative_base_works_with_generated_async_crud(client):
    class Base(DeclarativeBase):
        pass

    class Product(Base):
        __tablename__ = "declarative_product"

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column()

    class ProductSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class ProductView(fr.AsyncRestView):
        prefix = "/declarative-products"
        model = Product
        schema = ProductSchema

    async def create_tables():
        engine = fr.get_async_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())

    create_response = client.post("/declarative-products/", json={"name": "Widget"})
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "Widget"

    get_response = client.get(f"/declarative-products/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Widget"
