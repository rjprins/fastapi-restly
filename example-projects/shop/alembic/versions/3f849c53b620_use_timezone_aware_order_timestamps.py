"""Use timezone-aware order timestamps.

Revision ID: 3f849c53b620
Revises: e65cfc57aa0b
Create Date: 2026-08-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f849c53b620"
down_revision: str | None = "e65cfc57aa0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("order") as batch_op:
        for column_name in ("created_at", "updated_at"):
            batch_op.alter_column(
                column_name,
                existing_type=sa.DateTime(),
                type_=sa.DateTime(timezone=True),
                existing_nullable=False,
                postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
            )


def downgrade() -> None:
    with op.batch_alter_table("order") as batch_op:
        for column_name in ("created_at", "updated_at"):
            batch_op.alter_column(
                column_name,
                existing_type=sa.DateTime(timezone=True),
                type_=sa.DateTime(),
                existing_nullable=False,
                postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
            )
