"""Label model and TaskLabel association."""

from datetime import datetime

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr


class Label(fr.IDStampsBase):
    """
    Labels that can be applied to tasks.
    Organization-scoped.
    """

    name: orm.Mapped[str]
    color: orm.Mapped[str] = orm.mapped_column(default="#808080")

    # Foreign keys
    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id")
    )

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="labels",
        init=False,
    )
    task_labels: orm.Mapped[list["TaskLabel"]] = orm.relationship(
        back_populates="label",
        init=False,
        default_factory=list,
    )


class TaskLabel(fr.IDStampsBase):
    """
    Association table between Task and Label with extra metadata.
    Tracks who added the label and when.
    """

    # Foreign keys
    task_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("task.id"))
    label_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("label.id"))
    added_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )

    # Relationships
    task: orm.Mapped["Task"] = orm.relationship(  # noqa: F821
        back_populates="task_labels",
        init=False,
    )
    label: orm.Mapped["Label"] = orm.relationship(
        back_populates="task_labels",
        init=False,
    )
    added_by: orm.Mapped["User | None"] = orm.relationship(  # noqa: F821
        init=False,
    )
