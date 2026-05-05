"""Project model belonging to an organization."""

from datetime import datetime
from enum import Enum

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr


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
