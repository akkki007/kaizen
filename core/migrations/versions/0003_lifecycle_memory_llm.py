"""incident lifecycle + memory, investigation record, remediation reject, llm call trace,
telemetry sha (issues #88-#91)

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"

TZ = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("telemetry", sa.Column("sha", sa.String(length=64), nullable=True))

    op.add_column("incidents", sa.Column("severity", sa.String(length=16), nullable=True))
    op.add_column("incidents", sa.Column("diagnosed_at", TZ, nullable=True))
    op.add_column("incidents", sa.Column("acknowledged_by", sa.String(length=255), nullable=True))
    op.add_column("incidents", sa.Column("confirmed_root_cause", sa.String(length=64), nullable=True))
    op.add_column("incidents", sa.Column("root_cause_status", sa.String(length=16), nullable=True))
    op.add_column("incidents", sa.Column("memory", JSONB, nullable=True))

    op.create_table(
        "incident_transitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_incident_transitions_incident", "incident_transitions", ["incident_id", "id"]
    )

    op.add_column("investigations", sa.Column("inputs", JSONB, nullable=True))
    op.add_column("investigations", sa.Column("scorer_version", sa.String(length=64), nullable=True))
    op.add_column("investigations", sa.Column("duration_ms", sa.Float(), nullable=True))

    op.add_column("remediation_runs", sa.Column("rejected_by", sa.String(length=255), nullable=True))
    op.add_column("remediation_runs", sa.Column("reject_reason", sa.Text(), nullable=True))

    op.add_column("llm_calls", sa.Column("run_id", sa.String(length=64), nullable=True))
    op.add_column("llm_calls", sa.Column("prompt_hash", sa.String(length=64), nullable=True))
    op.add_column("llm_calls", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("llm_calls", sa.Column("request", JSONB, nullable=True))
    op.add_column("llm_calls", sa.Column("response", JSONB, nullable=True))


def downgrade() -> None:
    for col in ("response", "request", "status", "prompt_hash", "run_id"):
        op.drop_column("llm_calls", col)
    for col in ("reject_reason", "rejected_by"):
        op.drop_column("remediation_runs", col)
    for col in ("duration_ms", "scorer_version", "inputs"):
        op.drop_column("investigations", col)
    op.drop_index("ix_incident_transitions_incident", table_name="incident_transitions")
    op.drop_table("incident_transitions")
    for col in (
        "memory",
        "root_cause_status",
        "confirmed_root_cause",
        "acknowledged_by",
        "diagnosed_at",
        "severity",
    ):
        op.drop_column("incidents", col)
    op.drop_column("telemetry", "sha")
