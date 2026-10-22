"""a project slug is unique inside its organization

Revision ID: 2e3b6198768d
Revises: a8d5d13dea9a
Create Date: 2026-10-22 18:00:00.000000

The mapper events of Project pick a slug that no project of the organization
uses, but two creates at the same time can pick the same one. With this
constraint the second insert fails, and Restly answers it with 409. The name
is the one PostgreSQL gives the unnamed constraint of the model.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e3b6198768d"
down_revision: Union[str, Sequence[str], None] = "a8d5d13dea9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "project_organization_id_slug_key", "project", ["organization_id", "slug"]
    )


def downgrade() -> None:
    op.drop_constraint("project_organization_id_slug_key", "project", type_="unique")
