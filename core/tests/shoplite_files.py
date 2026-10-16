from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "shoplite"


def load() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text() for p in ROOT.rglob("*") if p.is_file()}
