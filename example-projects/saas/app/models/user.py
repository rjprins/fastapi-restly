"""User model with organization membership and role."""

from datetime import datetime
from enum import Enum

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr


class UserRole(str, Enum):
    """User roles within an organization."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    HR = "hr"  # Can see salary information


class User(fr.TimestampsMixin, fr.IDBase):
    """
    User belongs to an organization and has a role.
    Users can be assigned to tasks.

    The ``password`` column stores the *hashed* digest, never plaintext.
    ``UserView.handle_create`` hashes the incoming plaintext from the
    ``password`` schema field before the row is persisted. The schema
    marks ``password`` as ``WriteOnly``, so it never appears in responses.
    """

    email: orm.Mapped[str] = orm.mapped_column(unique=True)
    name: orm.Mapped[str]

    # Foreign keys
    organization_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("organization.id"))

    # Stores the hashed password (see UserView.handle_create). The wire-format
    # field shares the name; handle_create swaps plaintext for digest before flush.
    password: orm.Mapped[str] = orm.mapped_column(default="")
    role: orm.Mapped[UserRole] = orm.mapped_column(default=UserRole.MEMBER)
    # Sensitive field - only visible to HR role
    salary: orm.Mapped[int | None] = orm.mapped_column(default=None)

    # Soft-delete + audit columns picked up by the SoftDeleteMixin and
    # AuditStampedMixin on UserView. Filling them is the mixin's job —
    # neither this model nor the view body needs to know.
    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), default=None
    )

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="users", init=False
    )
    assigned_tasks: orm.Mapped[list["Task"]] = orm.relationship(  # noqa: F821
        back_populates="assignee",
        default_factory=list,
        # Pinned to assignee_id because Task now also has created_by_id /
        # updated_by_id FKs to user.id from AuditStampedMixin's columns.
        foreign_keys="Task.assignee_id",
    )
