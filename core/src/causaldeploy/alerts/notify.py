"""Tell the team the answer: when an incident is investigated, post who/what/where to Slack.

The message carries the graph's conclusion (culprit commit, author, the function that changed and
the function that threw) and a link to the evidence, so nobody has to open a dashboard to learn
what broke. Idempotent per incident (audit log), never fails the investigation.
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select

from causaldeploy.config import get_settings
from causaldeploy.db.models import AuditLog, IncidentRow, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.github.client import GitHubClient
from causaldeploy.investigate.scorer import RankResult

log = logging.getLogger(__name__)
SLACK_ACTION = "alert.slack"
ISSUE_ACTION = "alert.github_issue"


def render(inc: IncidentRow, result: RankResult, base_url: str) -> tuple[str, str]:
    """(headline, body) in Slack mrkdwn; also used for the GitHub issue."""
    link = f"{base_url.rstrip('/')}/incidents/{inc.id}"
    head = f":red_circle: *{inc.service}* `{inc.endpoint}` is failing in {inc.env}"
    top = result.candidates[0] if result.candidates else None
    if top is None or result.verdict in ("no_candidates", "no_code_evidence"):
        why = (
            "No recent deployment is linked to this incident."
            if top is None
            else f"{len(result.candidates)} commit(s) shipped recently but none touches the code "
            "on the failing path, so Kaizen is not naming a culprit."
        )
        return head, f"{why}\n<{link}|Open the investigation>"
    pair = top.evidence[0] if top.evidence else None
    lines = [
        f"*Likely cause:* commit `{top.sha[:8]}` _{top.message}_"
        + (f" by *{top.author}*" if top.author else ""),
        f"*Confidence:* {round(top.confidence * 100)}%"
        + ("" if result.verdict == "confident" else " (ambiguous, review the evidence)"),
    ]
    if pair:
        lines.append(
            f"*Why:* it changed `{pair.changed_function.split(':')[-1]}` and the error is "
            f"thrown in `{pair.thrown_function.split(':')[-1]}`"
        )
    lines.append(f"<{link}|Open the evidence graph and the revert PR>")
    return head, "\n".join(lines)


async def _already_sent(incident_id: str, action: str) -> bool:
    async with session_scope() as s:
        row = (
            await s.execute(
                select(AuditLog.id).where(AuditLog.action == action, AuditLog.target == incident_id)
            )
        ).first()
    return row is not None


async def _record(incident_id: str, action: str, detail: dict[str, Any]) -> None:
    async with session_scope() as s:
        s.add(AuditLog(actor="kaizen", action=action, target=incident_id, detail=detail))


async def send_slack(text: str, client: httpx.AsyncClient | None = None) -> bool:
    url = get_settings().slack_webhook_url.get_secret_value()
    if not url:
        return False
    own = client is None
    c = client or httpx.AsyncClient(timeout=10)
    try:
        r = await c.post(url, json={"text": text})
        r.raise_for_status()
        return True
    finally:
        if own:
            await c.aclose()


async def _open_issue(repo: str, inc: IncidentRow, head: str, body: str) -> None:
    async with session_scope() as s:
        rep = (
            await s.execute(select(Repository).where(Repository.full_name == repo))
        ).scalar_one_or_none()
    if rep is None or rep.installation_id is None:
        return
    gh = GitHubClient()
    try:
        title = f"[Kaizen] {inc.service} {inc.endpoint} failing in {inc.env}"
        text = (head + "\n\n" + body).replace("*", "**").replace(":red_circle:", "🔴")
        await gh.request(
            rep.installation_id,
            "POST",
            f"/repos/{repo}/issues",
            json={"title": title, "body": text, "labels": ["kaizen"]},
        )
    finally:
        await gh.aclose()


async def alert_incident(repo: str, incident_id: str, result: RankResult) -> list[str]:
    """Send the alerts that are configured; returns the channels that were used."""
    s = get_settings()
    sent: list[str] = []
    try:
        async with session_scope() as db:
            inc = (
                await db.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
            ).scalar_one()
        head, body = render(inc, result, s.public_base_url)
        if s.slack_webhook_url.get_secret_value() and not await _already_sent(
            incident_id, SLACK_ACTION
        ):
            if await send_slack(f"{head}\n{body}"):
                await _record(incident_id, SLACK_ACTION, {"verdict": result.verdict})
                sent.append("slack")
        if s.alert_github_issues and not await _already_sent(incident_id, ISSUE_ACTION):
            await _open_issue(repo, inc, head, body)
            await _record(incident_id, ISSUE_ACTION, {"verdict": result.verdict})
            sent.append("github_issue")
    except Exception:  # an alert problem must never fail or retry the investigation
        log.exception("alerting failed for %s", incident_id)
    return sent
