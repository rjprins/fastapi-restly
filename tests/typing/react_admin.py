from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Product(fr.IDBase):
    name: Mapped[str]


class ProductRead(fr.IDSchema[Product]):
    name: str


@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    schema = ProductRead


@fr.include_view(app)
class SyncProductView(fr.ReactAdminView):
    prefix = "/sync-products"
    model = Product
    schema = ProductRead
