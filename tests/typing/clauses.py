"""Typing fixture: the clauses API stays Pyright-clean for consumers.

Covers the constructor-to-type symmetry (where_clause -> WhereClause,
transform_clause -> TransformClause, combine -> CombinedClause,
ContextNamespace declaration -> ContextParam[T]), the all_of overloads
(pure operands narrow
to WhereClause), the apply_clauses overloads per statement kind,
namespace access by class name, and the WhereClause call form.

A parameterized clause function inside a class body stacks the clause
decorator over ``@staticmethod``: a bare ``def`` there is checked as a
method, with its first parameter reported against the class type. At
module level no marker is needed.
"""

from datetime import datetime
from typing import Annotated, Any

from sqlalchemy import ColumnElement, Delete, Select, Update, delete, select, update
from sqlalchemy.orm import Mapped
from typing_extensions import assert_type

import fastapi_restly as fr


class Ticket(fr.IDBase):
    tenant_id: Mapped[int]
    created_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]


class Context(fr.ContextNamespace):
    tenant_id: fr.ContextParam[int]


assert_type(Context.tenant_id, fr.ContextParam[int])
assert_type(Context.tenant_id(), int)


@fr.where_clause
def in_period(start: datetime, end: datetime) -> ColumnElement[bool]:
    return Ticket.created_at.between(start, end)


@fr.where_clause
def marked(tid: Annotated[int, Context.tenant_id]) -> ColumnElement[bool]:
    return Ticket.tenant_id == tid


@fr.transform_clause
def newest_first(stmt: Select[Any]) -> Select[Any]:
    return stmt.order_by(Ticket.created_at.desc())


class TicketClauses(fr.ClauseNamespace):
    model = Ticket

    is_deleted = fr.where_clause(Ticket.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Ticket.tenant_id == Context.tenant_id)
    in_period = in_period
    marked = marked
    newest_first = newest_first

    # in-body function clauses: the clause decorator over @staticmethod
    @fr.where_clause
    @staticmethod
    def since(cutoff: datetime) -> ColumnElement[bool]:
        return Ticket.created_at >= cutoff

    @fr.transform_clause
    @staticmethod
    def paged(stmt: Select[Any]) -> Select[Any]:
        return stmt.limit(10)

    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))


# constructor -> type symmetry; alias() preserves the subtype (Self)
assert_type(TicketClauses.is_deleted, fr.WhereClause)
assert_type(TicketClauses.is_deleted.alias("aliased"), fr.WhereClause)
assert_type(TicketClauses.newest_first, fr.TransformClause)
assert_type(TicketClauses.since, fr.WhereClause)
assert_type(TicketClauses.paged, fr.TransformClause)
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


def unscoped_composition(
    default: fr.WhereClause | fr.clauses.Unscoped,
    other: fr.WhereClause | fr.clauses.Unscoped,
) -> None:
    # A definite predicate in either position absorbs possible unscoping.
    assert_type(fr.all_of(default, pure), fr.WhereClause)
    assert_type(fr.all_of(pure, default), fr.WhereClause)
    assert_type(fr.all_of(default, pure, other), fr.WhereClause)
    # Other mixed positions retain a safe union, even with a known predicate.
    assert_type(fr.all_of(default, other, pure), fr.WhereClause | fr.clauses.Unscoped)
    assert_type(fr.all_of(default), fr.WhereClause | fr.clauses.Unscoped)
    assert_type(fr.all_of(default, other), fr.WhereClause | fr.clauses.Unscoped)
    assert_type(fr.all_of(fr.clauses.UNSCOPED), fr.clauses.Unscoped)
    assert_type(fr.any_of(fr.clauses.UNSCOPED), fr.clauses.Unscoped)
    assert_type(fr.any_of(default, pure), fr.WhereClause | fr.clauses.Unscoped)
    assert_type(fr.any_of(pure, default), fr.WhereClause | fr.clauses.Unscoped)
    assert_type(fr.none_of(default, other), fr.WhereClause)
    assert_type(fr.none_of(fr.clauses.UNSCOPED), fr.WhereClause)
    assert_type(fr.all_of(default, bundle), fr.Clause | fr.clauses.Unscoped)
    assert_type(fr.combine(default, newest_first), fr.CombinedClause)

    # The motivating composition remains usable wherever a predicate is needed.
    scoped = fr.all_of(default, pure)
    assert_type(scoped(), ColumnElement[bool])
    assert_type(scoped.update(Ticket), Update)
    assert_type(fr.apply_clauses(delete(Ticket), scoped), Delete)
    fr.RefExists(Ticket, scope=scoped)

    # A variable-length list may contain only UNSCOPED.
    predicates: list[fr.WhereClause | fr.clauses.Unscoped] = [default, other]
    assert_type(fr.all_of(*predicates), fr.WhereClause | fr.clauses.Unscoped)
    assert_type(fr.any_of(*predicates), fr.WhereClause | fr.clauses.Unscoped)


# apply_clauses overloads per statement kind; the Select overload keeps
# the precise statement type
listing = fr.apply_clauses(select(Ticket), TicketClauses.visible)
listing = listing.where(Ticket.tenant_id == 1).limit(1)
assert_type(fr.apply_clauses(update(Ticket), TicketClauses.owned_by_tenant), Update)
assert_type(fr.apply_clauses(delete(Ticket), TicketClauses.owned_by_tenant), Delete)

# ephemeral binds keep the per-statement-kind dispatch; assign the
# select() first: the inline nested call widens under bidirectional
# inference
_base = select(Ticket)
bound = fr.apply_clauses(_base, TicketClauses.visible, tenant_id=1)
bound = bound.limit(1)
assert_type(
    fr.apply_clauses(update(Ticket), TicketClauses.owned_by_tenant, tenant_id=1), Update
)
assert_type(
    fr.apply_clauses(delete(Ticket), TicketClauses.owned_by_tenant, tenant_id=1), Delete
)

# the call form resolves to a ColumnElement usable in plain SQLAlchemy
expr = TicketClauses.owned_by_tenant(tenant_id=1)
assert_type(expr, ColumnElement[bool])
_stmt = select(Ticket).where(expr, TicketClauses.is_deleted())

# statement methods chain as normal SQLAlchemy statements
_chained = (
    TicketClauses.visible.select(Ticket, tenant_id=1).where(Ticket.id == 1).limit(1)
)

# select() takes any SQLAlchemy entities; exact row typing lives on the
# apply_clauses path, which keeps the statement type select() produced
_projected = TicketClauses.visible.select(Ticket.id, Ticket.created_at, tenant_id=1)
_typed = fr.apply_clauses(select(Ticket.id, Ticket.created_at), TicketClauses.visible)
_typed = _typed.where(Ticket.tenant_id == 1).limit(1)
