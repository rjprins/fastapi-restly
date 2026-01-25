"""Project model belonging to an organization."""

from enum import Enum

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr


class ProjectStatus(str, Enum):
    """Project status options."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class Project(fr.IDStampsBase):
    """
    Project belongs to an organization and contains tasks.
    """

    name: orm.Mapped[str]
    description: orm.Mapped[str] = orm.mapped_column(default="")
    status: orm.Mapped[ProjectStatus] = orm.mapped_column(default=ProjectStatus.ACTIVE)

    # Foreign keys
    organization_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("organization.id"))

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="projects",
        init=False,
    )
    tasks: orm.Mapped[list["Task"]] = orm.relationship(  # noqa: F821
        back_populates="project",
        default_factory=list,
        cascade="all, delete-orphan",
    )
