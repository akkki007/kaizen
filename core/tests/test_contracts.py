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
