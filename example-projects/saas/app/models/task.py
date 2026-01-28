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


class TaskType(str, Enum):
    """Task type for polymorphic behavior."""

    TASK = "task"
    BUG = "bug"
    FEATURE = "feature"


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
    task_type: orm.Mapped[TaskType] = orm.mapped_column(default=TaskType.TASK)

    # Type-specific fields (polymorphic)
    # Bug-specific
    severity: orm.Mapped[int | None] = orm.mapped_column(default=None)
    steps_to_reproduce: orm.Mapped[str | None] = orm.mapped_column(default=None)
    # Feature-specific
    story_points: orm.Mapped[int | None] = orm.mapped_column(default=None)
    acceptance_criteria: orm.Mapped[str | None] = orm.mapped_column(default=None)

    # Optimistic locking
    version: orm.Mapped[int] = orm.mapped_column(default=1)

    # Foreign keys
    project_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("project.id"))
    assignee_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )
    parent_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("task.id"), default=None
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
    parent: orm.Mapped["Task | None"] = orm.relationship(
        back_populates="subtasks",
        remote_side="Task.id",
        init=False,
    )
    subtasks: orm.Mapped[list["Task"]] = orm.relationship(
        back_populates="parent",
        init=False,
        default_factory=list,
    )
    task_labels: orm.Mapped[list["TaskLabel"]] = orm.relationship(  # noqa: F821
        back_populates="task",
        init=False,
        default_factory=list,
    )
