"""Project model belonging to an organization."""

from enum import Enum

from sqlalchemy import orm

import fastapi_restly as fr

from ..models import AuditStamped, SoftDeletable, TenantOwned


class ProjectStatus(str, Enum):
    """Project status options."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class Project(TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDBase):
    """
    Project belongs to an organization and contains tasks.

    ``slug`` is auto-generated from ``name`` on create/update by
    ``ProjectView`` if the client doesn't supply one. ``organization_id``,
    the audit stamps, and ``deleted_at`` come from the model mixins in
    ``app.models``; the view never touches them.
    ``total_story_points`` is a denormalized roll-up maintained by
    ``TaskView`` whenever a task's points change (use-case: "update
    related object based on updated object").
    """

    name: orm.Mapped[str]
    slug: orm.Mapped[str] = orm.mapped_column(default="")
    description: orm.Mapped[str] = orm.mapped_column(default="")
    status: orm.Mapped[ProjectStatus] = orm.mapped_column(default=ProjectStatus.ACTIVE)

    # Denormalized roll-up — kept in sync by TaskView (see use-case in matrix).
    total_story_points: orm.Mapped[int] = orm.mapped_column(default=0)

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="projects", init=False
    )
    tasks: orm.Mapped[list["Task"]] = orm.relationship(  # noqa: F821
        back_populates="project", default_factory=list, cascade="all, delete-orphan"
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
