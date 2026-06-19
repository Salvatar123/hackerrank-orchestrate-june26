"""Entry point: reads dataset/claims.csv and writes output.csv to the repo root."""
from __future__ import annotations
import sys
from pathlib import Path

# Ensure the code/ directory is on the path so `agent` is importable
CODE_DIR = Path(__file__).parent
REPO_ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from agent.runner import run

if __name__ == "__main__":
    run(
        claims_csv=REPO_ROOT / "dataset" / "claims.csv",
        output_csv=REPO_ROOT / "output.csv",
        images_dir=REPO_ROOT / "dataset" / "images" / "test",
        repo_root=REPO_ROOT,
    )
