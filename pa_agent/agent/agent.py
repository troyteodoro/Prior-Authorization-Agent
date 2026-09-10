"""The ADK app entry point — `adk web` and `adk run` discover `root_agent` here.

*Was the `adk create` template until T-62 (D62).* It is now the real extraction
agent: the same `LlmAgent` `AdkExtractionRunner` drives, so the dev UI exercises
the thing the system actually runs rather than a generic assistant that shares
nothing with it but a model name.

**Built with no tools and no store, deliberately.** `adk web` imports this module
at startup, and constructing a `LocalPatientStore` here would put a storage adapter
behind an HTTP server that nothing in this project asked for — REQ-41 keeps store
construction in `pa_agent/cli.py`, which is the one entry point that owns a path.
So the dev-UI agent takes its note in the message, which is `tool_fetch=False`: the
configuration `spike/spike_001/run.py` measured D19 on, and the honest thing to
hand a reviewer who wants to paste a note and see what comes back.

The tool-calling variant needs an injected store and is reached through
`python -m pa_agent.cli --extraction adk --tool-fetch`.

`scripts/check_skeleton.py` asserts this module imports and exposes a reachable
`root_agent`, and that a spawned `adk web` answers 200 on `/list-apps` naming the
app. Neither needs a credential, because ADK creates its model client lazily — the
`Gemini` wrapper builds one in a `cached_property` and nothing touches it until a
request arrives.
"""

from pa_agent.agent.extraction_agent import build_extraction_agent

# D20: the model identifier is `PINNED_MODEL`, read by the builder. No literal
# here and none anywhere outside `pa_agent/model_pin.py`.
root_agent = build_extraction_agent(tool_fetch=False)
