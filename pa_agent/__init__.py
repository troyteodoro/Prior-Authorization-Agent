"""Prior authorization determination agent for bariatric surgery under CMS NCD 100.1.

The deterministic modules live here: resolver, criteria, spans, cli. The single
model-facing component is the subpackage `pa_agent.agent`, and it is kept in its
own directory so the Article I and II boundary is a path rather than a
convention. See D16.

This module deliberately does not import `.agent`. Doing so would pull
`google.adk` and a model configuration into every import of the deterministic
code, including the tests that assert no model was called.
"""
