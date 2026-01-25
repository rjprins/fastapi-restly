"""Organization model - the tenant in multi-tenant SaaS."""

from sqlalchemy import orm

import fastapi_restly as fr


class Organization(fr.IDStampsBase):
    """
    Organization represents a tenant in the multi-tenant system.
    All users, projects, and tasks belong to an organization.
    """

    name: orm.Mapped[str]
    slug: orm.Mapped[str] = orm.mapped_column(unique=True)

    # Relationships
    users: orm.Mapped[list["User"]] = orm.relationship(  # noqa: F821
        back_populates="organization",
        default_factory=list,
        cascade="all, delete-orphan",
    )
    projects: orm.Mapped[list["Project"]] = orm.relationship(  # noqa: F821
        back_populates="organization",
        default_factory=list,
        cascade="all, delete-orphan",
    )
