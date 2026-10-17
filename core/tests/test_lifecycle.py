from datetime import UTC, datetime

import pytest

from causaldeploy.contracts.domain import IncidentStatus as S
from causaldeploy.db.models import IncidentRow, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.incidents import lifecycle


async def _incident(inc_id: str = "inc-lc1") -> str:
    now = datetime.now(UTC)
    async with session_scope() as s:
        s.add(Repository(full_name="o/r"))
        await s.flush()
        s.add(
            IncidentRow(
                id=inc_id,
                repo="o/r",
                service="payments",
                env="prod",
                status="open",
                onset_ts=now,
                detected_at=now,
            )
        )
        await s.flush()
        await lifecycle.record_opened(s, inc_id)
    return inc_id


def test_every_status_has_a_transition_entry() -> None:
    assert set(lifecycle.TRANSITIONS) == set(S)
    assert lifecycle.allowed("open", "investigating")
    assert not lifecycle.allowed("open", "resolved")  # never resolved without a verified fix
    assert not lifecycle.allowed("open", "bogus")


async def test_happy_path_is_recorded_in_order(db: None) -> None:
    inc = await _incident()
    for to in (S.INVESTIGATING, S.IDENTIFIED, S.EXPLAINED, S.REMEDIATING, S.VERIFYING, S.RESOLVED):
        await lifecycle.transition(inc, to, reason=f"to {to}")
    hist = await lifecycle.history(inc)
    assert [(h.from_status, h.to_status) for h in hist] == [
        (None, "open"),
        ("open", "investigating"),
        ("investigating", "identified"),
        ("identified", "explained"),
        ("explained", "remediating"),
        ("remediating", "verifying"),
        ("verifying", "resolved"),
    ]
    assert all(h.actor == "system" for h in hist)


async def test_invalid_transition_is_rejected_and_not_recorded(db: None) -> None:
    inc = await _incident()
    with pytest.raises(lifecycle.InvalidTransition):
        await lifecycle.transition(inc, S.RESOLVED, actor="alice")
    async with session_scope() as s:
        assert (await s.get(IncidentRow, inc)).status == "open"  # type: ignore[union-attr]
    assert len(await lifecycle.history(inc)) == 1


async def test_advance_never_overwrites_a_human_decision(db: None) -> None:
    inc = await _incident()
    await lifecycle.transition(inc, S.CLOSED, actor="alice", reason="false alarm")
    # a late investigation job must not reopen it
    moved = await lifecycle.advance(inc, S.INVESTIGATING, only_from=[S.OPEN])
    assert moved is False
    hist = await lifecycle.history(inc)
    assert hist[-1].to_status == "closed" and hist[-1].actor == "alice"
    assert hist[-1].reason == "false alarm"
