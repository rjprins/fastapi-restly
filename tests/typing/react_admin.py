from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Product(fr.IDBase):
    name: Mapped[str]


class ProductSchema(fr.IDSchema[Product]):
    name: str


@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    schema = ProductSchema
