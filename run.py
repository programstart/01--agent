from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from material_review_agent.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
