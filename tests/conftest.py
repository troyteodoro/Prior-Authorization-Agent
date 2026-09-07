"""Put the repo root on `sys.path` for every test.

There is no installed package (working rule 9), and pytest prepends the test
file's own directory, not the repo root. Same insertion
`scripts/check_skeleton.py` and `spike/spike_001/run.py` already make.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
