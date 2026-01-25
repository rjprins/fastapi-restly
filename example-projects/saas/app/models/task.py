"""Task model belonging to a project."""

from enum import Enum

from sqlalchemy import ForeignKey, Integer, orm

import fastapi_restly as fr


class TaskStatus(str, Enum):
    """Task status options."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(int, Enum):
    """Task priority levels (1 = highest, 4 = lowest)."""

    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


class Task(fr.IDStampsBase):
    """
    Task belongs to a project and can be assigned to a user.
    """

    title: orm.Mapped[str]
    description: orm.Mapped[str] = orm.mapped_column(default="")
    status: orm.Mapped[TaskStatus] = orm.mapped_column(default=TaskStatus.TODO)
    priority: orm.Mapped[TaskPriority] = orm.mapped_column(
        Integer, default=TaskPriority.MEDIUM
    )

    # Foreign keys
    project_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("project.id"))
    assignee_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )

    # Relationships
    project: orm.Mapped["Project"] = orm.relationship(  # noqa: F821
        back_populates="tasks",
        init=False,
    )
    assignee: orm.Mapped["User | None"] = orm.relationship(  # noqa: F821
        back_populates="assigned_tasks",
        init=False,
    )
