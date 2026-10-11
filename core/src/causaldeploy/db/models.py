"""Relational store: app state and raw telemetry. The graph stores summaries only."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class Installation(Base):
    __tablename__ = "installations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub installation id
    account: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Repository(Base):
    __tablename__ = "repositories"

    full_name: Mapped[str] = mapped_column(String(255), primary_key=True)  # owner/name
    installation_id: Mapped[int | None] = mapped_column(ForeignKey("installations.id"))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    backfilled_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(ForeignKey("repositories.full_name"))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex, never the key
    label: Mapped[str] = mapped_column(String(255), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class RawEvent(Base):
    """Every webhook delivery; PK doubles as the dedupe key (X-GitHub-Delivery)."""

    __tablename__ = "raw_events"

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (Index("ix_telemetry_window", "repo", "service", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(String(255))
    service: Mapped[str] = mapped_column(String(128))
    env: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(TZ)
    kind: Mapped[str] = mapped_column(String(16))
    endpoint: Mapped[str | None] = mapped_column(String(255))
    status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo: Mapped[str] = mapped_column(ForeignKey("repositories.full_name"))
    service: Mapped[str] = mapped_column(String(128))
    env: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    onset_ts: Mapped[datetime] = mapped_column(TZ)
    detected_at: Mapped[datetime] = mapped_column(TZ)
    resolved_at: Mapped[datetime | None] = mapped_column(TZ)


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class RemediationRun(Base):
    __tablename__ = "remediation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(128))
    target: Mapped[str] = mapped_column(String(255))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class JobRecord(Base):
    """Job status persisted so the UI can query it; idempotency_key dedupes effects."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(16))  # queued|running|succeeded|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now(), onupdate=func.now())
