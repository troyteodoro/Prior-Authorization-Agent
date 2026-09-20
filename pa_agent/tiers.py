"""The one place a model client is built, and the one place a tier is set (D106).

A *tier* in this project is not a credential. It is a credential **and** an
environment, and getting only the first half right produces a measurement that
is wrong in a way every gate agrees with.

`google-adk` decides whether `output_schema` and `tools` take the native path —
D62's finding, the reason the tool-calling extraction prompt differs between
tiers — by calling `is_enterprise_mode_enabled()`, which reads
`GOOGLE_GENAI_USE_ENTERPRISE` from the **process environment**. It never
consults the `genai.Client` it is handed; only request routing reads
`api_client.vertexai`. So a Vertex client alone sends requests to Vertex while
ADK still injects a `SetModelResponseTool`, and the recording that results is
stamped `vertex` and carries the AI Studio prompt. `pa_agent/agent/.env` holds
`GOOGLE_GENAI_USE_ENTERPRISE=0`, and every loader in the repo `setdefault`s it
into the process, so that is the default outcome rather than an unlucky one
(measured in D106).

The second failure is quieter. `Client(vertexai=True)` with `GOOGLE_API_KEY` in
the environment and **no project** keeps the key and targets
`aiplatform.googleapis.com` — Vertex express mode, authenticated by the free
tier's credential. Passing project and location explicitly drops the key to
application-default credentials. D5 chose Vertex because it does not train on
submitted data; a run that quietly falls back to the key defeats the choice and
still writes `"tier": "vertex"`.

Both are well-formed wrong answers, which is why neither is allowed to be
reached by omission: an unknown tier raises, a Vertex client without a project
raises, and what `client_for` returns is checked against what it was asked for
before it is handed back (D31's shape, applied to provenance).

The ADK half of the question — whether `output_schema` and `tools` take the
native path — is answered by `native_schema_enabled` in
`pa_agent/agent/extraction_agent.py`, not here: nothing under `pa_agent/` outside
`pa_agent/agent/` may import `google.adk`, even lazily, and
`tests/test_adk_agent.py` scans for it (D16's boundary).

`from google import genai` is deliberately **inside** the functions. Three
fresh-interpreter assertions require that importing the contracts, the workflow
or retrieval does not pull `google.genai` or `google.adk` onto the path
(`tests/test_error_state.py`, `tests/test_workflow.py`,
`tests/test_agentic_workflow.py`), and `pa_agent/cli.py` imports this module.
"""

from __future__ import annotations

import os
from typing import Any

from pa_agent.model_pin import MEASURED_TIER, SECOND_TIER

#: The tiers this project measures on. Membership is checked, never assumed:
#: a tier that is not one of these is a caller defect, not a new endpoint.
TIERS: tuple[str, ...] = (MEASURED_TIER, SECOND_TIER)

#: The switch ADK reads to decide `output_schema_and_tools`. The SDK also honours
#: the older `GOOGLE_GENAI_USE_VERTEXAI`, but this one wins when both are set and
#: wins **silently** — so `configure_tier_env` removes the other rather than
#: leaving two switches disagreeing (D106).
ENTERPRISE_ENV = "GOOGLE_GENAI_USE_ENTERPRISE"
LEGACY_ENV = "GOOGLE_GENAI_USE_VERTEXAI"

PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
LOCATION_ENV = "GOOGLE_CLOUD_LOCATION"
API_KEY_ENV = "GOOGLE_API_KEY"


class TierError(ValueError):
    """A tier could not be honoured as asked. Never downgraded to a default."""


class UnknownTier(TierError):
    """The tier named is not one this project measures on."""


class TierNotConfigured(TierError):
    """The environment lacks what the named tier needs."""


def _require(tier: str) -> str:
    if tier not in TIERS:
        raise UnknownTier(
            f"unknown tier {tier!r}; this project measures on {list(TIERS)} "
            f"(T-90, D106). A tier is never defaulted: a recording stamped with "
            f"a tier it was not measured on is the failure D19 names."
        )
    return tier


def configure_tier_env(tier: str) -> bool:
    """Make the process environment match `tier`, and report the native flag.

    Assignment, not `setdefault`: `pa_agent/agent/.env` carries
    `GOOGLE_GENAI_USE_ENTERPRISE=0` and every loader in the repo `setdefault`s
    it, so a value that merely defaults would lose to the file and the Vertex
    run would take the AI Studio prompt (D106).

    Returns what the environment now says, so a caller can record it rather than
    assume it. The authority on what the *model* then does is
    `native_schema_enabled`, which asks ADK.
    """
    _require(tier)
    enterprise = "1" if tier == SECOND_TIER else "0"
    os.environ[ENTERPRISE_ENV] = enterprise
    # Two switches that disagree resolve silently in favour of this one, so the
    # losing one is removed instead of left to mislead a later reader (D106).
    os.environ.pop(LEGACY_ENV, None)
    return enterprise == "1"


def client_for(tier: str) -> Any:
    """Build the `genai.Client` for `tier`, or raise. Never returns a fallback.

    Sets the environment itself rather than trusting a caller to have done it.
    The two halves are not independent: `BaseApiClient` reads
    `GOOGLE_GENAI_USE_ENTERPRISE` too, so with it left at `1` even a client
    built with `api_key=` comes back claiming `vertexai=True` — measured while
    writing this module. One call sets both halves so they cannot disagree.
    """
    _require(tier)
    configure_tier_env(tier)
    from google import genai

    if tier == MEASURED_TIER:
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise TierNotConfigured(
                f"tier {tier!r} needs {API_KEY_ENV} in the environment; "
                f"`pa_agent/agent/.env` is where it lives and it is gitignored."
            )
        client = genai.Client(api_key=key)
        if client._api_client.vertexai:  # noqa: SLF001
            raise TierNotConfigured(
                f"asked for {tier!r} and got a Vertex client; the environment "
                f"is overriding the credential (D106)."
            )
        return client

    project = os.environ.get(PROJECT_ENV)
    location = os.environ.get(LOCATION_ENV)
    missing = [
        name
        for name, value in ((PROJECT_ENV, project), (LOCATION_ENV, location))
        if not value
    ]
    if missing:
        # Not a convenience check. With the project absent the SDK keeps
        # `GOOGLE_API_KEY` and targets Vertex express mode, so the run would
        # succeed, spend money, and record `vertex` over a free-tier credential
        # (D106, fact 2). The region is required for the same reason the tier is:
        # it is part of the configuration a measurement is only valid under.
        raise TierNotConfigured(
            f"tier {tier!r} needs {' and '.join(missing)} in the environment. "
            f"Without a project the SDK falls back to Vertex express mode on "
            f"{API_KEY_ENV}, which would record {SECOND_TIER!r} over a free-tier "
            f"credential (D5, D106)."
        )

    client = genai.Client(vertexai=True, project=project, location=location)

    api = client._api_client  # noqa: SLF001 — the only reliable read-back
    if not api.vertexai or api.api_key is not None:
        raise TierNotConfigured(
            f"asked for {tier!r} and got vertexai={api.vertexai!r} with "
            f"api_key {'set' if api.api_key else 'unset'}; refusing to measure "
            f"on a client that is not what it was asked for (D106)."
        )
    return client


def tier_of(client: Any) -> str:
    """The tier `client` actually reaches, read off the client itself.

    Recordings stamp this rather than the flag they were invoked with, so a
    mis-built client cannot launder itself into the artifact's provenance.
    """
    return SECOND_TIER if getattr(client, "vertexai", False) else MEASURED_TIER
