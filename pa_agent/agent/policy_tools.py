"""T-62 — the policy-plane tools a model may call (REQ-41, REQ-53, REQ-54; D62, D66).

Two declared tools over an injected `PolicyStore`, built the same way the patient
tools are: closures, so the store never appears in a function declaration, and one
port call per body with no path and no connection.

**Separate module from `patient_tools.py`, and nothing imports both.** That is
Article VI as a fact about the import graph rather than a promise in a docstring,
and `tests/test_adk_agent.py` asserts it. REQ-41's closing line is that a module
able to read both planes would have to hold both handles; keeping the handles in
two modules is what makes holding both a visible act.

**Honest status: these have no model consumer today.** The extraction agent is not
given them — a note extractor that can read the policy's thresholds is a threshold
leaking into the model's judgment, which is REQ-53's whole argument. They are
declared and tested here as the policy-plane surface **T-61** will hand to its
adjudicator, which is the agent that legitimately needs to read the rule it is
applying. Saying so rather than implying they are wired in.

They are read-only by construction, which is Article VII's requirement of any
model-facing policy surface: there is no setter, no write, and no way to reach the
tree's file. The model may interpret the selected policy and cannot rewrite it.

**Bounded like the patient tools (T-65, REQ-54).** A criteria list is small by
construction and a value set is not — this corpus's has two codes and a production
one has thousands, which is the same shape as the 3,780 observations D64 measured.
Both truncate rather than fault: **Amendment 1 reserves set membership to Python on
both paths**, so no verdict can turn on which codes the model saw, and criterion (b)
reads the port's full set through `PolicyStore.get_value_set` regardless (D66).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pa_agent.contracts import ToolCall
from pa_agent.stores.policy import PolicyStore

from .tool_bounds import bounded


@dataclass
class PolicyToolset:
    """The declared policy tools, plus the ordered record of what was called."""

    tools: dict[str, Callable[..., Any]] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)

    def allowlist(self, *names: str) -> list[Callable[..., Any]]:
        missing = [name for name in names if name not in self.tools]
        if missing:
            raise KeyError(
                f"no such tool(s) {missing}; declared: {sorted(self.tools)}"
            )
        return [self.tools[name] for name in names]

    @property
    def names(self) -> list[str]:
        return sorted(self.tools)


def build_policy_tools(policy_store: PolicyStore) -> PolicyToolset:
    """Declare the policy-plane tools over one injected store."""
    toolset = PolicyToolset()

    def _record(name: str, arguments: dict[str, Any], started: float, ok: bool,
                detail: str | None = None) -> None:
        toolset.calls.append(
            ToolCall(
                name=name,
                arguments_digest=ToolCall.digest(arguments),
                ok=ok,
                wall_time_ms=(time.perf_counter() - started) * 1000.0,
                detail=detail,
            )
        )

    def get_policy_context(policy_version_id: str) -> dict:
        """Read the criteria a policy version requires, and the rule combining them.

        Returns each criterion's id, label and constants, plus the boolean
        expression the overall outcome is computed from. Read-only.

        Args:
          policy_version_id: the policy version, as recorded on a determination.
        """
        started = time.perf_counter()
        arguments = {"policy_version_id": policy_version_id}
        try:
            tree = policy_store.get_tree(policy_version_id)
        except Exception as exc:
            _record("get_policy_context", arguments, started, False,
                    f"{type(exc).__name__}")
            raise
        _record("get_policy_context", arguments, started, True)
        # Bounded for the same reason the patient tools are, though a tree with
        # more criteria than the ceiling would be a policy nobody could review.
        # The uniform rule is worth more than the exemption (REQ-54).
        criteria, meta = bounded(tree.criteria)
        return {
            "policy_version_id": tree.policy_version_id,
            "title": tree.title,
            "jurisdiction": {
                "authority": tree.jurisdiction.authority,
                "contractor": tree.jurisdiction.contractor,
                "states": list(tree.jurisdiction.states),
            },
            # REQ-19's rule, reported so a model can read what it is applying —
            # and never so it can evaluate it. `pa_agent.aggregate` computes the
            # outcome from this same string, in Python (Art. II).
            "decision_expression": tree.decision_expression,
            "criteria_total": meta["total"],
            "criteria_truncated": meta["truncated"],
            "criteria": [
                {
                    "id": criterion.id,
                    "label": criterion.label,
                    "scoped_to": criterion.scoped_to,
                    "constants": {
                        # `.value` and not the `PolicyConstant`: the span, the
                        # provisional flag and the open-question number are
                        # provenance for a human reviewing the tree, not inputs to
                        # a model's reasoning.
                        name: constant.value
                        for name, constant in criterion.constants.items()
                    },
                }
                for criterion in criteria
            ],
        }

    def get_policy_value_set(value_set_id: str) -> dict:
        """Read the codes a policy value set admits.

        Args:
          value_set_id: the value set's identifier, as named by a criterion's
            constants in get_policy_context.
        """
        started = time.perf_counter()
        arguments = {"value_set_id": value_set_id}
        try:
            codes = policy_store.get_value_set(value_set_id)
        except Exception as exc:
            _record("get_policy_value_set", arguments, started, False,
                    f"{type(exc).__name__}")
            raise
        _record("get_policy_value_set", arguments, started, True)
        # Truncates rather than faults. A model cannot decide membership anyway —
        # Amendment 1 reserves it to Python — so a partial list informs and never
        # adjudicates, and criterion (b) reads the port's full set (D66).
        listed, meta = bounded(sorted(codes))
        return {"value_set_id": value_set_id, "codes": listed, **meta}

    toolset.tools = {
        "get_policy_context": get_policy_context,
        "get_policy_value_set": get_policy_value_set,
    }
    return toolset
