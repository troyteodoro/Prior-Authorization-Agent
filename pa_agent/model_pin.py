"""The one place in tracked Python that names a model (D20).

Every other module imports `PINNED_MODEL`. `tests/test_model_pin.py` enforces
that by scanning tracked Python for model identifiers written as string
literals, and this module is the single file exempt from the scan — the module
that defines what a model identifier looks like is the module allowed to write
one.

The pin exists because a recorded measurement is only a finding while its
provenance holds. `spike/spike_001/results.json` records D19 against
`gemini-3.5-flash-lite`; before T-34, `run.py` would have re-measured on
`gemini-2.5-flash-lite` and overwritten that file, and every gate in the repo
would still have returned zero.

Changing the pin is allowed. Changing it without re-measuring is not: the test
compares the pin to the model `results.json` records, so the two move together
or the suite fails.

`PINNED_MODEL` is a model *name*, not a tier. Per D5 the same name resolves
against AI Studio during development and against Vertex for recorded evals and
any demo, and the two are not interchangeable — Vertex does not train on
submitted data and the free tier may. `MEASURED_TIER` records which one produced
the numbers currently in `results.json`.
"""

from __future__ import annotations

import re

# The model this project runs against. D19 was measured on it; see D20 for why
# the pin follows the measurement rather than the other way around.
PINNED_MODEL = "gemini-3.5-flash-lite"

# The tier D19's numbers came from (D5). A Vertex run of the same corpus is a
# new measurement, not a confirmation of that one.
MEASURED_TIER = "ai_studio"

# Families this project could plausibly call. The scan looks for a family name
# followed by a version suffix, so `gemini-2.5-flash-lite` matches and the bare
# word "Gemini" in prose does not. Adding a provider is one entry here.
_MODEL_FAMILIES = (
    "gemini",
    "gemma",
    "claude",
    "gpt",
    "llama",
    "mistral",
)

#: Matches a model identifier written out in full. Used only by the T-34 test,
#: but it lives here because this is the file the scan exempts.
MODEL_LITERAL_PATTERN = re.compile(
    r"(?i)\b(?:" + "|".join(_MODEL_FAMILIES) + r")[a-z]*-[\w.]+"
)


def find_model_literals(text: str) -> list[str]:
    """Every model identifier appearing in `text`, in order."""
    return [m.group() for m in MODEL_LITERAL_PATTERN.finditer(text)]
