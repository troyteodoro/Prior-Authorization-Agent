"""T-65 — one declared ceiling on what any tool may return (REQ-46, REQ-54; D66).

D64 measured the agentic path at 73x the deterministic path's input tokens, and
traced 446x of that to a single patient: `get_patient_observations` returned all
3,780 of their observations, the payload entered the context window, and
`include_contents="default"` re-sent it on every subsequent turn. **Cost is
tool-payload size times turns, and an unbounded tool scales with the chart rather
than with the question being asked.**

The fix is a tool-contract question rather than a model question. The
deterministic path reads the same 3,780 rows through the same port and pays
nothing for them, because they never enter a context window and
`most_recent_bmi` picks one.

### Truncate, or fault

Two behaviours, and which one a tool gets is not a matter of taste:

- **Truncate** where the payload *informs the model's plan*. The model's copy of
  the observations reaches no criterion — `AgenticRetrievalPlanner.gather` re-reads
  them from the port — so dropping the oldest rows cannot change a verdict, and
  `total` plus `truncated` keep the response honest about what it left out.
- **Fault** where the payload *is the model's action space*. Every `document_id`
  the model may ask for comes out of `get_patient_notes`, so a truncated list is a
  shorter chart with a flag nobody can act on: there is no page two. REQ-46 already
  makes an exceeded bound an error rather than a partial answer.

A single document is not a collection and is exempt. The extraction instruction
says to read the note in full, and a note cut at a character count is a clinical
claim the system did not mean to make.

### Why not paging

`get_patient_observations(patient_id, offset)` bounds the payload and not the run:
the model pages until it holds the chart, and the cost comes back as turns times
payload. A soft bound on the thing that caused a 446x is not a bound (D66).

### This module holds no store and reaches no plane

It is imported by both `patient_tools.py` and `policy_tools.py`, which is only
legal because it holds neither plane's handle — Article VI is about who can read
both planes, and this reads neither. It takes rows and returns rows.
"""

from __future__ import annotations

from typing import Any, Sequence

#: The one ceiling. Not per-tool: four numbers would need four justifications and
#: they would all be the same sentence (D66).
#:
#: It is not a clinical parameter and must not be read as one. Nothing downstream
#: adjudicates on a bounded response — the criteria read the port's full result —
#: so this number trades the model's field of view against tokens and against
#: nothing else.
MAX_ROWS = 50


class ToolBudgetExceeded(RuntimeError):
    """A read whose honest answer will not fit inside `MAX_ROWS`.

    Raised only where truncating would silently shrink what the model is able to
    ask for next. Shaped like the project's other classified refusals: it names
    the tool, the true size and the ceiling, so the message is actionable by a
    human reading a failed run rather than only by the caller (D38's shape).
    """

    def __init__(self, tool: str, size: int, limit: int = MAX_ROWS) -> None:
        super().__init__(
            f"{tool} would return {size} rows against a ceiling of {limit}. "
            "Truncating here would hand the model a shorter chart than the "
            "patient has, and every document_id it can name comes from this "
            "response, so the omission is unrecoverable (REQ-46, REQ-54)."
        )
        self.tool = tool
        self.size = size
        self.limit = limit


def bounded(rows: Sequence[Any], limit: int = MAX_ROWS) -> tuple[list[Any], dict]:
    """The rows a tool may return, and the fields that declare what was dropped.

    Returns `(kept, meta)` where `meta` carries `total`, `returned` and
    `truncated`. The caller merges `meta` into its response rather than this
    module choosing the payload's key name — the tools differ in what they are
    listing and a generic `items` would make every response read the same.

    `total` is the whole point of the return value. A truncated payload that did
    not say so would be indistinguishable from a patient with a thinner chart,
    which is the failure mode this project refuses everywhere else (D31, D63).
    """
    kept = list(rows[:limit])
    return kept, {
        "total": len(rows),
        "returned": len(kept),
        "truncated": len(rows) > len(kept),
    }


def refuse_if_over(tool: str, rows: Sequence[Any], limit: int = MAX_ROWS) -> None:
    """Raise rather than truncate, for a payload the model will act on."""
    if len(rows) > limit:
        raise ToolBudgetExceeded(tool, len(rows), limit)
