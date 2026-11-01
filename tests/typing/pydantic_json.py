"""Typing fixture: a Pydantic type in a JSON column.

A Pydantic model in Mapped[...] gets a PydanticJSON column from
fr.ModelDefaults. Other types name the column type in mapped_column().
"""

from typing import Any

import pydantic
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Address(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True)

    street: str
    city: str


class Shop(fr.IDMixin, Base):
    address: Mapped[Address]
    postal: Mapped[Address | None] = mapped_column(
        fr.models.PydanticJSONB(Address, none_as_null=True), default=None
    )
    branches: Mapped[dict[str, Address]] = mapped_column(
        MutableDict.as_mutable(fr.models.PydanticJSON(dict[str, Address])),
        default_factory=dict,
    )


shop = Shop(address=Address(street="Main", city="Utrecht"))
city: str = shop.address.city
shop.address = shop.address.model_copy(update={"city": "Delft"})
shop.branches["south"] = Address(street="Dock", city="Delft")

stmt = select(Shop.id).where(Shop.address["city"].as_string() == "Delft")
column_type: fr.models.PydanticJSON = fr.models.PydanticJSON(list[Address])
document_type: Any = column_type.type_


@fr.include_view(app)
class ShopView(fr.AsyncRestView):
    prefix = "/shops"
    model = Shop
