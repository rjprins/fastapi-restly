"""Create shop tables

Revision ID: e6c00f167d74
Revises:
Create Date: 2025-02-11 20:10:44.289642

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6c00f167d74"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "product",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "order",
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customer.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "product_order",
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["order.id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["product.id"],
        ),
    )


def downgrade() -> None:
    op.drop_table("product_order")
    op.drop_table("order")
    op.drop_table("product")
    op.drop_table("customer")
