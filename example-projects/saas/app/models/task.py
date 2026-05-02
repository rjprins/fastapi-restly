"""Task model belonging to a project."""

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import ForeignKey, Integer, orm
from sqlalchemy.types import TypeDecorator

import fastapi_restly as fr


class IntEnumType(TypeDecorator):
    """Store an IntEnum as a plain INTEGER column, returning the enum on load.

    SQLAlchemy's default Enum type stores values as strings (by name), which
    breaks integer sorting. Specifying Integer directly keeps the right SQL
    type but loses the Python-side conversion back to the enum. This decorator
    combines both: INTEGER storage in the database and TaskPriority instances
    in Python.
    """

    impl = Integer
    cache_ok = True

    def __init__(self, enum_class: type[Enum], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.enum_class = enum_class

    def process_bind_param(self, value: Any, dialect: Any) -> int | None:
        if isinstance(value, self.enum_class):
            return int(value)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Enum | None:
        if value is not None:
            return self.enum_class(value)
        return value


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
        IntEnumType(TaskPriority), default=TaskPriority.MEDIUM
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

    # Soft-delete + audit columns. SoftDeleteMixin / AuditStampedMixin
    # on TaskView fill these in; the view body never touches them.
    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )

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
        # Multiple FKs from Task → User now exist (assignee_id +
        # created_by_id + updated_by_id from AuditStampedMixin); pin the
        # relationship to the assignee FK so SQLAlchemy can disambiguate.
        foreign_keys="Task.assignee_id",
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
