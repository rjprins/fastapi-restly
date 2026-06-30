"""``_model_id_type`` resolves a PEP 563 (``from __future__ import annotations``)
model's stringized ``id`` annotation via the SA mapper, so ``IDSchema``/``IDRef``
pk-type coercion still works for non-int (e.g. UUID) primary keys when the model's
module stringizes its annotations.

Kept in its own ``from __future__ import annotations`` module: it needs the string
annotation, and unlike the view-level tests it builds no Pydantic schema or
forward-ref, so it is safe under the min-versions Pydantic floor.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr
from fastapi_restly.schemas._base import _model_id_type


def test_model_id_type_resolves_pep563_string_annotation():
    class UPk(fr.DataclassBase):
        __tablename__ = "pep563_pk_type_upk"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)

    assert isinstance(UPk.__annotations__["id"], str)  # PEP 563 is in effect
    assert _model_id_type(UPk) is UUID
