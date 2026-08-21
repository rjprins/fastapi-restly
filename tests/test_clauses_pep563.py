"""The marker machinery under PEP 563: every annotation is a string here."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import pytest
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fastapi_restly.clauses import apply_clauses, context_param, where_clause

if TYPE_CHECKING:
    from decimal import Decimal  # a TYPE_CHECKING-only name, string at runtime


class Base(DeclarativeBase):
    pass


class Thing(Base):
    __tablename__ = "pep563_thing"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]


module_slot = context_param("p563_tenant")


def test_module_level_marker_resolves():
    @where_clause
    def owned(tid: Annotated[int, module_slot]) -> ColumnElement[bool]:
        return Thing.tenant_id == tid

    with module_slot.bind(p563_tenant=4):
        stmt = apply_clauses(select(Thing), owned)
    assert 4 in stmt.compile().params.values()


def test_unresolvable_annotated_raises_at_declaration():
    with pytest.raises(TypeError, match="Annotated marker"):

        @where_clause
        def broken(tid: Annotated[int, no_such_slot]) -> ColumnElement[bool]:  # noqa: F821
            return Thing.tenant_id == tid


def test_type_checking_only_annotation_tolerated():
    @where_clause
    def priced(amount: Decimal) -> ColumnElement[bool]:
        return Thing.tenant_id == amount

    with priced.bind(amount=2):
        stmt = apply_clauses(select(Thing), priced)
    assert 2 in stmt.compile().params.values()
