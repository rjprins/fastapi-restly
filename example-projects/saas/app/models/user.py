"""User model with organization membership and role."""

from enum import Enum

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr


class UserRole(str, Enum):
    """User roles within an organization."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    HR = "hr"  # Can see salary information


class User(fr.IDStampsBase):
    """
    User belongs to an organization and has a role.
    Users can be assigned to tasks.
    """

    email: orm.Mapped[str] = orm.mapped_column(unique=True)
    name: orm.Mapped[str]
    role: orm.Mapped[UserRole] = orm.mapped_column(default=UserRole.MEMBER)
    # Sensitive field - only visible to HR role
    salary: orm.Mapped[int | None] = orm.mapped_column(default=None)

    # Foreign keys
    organization_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("organization.id"))

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="users",
        init=False,
    )
    assigned_tasks: orm.Mapped[list["Task"]] = orm.relationship(  # noqa: F821
        back_populates="assignee",
        default_factory=list,
    )
