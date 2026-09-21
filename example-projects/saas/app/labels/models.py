"""Label model and TaskLabel association."""

import sqlalchemy as sa
from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr

from ..current import Current
from ..models import TenantOwned, tenant_is_admin, tenant_org_id
from ..projects.models import Project
from ..tasks.models import Task


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
        ForeignKey("user.id"), init=False, insert_default=Current.user_id
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


@sa.event.listens_for(orm.Session, "do_orm_execute")
def _restrict_task_labels_to_tenant(state: orm.ORMExecuteState) -> None:
    if not state.is_select or state.is_column_load:
        return
    state.statement = state.statement.options(
        orm.with_loader_criteria(
            TaskLabel,
            sa.or_(
                tenant_is_admin,
                sa.and_(
                    TaskLabel.task.has(
                        Task.project.has(Project.organization_id == tenant_org_id)
                    ),
                    TaskLabel.label.has(Label.organization_id == tenant_org_id),
                ),
            ),
            include_aliases=True,
        )
    )
