"""Typing fixture: view scopes and scoped reference markers.

Covers the ``scope`` class attribute (a ``WhereClause`` or ``fr.clauses.UNSCOPED``),
namespace clauses feeding a view scope, and a ``MustExist`` field staying a
plain scalar.
"""

from datetime import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy.orm import DeclarativeBase, Mapped
from typing_extensions import assert_type

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Ticket(fr.IDMixin, Base):
    tenant_id: Mapped[int]
    assignee_id: Mapped[int]
    deleted_at: Mapped[datetime | None]


class Current(fr.ContextNamespace):
    tenant_id: fr.ContextParam[int]


class TicketClauses(fr.ClauseNamespace):
    model = Ticket

    is_deleted = fr.where_clause(Ticket.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Ticket.tenant_id == Current.tenant_id)
    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
    trashed = fr.all_of(owned_by_tenant, is_deleted)
    default_scope = visible


class TicketSchema(fr.IDSchema):
    tenant_id: int
    assignee_id: fr.MustExist[int, Ticket]


class TicketView(fr.AsyncRestView):
    prefix = "/tickets"
    model = Ticket
    schema = TicketSchema


class TrashView(TicketView):
    scope = TicketClauses.trashed


class AdminView(TicketView):
    scope = fr.clauses.UNSCOPED  # explicit opt-out; deviating views declare


if TYPE_CHECKING:
    _payload = cast(TicketSchema, None)
    assert_type(_payload.assignee_id, int)
