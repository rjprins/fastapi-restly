"""Typing fixture: fr.ModelDefaults on a base of your own.

The models are keyword-only dataclasses, so field order is free, and the
base's own metadata and type_annotation_map stay ordinary SQLAlchemy.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import MetaData, Numeric
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})
    type_annotation_map = {Decimal: Numeric(12, 2)}


class Invoice(fr.TimestampsMixin, fr.IDMixin, Base):
    paid: Mapped[bool] = mapped_column(default=False)
    amount: Mapped[Decimal]
    due_at: Mapped[datetime]


invoice = Invoice(amount=Decimal("9.95"), due_at=datetime.now())
invoice_id: int = invoice.id
metadata: MetaData = Base.metadata
