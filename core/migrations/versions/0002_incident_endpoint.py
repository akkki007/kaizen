"""incidents.endpoint: one open incident per service + environment + endpoint

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("endpoint", sa.String(length=255), server_default="-", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("incidents", "endpoint")
