"""Typing fixture: the clauses API stays Pyright-clean for consumers.

Covers the constructor-to-type symmetry (where_clause -> WhereClause,
ContextNamespace declaration -> ContextParam[T]), the all_of overloads
(a definite predicate absorbs a possible UNSCOPED), the apply_clauses
overloads per statement kind, namespace access by class name, and the
WhereClause call form.

A parameterized clause function inside a class body stacks the clause
decorator over ``@staticmethod``: a bare ``def`` there is checked as a
method, with its first parameter reported against the class type. At
module level no marker is needed.
"""

from datetime import datetime

from sqlalchemy import ColumnElement, Delete, Update, delete, select, update
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


class TicketClauses(fr.ClauseNamespace):
    model = Ticket

    is_deleted = fr.where_clause(Ticket.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Ticket.tenant_id == Context.tenant_id)
    in_period = in_period

    # in-body function clauses: the clause decorator over @staticmethod
    @fr.where_clause
    @staticmethod
    def since(cutoff: datetime) -> ColumnElement[bool]:
        return Ticket.created_at >= cutoff

    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))


# constructor -> type symmetry
assert_type(TicketClauses.is_deleted, fr.WhereClause)
assert_type(TicketClauses.since, fr.WhereClause)

# composites are WhereClauses, so they compose on
pure = fr.all_of(TicketClauses.owned_by_tenant, TicketClauses.is_deleted)
assert_type(pure, fr.WhereClause)
assert_type(fr.any_of(pure, TicketClauses.is_deleted), fr.WhereClause)


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

# the call form resolves to a ColumnElement usable in plain SQLAlchemy
expr = TicketClauses.owned_by_tenant()
assert_type(expr, ColumnElement[bool])
_stmt = select(Ticket).where(expr, TicketClauses.is_deleted())

# statement methods chain as normal SQLAlchemy statements
_chained = TicketClauses.visible.select(Ticket).where(Ticket.id == 1).limit(1)

# select() takes any SQLAlchemy entities; exact row typing lives on the
# apply_clauses path, which keeps the statement type select() produced
_projected = TicketClauses.visible.select(Ticket.id, Ticket.created_at)
_typed = fr.apply_clauses(select(Ticket.id, Ticket.created_at), TicketClauses.visible)
_typed = _typed.where(Ticket.tenant_id == 1).limit(1)
