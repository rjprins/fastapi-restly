"""
Shop example for FastAPI-Restly.

This example demonstrates:
- Custom endpoints
- Custom filtering
- Custom sorting
- Custom pagination
- Custom validation
"""

from contextlib import asynccontextmanager
from typing import ClassVar
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from sqlalchemy import orm
from sqlalchemy.dialects.postgresql import UUID as POSTGRES_UUID
from starlette.middleware.cors import CORSMiddleware

import fastapi_restly as fr

fr.configure(async_database_url="sqlite+aiosqlite:///shop.db")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine = fr.get_async_engine()
    async with engine.connect() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_headers="*",
    allow_methods="*",
    allow_origins="*",
    expose_headers="*",
)


# Example of class using IDBase
# This includes an id primary key
class Customer(fr.IDBase):
    email: orm.Mapped[str]
    orders: orm.Mapped[list["Order"]] = orm.relationship(default_factory=list)


# Example of many-to-many
product_order_table = sa.Table(
    "product_order",
    fr.DataclassBase.metadata,
    sa.Column("product_id", sa.ForeignKey("product.id")),
    sa.Column("order_id", sa.ForeignKey("order.id")),
)


# Example with using UUID as primary key
class Product(fr.DataclassBase):
    id: orm.Mapped[UUID] = orm.mapped_column(primary_key=True, default_factory=uuid4)
    name: orm.Mapped[str]
    price: orm.Mapped[float]
    orders: orm.Mapped[list["Order"]] = orm.relationship(
        secondary=product_order_table, back_populates="products"
    )


# Example with TimestampsMixin
class Order(fr.IDBase, fr.TimestampsMixin):
    customer_id: orm.Mapped[int] = orm.mapped_column(sa.ForeignKey(Customer.id))
    products: orm.Mapped[list[Product]] = orm.relationship(
        secondary=product_order_table, back_populates="orders"
    )


class CustomerSchema(fr.IDSchema):
    email: str


class ProductSchema(fr.IDSchema):
    name: str
    price: float
    # Example with one-to-many id list
    orders: list[fr.IDSchema[Order]] = []


class OrderSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    # Example of embedded schema — read-only because it's resolved from customer_id
    customer: fr.ReadOnly[CustomerSchema | None]
    customer_id: fr.IDSchema[Customer]
    products: list[fr.IDSchema[Product]]


@fr.include_view(app)
class CustomerView(fr.AsyncRestView):
    prefix = "/customers"
    model = Customer
    schema = CustomerSchema


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema
    id_type = UUID


@fr.include_view(app)
class OrderView(fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
