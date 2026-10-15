import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from causaldeploy.contracts import BuildEvent, DeploymentEvent, Event

DEPLOY_STATUS = {
    "success": "success",
    "failure": "failure",
    "error": "failure",
    "pending": "pending",
    "queued": "pending",
    "in_progress": "pending",
}
BUILD_STATUS = {
    "success": "success",
    "neutral": "success",
    "failure": "failure",
    "cancelled": "failure",
    "timed_out": "failure",
    "startup_failure": "failure",
}


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize(event_type: str, p: dict[str, Any]) -> list[Event]:
    """Malformed or partial payloads yield no events instead of failing the delivery."""
    try:
        return _normalize(event_type, p)
    except (KeyError, ValueError, TypeError):
        return []


def _normalize(event_type: str, p: dict[str, Any]) -> list[Event]:
    """GitHub webhook -> internal events. `push` is handled by the incremental backfill job."""
    repo = (p.get("repository") or {}).get("full_name")
    if not repo:
        return []
    if event_type == "deployment":
        d = p["deployment"]
        return [
            DeploymentEvent(
                repo=repo,
                id=str(d["id"]),
                env=d["environment"],
                sha_to=d["sha"],
                started_at=_ts(d.get("created_at")),
                status="pending",
            )
        ]
    if event_type == "deployment_status":
        d, st = p["deployment"], p["deployment_status"]
        status = DEPLOY_STATUS.get(st["state"])
        if status is None:  # inactive etc.
            return []
        return [
            DeploymentEvent(
                repo=repo,
                id=str(d["id"]),
                env=d["environment"],
                sha_to=d["sha"],
                started_at=_ts(d.get("created_at")),
                finished_at=_ts(st.get("created_at")) if status != "pending" else None,
                status=status,
            )
        ]
    if event_type == "workflow_run" and p.get("action") == "completed":
        run = p["workflow_run"]
        return [
            BuildEvent(
                repo=repo,
                id=str(run["id"]),
                sha=run["head_sha"],
                status=BUILD_STATUS.get(run.get("conclusion") or "", "failure"),
                started_at=_ts(run.get("run_started_at") or run.get("created_at")),
                finished_at=_ts(run.get("updated_at")),
            )
        ]
    return []
