"""Outbox pattern: durable record of side-effects to dispatch.

The shipping rule for ``send email``, ``fire webhook``, ``invalidate cache``
on create/update is **not** to make those external calls inline before the
transaction commits — if the transaction later rolls back, the email/webhook
fires for a row that doesn't exist. The outbox pattern fixes this:

1. The view writes an ``OutboxEvent`` row in the *same* session as the
   business write. Either both commit, or neither does.
2. A separate worker polls the table, dispatches the side-effect, and
   marks the row delivered.

In the SaaS example we only implement step 1 (the part that the framework
hooks need to support cleanly). The worker is out of scope.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, orm

import fastapi_restly as fr


class OutboxEvent(fr.IDStampsBase):
    """A pending side-effect, written transactionally with the source change."""

    event_type: orm.Mapped[str]
    aggregate_type: orm.Mapped[str]
    aggregate_id: orm.Mapped[int]
    payload: orm.Mapped[dict[str, Any]] = orm.mapped_column(JSON, default_factory=dict)
    delivered_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
