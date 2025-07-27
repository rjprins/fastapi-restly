"""
Shop example for FastAPI-Ding.

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

import fastapi_ding as fd

fd.setup_async_database_connection(async_database_url="sqlite+aiosqlite:///shop.db")


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
class Customer(fd.IDBase):
    email: orm.Mapped[str]
    orders: orm.Mapped[list["Order"]] = orm.relationship(default_factory=list)


# Example of many-to-many
product_order_table = sa.Table(
    "product_order",
    fd.SQLBase.metadata,
    sa.Column("product_id", sa.ForeignKey("product.id")),
    sa.Column("order_id", sa.ForeignKey("order.id")),
)


# Example with using UUID as primary key
class Product(fd.SQLBase):
    id: orm.Mapped[UUID] = orm.mapped_column(primary_key=True, default_factory=uuid4)
    name: orm.Mapped[str]
    price: orm.Mapped[float]
    orders: orm.Mapped[list["Order"]] = orm.relationship(
        secondary=product_order_table, back_populates="products"
    )


# Example with TimestampsMixin
class Order(fd.IDBase, fd.TimestampsMixin):
    customer_id: orm.Mapped[int] = orm.mapped_column(sa.ForeignKey(Customer.id))
    products: orm.Mapped[list[Product]] = orm.relationship(
        secondary=product_order_table, back_populates="orders"
    )


class CustomerSchema(fd.IDSchema[Customer]):
    email: str


class ProductSchema(fd.IDSchema[Product]):
    name: str
    price: float
    # Example with one-to-many id list
    orders: list[fd.IDSchema[Order]] = []


class OrderSchema(fd.TimestampsSchemaMixin, fd.IDSchema[Order]):
    # Example of embedded schema
    customer: CustomerSchema
    customer_id: fd.IDSchema[Customer]
    products: list[fd.IDSchema[Product]]
    # Example of field-level read-only using the ReadOnly syntax
    internal_notes: fd.ReadOnly[str]


@fd.include_view(app)
class CustomerView(fd.AsyncAlchemyView):
    prefix = "/customers"
    model = Customer
    schema = CustomerSchema


@fd.include_view(app)
class ProductView(fd.AsyncAlchemyView):
    prefix = "/products"
    model = Product
    schema = ProductSchema


@fd.include_view(app)
class OrderView(fd.AsyncAlchemyView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
