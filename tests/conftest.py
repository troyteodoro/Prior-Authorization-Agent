"""Put the repo root on `sys.path` for every test.

There is no installed package (working rule 9), and pytest prepends the test
file's own directory, not the repo root. Same insertion
`scripts/check_skeleton.py` and `spike/spike_001/run.py` already make.

Also home to `AcceptAllVerifier`, T-17's test double (D77). It lives here and
not in `pa_agent/` on purpose: an accept-all verifier importable from the
package is the silent skip Article V would not survive, but a test exercising
criteria logic still needs its determinations to assemble without a recording.
`tests/test_verifier.py` is the file that tests verification itself and does
not use this class for the behavior under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.verifier import VerifierAnswer  # noqa: E402


class AcceptAllVerifier:
    """Accepts every claim and counts them. For tests of *other* components."""

    name = "accept_all"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, payload: dict) -> VerifierAnswer:
        self.calls.append(payload)
        return VerifierAnswer(accept=True, reason="test double")
