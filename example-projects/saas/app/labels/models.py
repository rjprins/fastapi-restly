"""Label model and TaskLabel association."""

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr

from ..context import Current
from ..models import TenantOwned


class Label(TenantOwned, fr.TimestampsMixin, fr.IDBase):
    """
    Labels that can be applied to tasks.
    Organization-scoped.
    """

    name: orm.Mapped[str]
    color: orm.Mapped[str] = orm.mapped_column(default="#808080")

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="labels", init=False
    )
    task_labels: orm.Mapped[list["TaskLabel"]] = orm.relationship(
        back_populates="label", init=False, default_factory=list
    )


class LabelClauses(fr.ClauseNamespace):
    """Label visibility: nothing beyond the tenant restriction (no soft delete).

    The namespace exists so ``label_id`` references on TaskLabel resolve
    against Label; the tenant listener in ``app.models`` scopes them.
    """

    model = Label


class TaskLabel(fr.TimestampsMixin, fr.IDBase):
    """
    Association table between Task and Label with extra metadata.
    Tracks who added the label and when.
    """

    # Foreign keys
    task_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("task.id"))
    label_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("label.id"))
    # Stamped from context like the audit columns: not a constructor argument.
    added_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), init=False, insert_default=lambda: Current.user_id()
    )

    # Relationships
    task: orm.Mapped["Task"] = orm.relationship(  # noqa: F821
        back_populates="task_labels", init=False
    )
    label: orm.Mapped["Label"] = orm.relationship(
        back_populates="task_labels", init=False
    )
    added_by: orm.Mapped["User | None"] = orm.relationship(  # noqa: F821
        init=False
    )
