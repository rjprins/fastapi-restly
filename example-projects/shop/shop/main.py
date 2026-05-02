"""
Shop example for FastAPI-Restly.

This example demonstrates auto-generated CRUD with React-Admin-compatible
endpoints, integer/UUID primary keys, timestamp mixins, and one-to-many and
many-to-many relationships -- all driven by the framework's defaults with no
custom endpoints, filters, or schema overrides.

For an example showcasing customization (custom endpoints, hooks, validation,
authorization), see ``example-projects/saas``.
"""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import orm
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
    orders: orm.Mapped[list["Order"]] = orm.relationship(default_factory=list, lazy="selectin")


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
        secondary=product_order_table, back_populates="products", lazy="selectin",
        default_factory=list,
    )


# Example with TimestampsMixin
class Order(fr.IDBase, fr.TimestampsMixin):
    customer_id: orm.Mapped[int] = orm.mapped_column(sa.ForeignKey(Customer.id))
    products: orm.Mapped[list[Product]] = orm.relationship(
        secondary=product_order_table, back_populates="orders", lazy="selectin"
    )


class CustomerSchema(fr.IDSchema):
    email: str
    orders: fr.ReadOnly[list[fr.FlatIDSchema[Order]]] = []


class ProductSchema(fr.IDSchema):
    name: str
    price: float
    orders: fr.ReadOnly[list[fr.FlatIDSchema[Order]]] = []


class OrderSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    customer: fr.ReadOnly[CustomerSchema | None]
    customer_id: int
    products: list[fr.FlatIDSchema[Product]]


@fr.include_view(app)
class CustomerView(fr.AsyncReactAdminView):
    prefix = "/customers"
    model = Customer
    schema = CustomerSchema


@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    schema = ProductSchema
    id_type = UUID


@fr.include_view(app)
class OrderView(fr.AsyncReactAdminView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
