"""Organization model - the tenant in multi-tenant SaaS."""

from typing import TYPE_CHECKING

from sqlalchemy import orm

import fastapi_restly as fr

if TYPE_CHECKING:
    from ..labels.models import Label
    from ..projects.models import Project
    from ..uploads.models import Upload
    from ..users.models import User


class Organization(fr.TimestampsMixin, fr.IDBase):
    """
    Organization represents a tenant in the multi-tenant system.
    All users, projects, and tasks belong to an organization.
    """

    name: orm.Mapped[str]
    slug: orm.Mapped[str] = orm.mapped_column(unique=True)

    # Relationships
    users: orm.Mapped[list["User"]] = orm.relationship(
        back_populates="organization",
        default_factory=list,
        cascade="all, delete-orphan",
    )
    projects: orm.Mapped[list["Project"]] = orm.relationship(
        back_populates="organization",
        default_factory=list,
        cascade="all, delete-orphan",
    )
    labels: orm.Mapped[list["Label"]] = orm.relationship(
        back_populates="organization",
        default_factory=list,
        cascade="all, delete-orphan",
    )
    uploads: orm.Mapped[list["Upload"]] = orm.relationship(
        back_populates="organization",
        default_factory=list,
        cascade="all, delete-orphan",
    )
