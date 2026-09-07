"""The storage ports (D25, REQ-41).

Two planes, two protocols, two adapters, two connections. This package is the
only place in `pa_agent` allowed to open a file, hold a connection, or name a
storage location; everything above it receives contracts and never learns where
they came from. That is what makes a production database a second adapter
rather than a rewrite.

**This module imports neither submodule on purpose.** A convenience re-export
here would be a module that reaches both planes, and Article VI's whole claim is
that no such module exists. Import `pa_agent.stores.policy` or
`pa_agent.stores.patient` — never a package-level alias that resolves to both.
"""
