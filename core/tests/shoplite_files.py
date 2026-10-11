from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "shoplite"


def load() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): p.read_text() for p in ROOT.rglob("*") if p.is_file()}
