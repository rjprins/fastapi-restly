"""Task model belonging to a project."""

from enum import Enum
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Integer, orm
from sqlalchemy.types import TypeDecorator

import fastapi_restly as fr

from ..current import Current
from ..models import (
    AuditStamped,
    Base,
    SoftDeletable,
    restrict_to_tenant,
    tenant_is_admin,
    tenant_org_id,
)
from ..projects.models import Project
from ..users.roles import UserRole

if TYPE_CHECKING:
    from ..labels.models import TaskLabel
    from ..users.models import User


class IntEnumType(TypeDecorator[Any]):
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
            return int(value.value)
        stored: int | None = value
        return stored

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


class Task(AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDMixin, Base):
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

    # Foreign keys
    project_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("project.id"))
    assignee_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )
    parent_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("task.id"), default=None
    )

    # Relationships
    project: orm.Mapped["Project"] = orm.relationship(
        back_populates="tasks", init=False
    )
    assignee: orm.Mapped["User | None"] = orm.relationship(
        back_populates="assigned_tasks",
        init=False,
        # Multiple FKs from Task → User now exist (assignee_id +
        # created_by_id + updated_by_id from AuditStamped); pin the
        # relationship to the assignee FK so SQLAlchemy can disambiguate.
        foreign_keys="Task.assignee_id",
    )
    parent: orm.Mapped["Task | None"] = orm.relationship(
        back_populates="subtasks", remote_side="Task.id", init=False
    )
    subtasks: orm.Mapped[list["Task"]] = orm.relationship(
        back_populates="parent", init=False, default_factory=list
    )
    # passive_deletes: the database removes the links (see TaskLabel).
    task_labels: orm.Mapped[list["TaskLabel"]] = orm.relationship(
        back_populates="task",
        init=False,
        default_factory=list,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# Task has no organization_id: its tenant is its project's.
restrict_to_tenant(
    Task,
    lambda cls: sa.or_(
        tenant_is_admin, cls.project.has(Project.organization_id == tenant_org_id)
    ),
)


@sa.event.listens_for(orm.Session, "before_flush")
def _keep_story_point_totals(session: orm.Session, flush_context: Any, _: Any) -> None:
    """Keep ``Project.total_story_points`` equal to the story points of the
    project's live tasks.

    A session event runs on every write path: the views, the bulk routes,
    the ``fr.objects`` helpers and a script. A mapper event may change only
    its own row, and the total is on another row, so it is kept here. For
    each new, changed or deleted task, the event takes what the task counted
    before the flush off its old project, and adds what it counts now to its
    new project. So a task that moves, is deleted or is restored is covered.
    """
    for task in session.new:
        if isinstance(task, Task):
            _add_points(*_counts_now(session, task))
    for task in session.dirty:
        if isinstance(task, Task):
            project, points = _counted_before(session, task)
            _add_points(project, -points)
            _add_points(*_counts_now(session, task))
    for task in session.deleted:
        if isinstance(task, Task):
            project, points = _counted_before(session, task)
            _add_points(project, -points)


def _add_points(project: Project | None, points: int) -> None:
    if project is not None and points:
        project.total_story_points += points


def _counts_now(session: orm.Session, task: Task) -> tuple[Project | None, int]:
    """The project of ``task``, and the points it adds to the total."""
    moved = sa.inspect(task).attrs.project.history.added
    if moved:
        # Set through the relationship: the flush copies the key later. None
        # is a task taken out of its project, which the flush deletes.
        project = moved[0]
    elif task.project_id is not None:
        project = session.get(Project, task.project_id)
    else:
        project = None
    points = (task.story_points or 0) if task.deleted_at is None else 0
    return project, points


def _counted_before(session: orm.Session, task: Task) -> tuple[Project | None, int]:
    """The project of ``task`` before this flush, and the points it added."""
    attrs = sa.inspect(task).attrs

    def before(key: str) -> Any:
        history = attrs[key].load_history()
        if history.deleted:
            return history.deleted[0]
        if history.added:
            # Set while not loaded, so SQLAlchemy kept no old value: read the
            # row, which holds the state of the last flush.
            query = sa.select(getattr(Task, key)).where(Task.id == task.id)
            return session.connection().scalar(query)
        return getattr(task, key)

    project_id = before("project_id")
    project = session.get(Project, project_id) if project_id is not None else None
    points = (before("story_points") or 0) if before("deleted_at") is None else 0
    return project, points


class TaskClauses(fr.ClauseNamespace):
    """Task visibility: deletion and assignment; the tenant restriction is the listener's.

    ``default_scope`` guards references to Task (``parent_id``, a label
    attach): live tasks, in the caller's organization by the listener.
    ``visible`` is the stricter read rule ``TaskView`` declares as its
    ``scope``, ``trashed`` the surface its trash route names.
    """

    model = Task

    @fr.where_clause
    @staticmethod
    def assigned_to_current_user() -> sa.ColumnElement[bool]:
        """Row-level permission: a member sees the tasks assigned to them.

        Every other role sees the organization's tasks, and an admin every
        task: the role is the conditional here, the way ``is_admin`` is
        for the tenant rule.
        """
        if Current.is_admin() or Current.role() != UserRole.MEMBER:
            return sa.true()
        return Task.assignee_id == Current.user_id()

    is_deleted = fr.where_clause(Task.deleted_at.is_not(None))

    # The read rule TaskView declares; shared here so ProjectView's
    # /{id}/tasks route can never disagree with GET /tasks.
    visible = fr.all_of(assigned_to_current_user, fr.none_of(is_deleted))
    trashed = fr.all_of(assigned_to_current_user, is_deleted)

    default_scope = fr.none_of(is_deleted)
