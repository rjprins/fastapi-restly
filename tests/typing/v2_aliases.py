"""Typing fixture: Pydantic field aliases combined with V2 query modifiers.

Verifies that consumer code defining schemas with camelCase aliases (e.g. for
React Admin / SPA front-ends) stays Pyright-clean when paired with
`QueryModifierVersion.V2`.
"""
from datetime import datetime

import pydantic
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

app = FastAPI()


class Invoice(fr.IDBase):
    customer_name: Mapped[str]
    customer_email: Mapped[str]
    amount_cents: Mapped[int]
    issued_at: Mapped[datetime]
    is_paid: Mapped[bool]


class InvoiceSchema(fr.IDSchema[Invoice]):
    model_config = pydantic.ConfigDict(populate_by_name=True, from_attributes=True)

    customer_name: str = pydantic.Field(alias="customerName")
    customer_email: str = pydantic.Field(alias="customerEmail")
    amount_cents: int = pydantic.Field(alias="amountCents")
    issued_at: datetime = pydantic.Field(alias="issuedAt")
    is_paid: bool = pydantic.Field(alias="isPaid")


@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceSchema
    query_modifier_version = fr.QueryModifierVersion.V2
