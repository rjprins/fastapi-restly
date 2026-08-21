"""Typing fixture: the clauses API stays Pyright-clean for consumers.

Covers the constructor-to-type symmetry (where_clause -> WhereClause,
transform_clause -> TransformClause, combine -> CombinedClause,
context_param -> ContextParam), the all_of overloads (pure operands narrow
to WhereClause), the apply_clauses overloads per statement kind, the
Model.C annotation pattern, and the WhereClause call form.

Parameterized clause functions live at module level: a ``def`` inside a
class body is checked as a method, so its first parameter would be
reported against the class type.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

from sqlalchemy import ColumnElement, Delete, Select, Update, delete, select, update
from sqlalchemy.orm import Mapped
from typing_extensions import assert_type

import fastapi_restly as fr


class Ticket(fr.IDBase):
    if TYPE_CHECKING:
        C: ClassVar[type["TicketClauses"]]

    tenant_id: Mapped[int]
    created_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]


current_tenant = fr.context_param("tenant_id", int)
assert_type(current_tenant, fr.ContextParam)


@fr.where_clause
def in_period(start: datetime, end: datetime) -> ColumnElement[bool]:
    return Ticket.created_at.between(start, end)


@fr.where_clause
def marked(tid: Annotated[int, current_tenant]) -> ColumnElement[bool]:
    return Ticket.tenant_id == tid


@fr.transform_clause
def newest_first(stmt: Select[Any]) -> Select[Any]:
    return stmt.order_by(Ticket.created_at.desc())


class TicketClauses(fr.ClauseNamespace):
    model = Ticket

    is_deleted = fr.where_clause(Ticket.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Ticket.tenant_id == current_tenant)
    in_period = in_period
    marked = marked
    newest_first = newest_first

    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))


# constructor -> type symmetry
assert_type(TicketClauses.is_deleted, fr.WhereClause)
assert_type(TicketClauses.newest_first, fr.TransformClause)
assert_type(
    fr.combine(TicketClauses.newest_first, TicketClauses.is_deleted), fr.CombinedClause
)

# all_of overloads: pure where operands narrow to WhereClause, so the
# result composes on into any_of/none_of
pure = fr.all_of(TicketClauses.owned_by_tenant, TicketClauses.is_deleted)
assert_type(pure, fr.WhereClause)
assert_type(fr.any_of(pure, TicketClauses.is_deleted), fr.WhereClause)
bundle = fr.combine(TicketClauses.newest_first, TicketClauses.is_deleted)
assert_type(fr.all_of(bundle, TicketClauses.is_deleted), fr.Clause)

# apply_clauses overloads per statement kind; the Select overload keeps
# the precise statement type
listing = fr.apply_clauses(select(Ticket), Ticket.C.visible)
listing = listing.where(Ticket.tenant_id == 1).limit(1)
assert_type(fr.apply_clauses(update(Ticket), Ticket.C.owned_by_tenant), Update)
assert_type(fr.apply_clauses(delete(Ticket), Ticket.C.owned_by_tenant), Delete)

# ephemeral binds keep the per-statement-kind dispatch; assign the
# select() first: the inline nested call widens under bidirectional
# inference
_base = select(Ticket)
bound = fr.apply_clauses(_base, Ticket.C.visible, tenant_id=1)
bound = bound.limit(1)
assert_type(
    fr.apply_clauses(update(Ticket), Ticket.C.owned_by_tenant, tenant_id=1), Update
)
assert_type(
    fr.apply_clauses(delete(Ticket), Ticket.C.owned_by_tenant, tenant_id=1), Delete
)

# the call form resolves to a ColumnElement usable in plain SQLAlchemy
expr = Ticket.C.owned_by_tenant(tenant_id=1)
assert_type(expr, ColumnElement[bool])
_stmt = select(Ticket).where(expr, Ticket.C.is_deleted())

# statement methods chain as normal SQLAlchemy statements
_chained = Ticket.C.visible.select(Ticket, tenant_id=1).where(Ticket.id == 1).limit(1)

# select() takes any SQLAlchemy entities; exact row typing lives on the
# apply_clauses path, which keeps the statement type select() produced
_projected = Ticket.C.visible.select(Ticket.id, Ticket.created_at, tenant_id=1)
_typed = fr.apply_clauses(select(Ticket.id, Ticket.created_at), Ticket.C.visible)
_typed = _typed.where(Ticket.tenant_id == 1).limit(1)
