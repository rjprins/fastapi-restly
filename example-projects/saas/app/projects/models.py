"""Project model belonging to an organization."""

from datetime import datetime
from enum import Enum

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr

from ..context import tenant_scope


class ProjectStatus(str, Enum):
    """Project status options."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class Project(fr.TimestampsMixin, fr.IDBase):
    """
    Project belongs to an organization and contains tasks.
    Supports soft delete via deleted_at field.

    ``slug`` is auto-generated from ``name`` on create/update by
    ``ProjectView`` if the client doesn't supply one.
    ``created_by_id`` / ``updated_by_id`` are stamped server-side from
    the request context — clients don't (and shouldn't) supply them.
    ``total_story_points`` is a denormalized roll-up maintained by
    ``TaskView`` whenever a task's points change (use-case: "update
    related object based on updated object").
    """

    name: orm.Mapped[str]
    slug: orm.Mapped[str] = orm.mapped_column(default="")
    description: orm.Mapped[str] = orm.mapped_column(default="")
    status: orm.Mapped[ProjectStatus] = orm.mapped_column(default=ProjectStatus.ACTIVE)
    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)

    # Denormalized roll-up — kept in sync by TaskView (see use-case in matrix).
    total_story_points: orm.Mapped[int] = orm.mapped_column(default=0)

    # Foreign keys
    organization_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("organization.id"))

    # Audit stamps — set by ProjectView from request.state, not by the client.
    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="projects", init=False
    )
    tasks: orm.Mapped[list["Task"]] = orm.relationship(  # noqa: F821
        back_populates="project", default_factory=list, cascade="all, delete-orphan"
    )


class ProjectClauses(fr.ClauseNamespace):
    """Project visibility: owned by the tenant and not soft-deleted.

    ``default_scope`` arms every view read and every reference to Project,
    so a cross-tenant or deleted ``project_id`` on a write reads as "does
    not exist" (404), unconditionally: no request can ask past it. The
    deleted rows stay reachable through explicit scopes built from the
    same tenant leaf: ``trashed`` on the trash view, ``owned_by_tenant``
    alone in ``ProjectView.restore``.
    """

    model = Project

    owned_by_tenant = tenant_scope(Project)
    is_deleted = fr.where_clause(Project.deleted_at.is_not(None))
    trashed = fr.all_of(owned_by_tenant, is_deleted)
    default_scope = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))
