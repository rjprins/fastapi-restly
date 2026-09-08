"""User model with organization membership and role."""

from sqlalchemy import orm

import fastapi_restly as fr

from ..models import AuditStamped, SoftDeletable, TenantOwned
from .roles import UserRole


class User(TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDBase):
    """
    User belongs to an organization and has a role.
    Users can be assigned to tasks.

    The ``password`` column stores the *hashed* digest, never plaintext.
    ``UserView.create`` (the bare business verb) hashes the incoming plaintext
    from the ``password`` schema field before the row is persisted. The schema
    marks ``password`` as ``WriteOnly``, so it never appears in responses.
    """

    email: orm.Mapped[str] = orm.mapped_column(unique=True)
    name: orm.Mapped[str]

    # Stores the hashed password (see UserView.create). The wire-format field
    # shares the name; create swaps plaintext for digest before flush.
    password: orm.Mapped[str] = orm.mapped_column(default="")
    role: orm.Mapped[UserRole] = orm.mapped_column(default=UserRole.MEMBER)
    # Sensitive field - only visible to HR role
    salary: orm.Mapped[int | None] = orm.mapped_column(default=None)

    # Relationships
    organization: orm.Mapped["Organization"] = orm.relationship(  # noqa: F821
        back_populates="users", init=False
    )
    assigned_tasks: orm.Mapped[list["Task"]] = orm.relationship(  # noqa: F821
        back_populates="assignee",
        default_factory=list,
        # Pinned to assignee_id because Task now also has created_by_id /
        # updated_by_id FKs to user.id from AuditStamped's columns.
        foreign_keys="Task.assignee_id",
    )


class UserClauses(fr.ClauseNamespace):
    """User visibility: not soft-deleted; the tenant restriction is the listener's.

    ``default_scope`` also guards references, so a cross-tenant or deleted
    ``assignee_id`` on a task reads as "does not exist" (404).
    """

    model = User

    is_deleted = fr.where_clause(User.deleted_at.is_not(None))
    default_scope = fr.none_of(is_deleted)
