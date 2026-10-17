"""telemetry.event_key (idempotent inserts on job retry) + request_id lookup index (#88/#92)

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column("telemetry", sa.Column("event_key", sa.String(length=64), nullable=True))
    op.create_index(
        "ux_telemetry_event_key",
        "telemetry",
        ["event_key"],
        unique=True,
        postgresql_where=sa.text("event_key IS NOT NULL"),
    )
    # request_spans(): cross-service call derivation looks rows up by request id
    op.execute(
        "CREATE INDEX ix_telemetry_request_id ON telemetry (repo, (body->>'request_id')) "
        "WHERE kind = 'request'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_telemetry_request_id")
    op.drop_index("ux_telemetry_event_key", table_name="telemetry")
    op.drop_column("telemetry", "event_key")
