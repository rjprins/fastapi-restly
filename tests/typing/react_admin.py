from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Product(fr.IDMixin, Base):
    name: Mapped[str]


class ProductSchema(fr.IDSchema[Product]):
    name: str


@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    schema = ProductSchema


@fr.include_view(app)
class SyncProductView(fr.ReactAdminView):
    prefix = "/sync-products"
    model = Product
    schema = ProductSchema
