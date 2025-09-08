"""
Shop example for FastAPI-Restly.

This example demonstrates:
- Custom endpoints
- Custom filtering
- Custom sorting
- Custom pagination
- Custom validation
"""

from typing import ClassVar
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from sqlalchemy import orm
from sqlalchemy.dialects.postgresql import UUID as POSTGRES_UUID
from starlette.middleware.cors import CORSMiddleware

import fastapi_restly as fr

fr.setup_async_database_connection(async_database_url="sqlite+aiosqlite:///shop.db")


app = FastAPI()

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
    fr.Base.metadata,
    sa.Column("product_id", sa.ForeignKey("product.id")),
    sa.Column("order_id", sa.ForeignKey("order.id")),
)


# Example with using UUID as primary key
class Product(fr.Base):
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


class CustomerSchema(fr.IDSchema[Customer]):
    email: str


class ProductSchema(fr.IDSchema[Product]):
    name: str
    price: float
    # Example with one-to-many id list
    orders: list[fr.IDSchema[Order]] = []


class OrderSchema(fr.TimestampsSchemaMixin, fr.IDSchema[Order]):
    # Example of embedded schema
    customer: CustomerSchema
    customer_id: fr.IDSchema[Customer]
    products: list[fr.IDSchema[Product]]
    # Example of field-level read-only using the ReadOnly syntax
    internal_notes: fr.ReadOnly[str]


@fr.include_view(app)
class CustomerView(fr.AsyncAlchemyView):
    prefix = "/customers"
    model = Customer
    schema = CustomerSchema


@fr.include_view(app)
class ProductView(fr.AsyncAlchemyView):
    prefix = "/products"
    model = Product
    schema = ProductSchema


@fr.include_view(app)
class OrderView(fr.AsyncAlchemyView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
