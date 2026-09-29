"""The storage ports (D25, REQ-41).

Six ports, six protocols, six adapters. This package is the only place in
`pa_agent` allowed to open a file, hold a connection, name a storage location —
or, since T-100's session adapter, **write** one; everything above it receives
contracts and never learns where they came from. That is what makes a
production database a second adapter rather than a rewrite.

`policy` is what a payer covers, `patient` is one person's chart, `knowledge`
is what a drug is known to do (T-97, D119), `session` is the system's own record
of having answered a question (T-100, D127), `payer` is who a packet may be
addressed to and `outbox` is what was sent to them (T-105, D134).

Three of the six are **corpora the repository ships**, and `verify_sources.py`
re-hashes the first three against a public re-download. `payer` is shipped and
**synthesized**, so it is in no hashed manifest: there is no upstream to fetch,
and a record asserting provenance it does not have is the failure that rule
exists to prevent. `session` and `outbox` are **output** — gitignored, pinned by
no manifest, and the two places this package writes.

That split is also why *what does empty mean* has two answers here, stated
rather than left to imitation: a shipped corpus raises on an empty read (D31,
D39), and an output root answers `[]`, because empty means nobody has created a
session or sent a packet yet (D127, D134).

**This module imports no submodule on purpose.** A convenience re-export here
would be a module that reaches every plane, and Article VI's whole claim is that
no such module exists. Import `pa_agent.stores.policy`, `.patient`,
`.knowledge`, `.session`, `.payer` or `.outbox` — never a package-level alias
that resolves to more than one.
"""
