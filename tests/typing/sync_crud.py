import pydantic
from sqlalchemy.orm import Mapped

import fastapi_restly as fr


class Customer(fr.IDBase):
    name: Mapped[str]


class Order(fr.IDBase):
    item_name: Mapped[str]
    quantity: Mapped[int]
    customer_id: Mapped[int]


class CustomerSchema(fr.IDSchema[Customer]):
    name: str


class OrderSchema(fr.IDSchema[Order]):
    item_name: str
    quantity: int
    customer_id: int
    customer: CustomerSchema | None = None


class OrderInput(pydantic.BaseModel):
    item_name: str
    quantity: int
    customer_id: int


class OrderView(fr.RestView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
    schema_create = OrderInput
    schema_update = OrderInput
