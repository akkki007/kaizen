"""initial schema

Revision ID: 0001
Revises: 
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('actor', sa.String(length=255), nullable=False),
    sa.Column('action', sa.String(length=128), nullable=False),
    sa.Column('target', sa.String(length=255), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('installations',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('account', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('jobs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_table('llm_calls',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('incident_id', sa.String(length=64), nullable=True),
    sa.Column('model', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('latency_ms', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('raw_events',
    sa.Column('delivery_id', sa.String(length=128), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('delivery_id')
    )
    op.create_table('telemetry',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('repo', sa.String(length=255), nullable=False),
    sa.Column('service', sa.String(length=128), nullable=False),
    sa.Column('env', sa.String(length=64), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('endpoint', sa.String(length=255), nullable=True),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=True),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_telemetry_window', 'telemetry', ['repo', 'service', 'ts'], unique=False)
    op.create_table('repositories',
    sa.Column('full_name', sa.String(length=255), nullable=False),
    sa.Column('installation_id', sa.BigInteger(), nullable=True),
    sa.Column('default_branch', sa.String(length=255), nullable=False),
    sa.Column('backfilled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['installation_id'], ['installations.id'], ),
    sa.PrimaryKeyConstraint('full_name')
    )
    op.create_table('api_keys',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('repo', sa.String(length=255), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['repo'], ['repositories.full_name'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash')
    )
    op.create_table('incidents',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('repo', sa.String(length=255), nullable=False),
    sa.Column('service', sa.String(length=128), nullable=False),
    sa.Column('env', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('onset_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['repo'], ['repositories.full_name'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('investigations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('incident_id', sa.String(length=64), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('remediation_runs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('incident_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('approved_by', sa.String(length=255), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('remediation_runs')
    op.drop_table('investigations')
    op.drop_table('incidents')
    op.drop_table('api_keys')
    op.drop_table('repositories')
    op.drop_index('ix_telemetry_window', table_name='telemetry')
    op.drop_table('telemetry')
    op.drop_table('raw_events')
    op.drop_table('llm_calls')
    op.drop_table('jobs')
    op.drop_table('installations')
    op.drop_table('audit_log')
