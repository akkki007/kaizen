import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from causaldeploy.cli import schema_documents
from causaldeploy.contracts import SCHEMA_VERSION, parse_event

FX = Path(__file__).parent / "fixtures" / "events"
FILES = sorted(FX.glob("*.json"))


def test_every_event_type_has_a_golden_fixture() -> None:
    assert {p.stem for p in FILES} == {
        "commit",
        "build",
        "deployment",
        "telemetry",
        "health",
        "verification_result",
    }


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_fixture_validates_and_roundtrips(path: Path) -> None:
    ev = parse_event(json.loads(path.read_text()))
    assert ev.type == path.stem and ev.schema_version == SCHEMA_VERSION
    assert parse_event(json.loads(ev.model_dump_json())) == ev


def test_invalid_payloads_are_rejected_with_clear_errors() -> None:
    with pytest.raises(ValidationError, match="union_tag_invalid|does not match"):
        parse_event({"type": "nope"})
    bad = json.loads((FX / "commit.json").read_text())
    del bad["sha"]
    with pytest.raises(ValidationError, match="sha"):
        parse_event(bad)
    bad = json.loads((FX / "health.json").read_text())
    bad["surprise"] = 1
    with pytest.raises(ValidationError, match="surprise"):
        parse_event(bad)


def test_published_schemas_match_models() -> None:
    """Changing a contract requires regenerating docs/schemas (and bumping SCHEMA_VERSION per
    the compatibility policy): `uv run python -m causaldeploy.cli export-schemas`."""
    for path, doc in schema_documents().items():
        assert path.exists(), f"{path} missing; run export-schemas"
        assert json.loads(path.read_text()) == json.loads(json.dumps(doc)), (
            f"{path.name} is stale; run export-schemas (and bump SCHEMA_VERSION if breaking)"
        )


def test_contract_change_requires_a_version_bump(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from causaldeploy import cli
    from causaldeploy.contracts import SCHEMA_VERSION

    schema_file = "docs/schemas/events.v1.json"
    real_run = subprocess.run

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "show"]:
            ref_path = cmd[2].split(":", 1)[1]
            if ref_path == schema_file:  # base had a different schema
                return subprocess.CompletedProcess(cmd, 0, stdout='{"changed": true}', stderr="")
            if ref_path.endswith("events.py"):
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=f'SCHEMA_VERSION = "{SCHEMA_VERSION}"', stderr=""
                )
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return real_run(cmd, **kw)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "run", fake_run)
    problems = cli.contract_version_problems("origin/main")
    assert any("SCHEMA_VERSION is still" in p for p in problems)

    def bumped(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "show"] and cmd[2].endswith("events.py"):
            return subprocess.CompletedProcess(cmd, 0, stdout='SCHEMA_VERSION = "0.9.0"', stderr="")
        return fake_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", bumped)
    assert cli.contract_version_problems("origin/main") == []
