import argparse
from pathlib import Path

from causaldeploy.evalharness.report import render
from causaldeploy.evalharness.runner import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="causaldeploy.evalharness")
    ap.add_argument("--out", default=str(Path("../eval/reports/latest.md")))
    args = ap.parse_args()
    text = render(run())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
