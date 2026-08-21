"""Typing fixture: view scopes and scoped reference markers.

Covers the ``scope`` class attribute (a ``Clause``), a ``get_scope``
override returning ``Clause | None``, the annotated ``Model.C`` access
pattern feeding a view scope, and the three ``RefExists`` states on a
schema field (defaulted, ``scope=<Clause>``, ``scope=None``) leaving the
field a plain scalar.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, ClassVar, cast

from sqlalchemy.orm import Mapped
from typing_extensions import assert_type

import fastapi_restly as fr


class Ticket(fr.IDBase):
    if TYPE_CHECKING:
        C: ClassVar[type["TicketClauses"]]

    tenant_id: Mapped[int]
    assignee_id: Mapped[int]
    deleted_at: Mapped[datetime | None]


current_tenant = fr.context_param("tenant_id", int)


class TicketClauses(fr.ClauseNamespace):
    model = Ticket

    is_deleted = fr.where_clause(Ticket.deleted_at.is_not(None))
    owned_by_tenant = fr.where_clause(Ticket.tenant_id == current_tenant)
    visible = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
    trashed = fr.all_of(owned_by_tenant, is_deleted)
    default_scope = visible


class TicketSchema(fr.IDSchema):
    tenant_id: int
    # the three RefExists states; each leaves the field the pk scalar
    assignee_id: fr.MustExist[int, Ticket]
    reviewer_id: Annotated[int, fr.RefExists(Ticket)]
    restore_id: Annotated[int, fr.RefExists(Ticket, scope=Ticket.C.trashed)]
    audit_id: Annotated[int, fr.RefExists(Ticket, scope=None)]


class TicketView(fr.AsyncRestView):
    prefix = "/tickets"
    model = Ticket
    schema = TicketSchema


class TrashView(TicketView):
    scope = Ticket.C.trashed


class AdminView(TicketView):
    def get_scope(self) -> fr.Clause | None:
        if self.request.headers.get("x-unscoped") == "1":
            return None
        return super().get_scope()


if TYPE_CHECKING:
    assert_type(TicketView().get_scope(), fr.Clause | None)
    assert_type(TrashView.scope, fr.Clause | None)
    _payload = cast(TicketSchema, None)
    assert_type(_payload.reviewer_id, int)
    assert_type(_payload.restore_id, int)
    assert_type(_payload.audit_id, int)
