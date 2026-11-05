"""Vercel entry point: the FastAPI app as one serverless function (see ../vercel.json)."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CD_EVAL_RESULTS_PATH", str(ROOT / "data" / "eval-latest.json"))

from causaldeploy.api.main import create_app  # noqa: E402

app = create_app()
