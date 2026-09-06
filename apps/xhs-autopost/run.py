"""Run with: python apps/xhs-autopost/run.py --list-tools"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
FRAMEWORK_ROOT = APP_ROOT.parents[1] / "usagi-agent"
for path in (APP_ROOT, FRAMEWORK_ROOT / "src", FRAMEWORK_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from xhs_autopost.cli import main


if __name__ == "__main__":
    main()
