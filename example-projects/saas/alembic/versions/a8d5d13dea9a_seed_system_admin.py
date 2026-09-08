"""seed the System organization and the platform admin

Revision ID: a8d5d13dea9a
Revises: ae415c26da57
Create Date: 2026-09-08 10:00:00.000000

Every write through the API carries the acting user and organization, so the
first user cannot come through the API: nobody exists yet to act. This row is
that bootstrap. It is the one user without a creator, and the organization it
belongs to exists only to hold it. The ids are fixed so the auth layer (and the
test suite) can refer to them; the sequences are moved past them because an
explicit id does not advance a sequence.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8d5d13dea9a"
down_revision: Union[str, Sequence[str], None] = "ae415c26da57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYSTEM_ORG_ID = 1
SYSTEM_ADMIN_ID = 1

organization = sa.table(
    "organization",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("slug", sa.String),
)
user = sa.table(
    "user",
    sa.column("id", sa.Integer),
    sa.column("organization_id", sa.Integer),
    sa.column("email", sa.String),
    sa.column("name", sa.String),
    sa.column("password", sa.String),
    sa.column("role", sa.String),
    sa.column("created_by_id", sa.Integer),
    sa.column("updated_by_id", sa.Integer),
)


def upgrade() -> None:
    """Seed the System organization and its admin."""
    op.execute(
        sa.insert(organization).values(id=SYSTEM_ORG_ID, name="System", slug="system")
    )
    # A migration's tables carry no ORM defaults, so the stamps are what this
    # statement says: NULL. No user existed to create the first one.
    op.execute(
        sa.insert(user).values(
            id=SYSTEM_ADMIN_ID,
            organization_id=SYSTEM_ORG_ID,
            email="admin@system.local",
            name="Platform admin",
            password="",  # set through the change-password action, never seeded
            role="OWNER",  # the enum stores member names
            created_by_id=None,
            updated_by_id=None,
        )
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('organization', 'id'), "
        "(SELECT max(id) FROM organization))"
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('\"user\"', 'id'), "
        '(SELECT max(id) FROM "user"))'
    )


def downgrade() -> None:
    """Remove the seeded rows."""
    op.execute(sa.delete(user).where(user.c.id == SYSTEM_ADMIN_ID))
    op.execute(sa.delete(organization).where(organization.c.id == SYSTEM_ORG_ID))
