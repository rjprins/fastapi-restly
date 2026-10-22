"""Project model belonging to an organization."""

import re
from enum import Enum
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import orm

import fastapi_restly as fr

from ..current import Current
from ..models import AuditStamped, Base, SoftDeletable, TenantOwned

if TYPE_CHECKING:
    from ..organizations.models import Organization
    from ..tasks.models import Task


class ProjectStatus(str, Enum):
    """Project status options."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class Project(
    TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDMixin, Base
):
    """
    Project belongs to an organization and contains tasks.

    ``slug`` comes from ``name`` when the client sends none, and changes
    with the name; the mapper events below set it on every write path.
    ``organization_id``, the audit stamps, and ``deleted_at`` come from the
    model mixins in ``app.models``; the view never touches them.
    ``total_story_points`` is the sum of the story points of the project's
    live tasks. The session event in ``app.tasks.models`` keeps it, on every
    write path. ``task_count`` and ``completed_task_count`` are SQL
    expressions in ``app.tasks.models``: every SELECT of a project computes
    them, and the reload after a write does too.
    """

    # The mapper events below pick a slug that no project of the organization
    # uses. Two creates at the same time can pick the same one; this
    # constraint makes the second insert fail, and Restly answers it with 409.
    __table_args__ = (sa.UniqueConstraint("organization_id", "slug"),)

    name: orm.Mapped[str]
    slug: orm.Mapped[str] = orm.mapped_column(default="")
    description: orm.Mapped[str] = orm.mapped_column(default="")
    status: orm.Mapped[ProjectStatus] = orm.mapped_column(default=ProjectStatus.ACTIVE)

    # Kept by the session event in app.tasks.models.
    total_story_points: orm.Mapped[int] = orm.mapped_column(default=0)

    if TYPE_CHECKING:
        # The number of live tasks, and of the done ones: SQL that loads with
        # the project, set in app.tasks.models after Task. Declared here so
        # that type checkers know them, and not as constructor arguments.
        task_count: orm.Mapped[int] = orm.mapped_column(init=False)
        completed_task_count: orm.Mapped[int] = orm.mapped_column(init=False)

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(
        back_populates="projects", init=False
    )
    tasks: orm.Mapped[list["Task"]] = orm.relationship(
        back_populates="project", default_factory=list, cascade="all, delete-orphan"
    )


def slugify(text: str) -> str:
    """Lowercase, hyphenated slug — adequate for the example."""
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "project"


def _unique_slug(
    connection: sa.Connection, base: str, org_id: int, exclude_id: int | None = None
) -> str:
    """The first of ``base``, ``base-2``, ``base-3``, ... that no other
    project of the organization uses."""
    candidate = base
    n = 1
    while True:
        stmt = sa.select(Project.id).where(
            Project.slug == candidate, Project.organization_id == org_id
        )
        if exclude_id is not None:
            stmt = stmt.where(Project.id != exclude_id)
        if connection.scalar(stmt) is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


@sa.event.listens_for(Project, "before_insert")
def _set_slug(mapper: Any, connection: sa.Connection, project: Project) -> None:
    """A new project gets the slug the client sent, or one from its name.

    The insert default has not stamped ``organization_id`` yet, so this
    reads the organization from ``Current``, as the insert default does.
    """
    project.slug = _unique_slug(
        connection, project.slug or slugify(project.name), Current.org_id()
    )


@sa.event.listens_for(Project, "before_update")
def _follow_name(mapper: Any, connection: sa.Connection, project: Project) -> None:
    """A new name gives a new slug, unless the slug changed as well. A blank
    slug comes from the name, as on insert."""
    attrs = sa.inspect(project).attrs
    renamed = attrs.name.history.has_changes() and not attrs.slug.history.has_changes()
    if renamed or not project.slug:
        project.slug = _unique_slug(
            connection,
            slugify(project.name),
            project.organization_id,
            exclude_id=project.id,
        )


class ProjectClauses(fr.ClauseNamespace):
    """Project visibility: not soft-deleted.

    The tenant restriction is not spelled here: the listener in
    ``app.models`` adds it to every SELECT over a ``TenantOwned`` class,
    so a view read, a ``project_id`` reference check and the trash route
    all see one organization. A cross-tenant or deleted id on a write
    reads as "does not exist" (404). Deleted rows are reachable only by a
    route that names ``is_deleted`` as its scope.
    """

    model = Project

    is_deleted = fr.where_clause(Project.deleted_at.is_not(None))
    default_scope = fr.none_of(is_deleted)
