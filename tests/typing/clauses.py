"""Typing fixture: the clauses API stays Pyright-clean for consumers.

Covers the constructor-to-type symmetry (where_clause -> WhereClause,
ContextNamespace declaration -> ContextParam[T]), the all_of overloads
(a definite predicate absorbs a possible UNSCOPED), the apply_clauses
overloads per statement kind, namespace access by class name, the
WhereClause call form, and a clause placed uncalled inside a relationship
condition.

A clause function takes no parameters, which the ``where_clause``
signature states: a checker rejects one that does. Inside a class body it
stacks the clause decorator over ``@staticmethod``: a bare ``def`` there
is checked as a method and reported for its missing ``self``. At module
level no marker is needed.
"""

from datetime import datetime

from sqlalchemy import ColumnElement, Delete, ForeignKey, Update, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from typing_extensions import assert_type

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Ticket(fr.IDMixin, Base):
    tenant_id: Mapped[int]
    created_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]


class Context(fr.ContextNamespace):
    tenant_id: fr.ContextParam[int]


class Report(fr.ContextNamespace):
    """A local namespace, declared next to the clause that reads it."""

    since: fr.ContextParam[datetime]


assert_type(Context.tenant_id, fr.ContextParam[int])
assert_type(Context.tenant_id(), int)
assert_type(Report.since(), datetime)


@fr.where_clause
def created_since() -> ColumnElement[bool]:
    return Ticket.created_at >= Report.since()


class TicketClauses(fr.ClauseNamespace):
    model = Ticket

    is_deleted = fr.where_clause(Ticket.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Ticket.tenant_id == Context.tenant_id)
    created_since = created_since

    # in-body function clauses: the clause decorator over @staticmethod
    @fr.where_clause
    @staticmethod
    def since() -> ColumnElement[bool]:
        return Ticket.created_at >= Report.since()

    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
    default_scope = visible


# constructor -> type symmetry
assert_type(TicketClauses.is_deleted, fr.WhereClause)
assert_type(TicketClauses.since, fr.WhereClause)
# default_scope keeps the type of the clause assigned to it
assert_type(TicketClauses.default_scope, fr.WhereClause)

# composites are WhereClauses, so they compose on
pure = fr.all_of(TicketClauses.owned_by_tenant, TicketClauses.is_deleted)
assert_type(pure, fr.WhereClause)
assert_type(fr.any_of(pure, TicketClauses.is_deleted), fr.WhereClause)


def unscoped_composition(
    default: fr.WhereClause, legacy: fr.WhereClause | fr.clauses.Unscoped
) -> None:
    # UNSCOPED is a WhereClause, so every composition with it is one
    assert_type(fr.all_of(default, pure), fr.WhereClause)
    assert_type(fr.all_of(fr.clauses.UNSCOPED), fr.WhereClause)
    assert_type(fr.any_of(fr.clauses.UNSCOPED, pure), fr.WhereClause)
    assert_type(fr.none_of(fr.clauses.UNSCOPED), fr.WhereClause)
    # an annotation that still spells out the union is accepted
    assert_type(fr.all_of(legacy, pure), fr.WhereClause)

    # The motivating composition remains usable wherever a predicate is needed.
    scoped = fr.all_of(default, pure)
    assert_type(scoped(), ColumnElement[bool])
    assert_type(fr.apply_clauses(update(Ticket), scoped), Update)
    assert_type(fr.apply_clauses(delete(Ticket), scoped), Delete)
    fr.RefExists(Ticket, scope=scoped)
    # UNSCOPED goes uncalled into a condition, as SQL TRUE
    _any_note = select(TicketNote).where(TicketNote.ticket.has(fr.clauses.UNSCOPED))


# apply_clauses overloads per statement kind; the Select overload keeps
# the precise statement type
visible_tickets = fr.apply_clauses(select(Ticket), TicketClauses.visible)
visible_tickets = visible_tickets.where(Ticket.tenant_id == 1).limit(1)
assert_type(fr.apply_clauses(update(Ticket), TicketClauses.owned_by_tenant), Update)
assert_type(fr.apply_clauses(delete(Ticket), TicketClauses.owned_by_tenant), Delete)

# the call form resolves to a ColumnElement usable in plain SQLAlchemy
expr = TicketClauses.owned_by_tenant()
assert_type(expr, ColumnElement[bool])
_stmt = select(Ticket).where(expr, TicketClauses.is_deleted())

# apply_clauses keeps the exact statement type select() produced
_typed = fr.apply_clauses(select(Ticket.id, Ticket.created_at), TicketClauses.visible)
_typed = _typed.where(Ticket.tenant_id == 1).limit(1)


# a clause without a clause function goes uncalled into a relationship
# condition, so a child model's scope can name its parent's clause
class TicketNote(fr.IDMixin, Base):
    ticket_id: Mapped[int] = mapped_column(ForeignKey("ticket.id"))
    ticket: Mapped[Ticket] = relationship(init=False)


class TicketNoteClauses(fr.ClauseNamespace):
    model = TicketNote

    default_scope = fr.where_clause(TicketNote.ticket.has(TicketClauses.visible))


_notes = select(TicketNote).where(TicketNote.ticket.has(TicketClauses.is_deleted))
_visible_notes = select(TicketNote).where(
    TicketNote.ticket.has(TicketClauses.default_scope)
)
