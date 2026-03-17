"""Tests for non-dataclass declarative base support."""

import asyncio

from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr


def test_plain_id_base_works_with_generated_async_crud(client):
    class PlainProduct(fr.PlainIDBase):
        name: Mapped[str] = mapped_column()

    class PlainProductSchema(fr.IDSchema):
        name: str

    @fr.include_view(client.app)
    class PlainProductView(fr.AsyncAlchemyView):
        prefix = "/plain-products"
        model = PlainProduct
        schema = PlainProductSchema

    async def create_plain_tables():
        engine = fr.FRAsyncSession.kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(fr.PlainBase.metadata.create_all)

    asyncio.run(create_plain_tables())

    create_response = client.post("/plain-products/", json={"name": "Widget"})
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "Widget"

    get_response = client.get(f"/plain-products/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Widget"
