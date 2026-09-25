"""The storage ports (D25, REQ-41).

Four ports, four protocols, four adapters. This package is the only place in
`pa_agent` allowed to open a file, hold a connection, name a storage location —
or, since T-100's session adapter, **write** one; everything above it receives
contracts and never learns where they came from. That is what makes a
production database a second adapter rather than a rewrite.

`policy` is what a payer covers, `patient` is one person's chart, `knowledge`
is what a drug is known to do (T-97, D119), and `session` is the system's own
record of having answered a question (T-100, D127). The first three are corpora
the repository ships and a gate re-hashes; the fourth is output, gitignored, and
pinned by no manifest.

**This module imports no submodule on purpose.** A convenience re-export here
would be a module that reaches every plane, and Article VI's whole claim is that
no such module exists. Import `pa_agent.stores.policy`, `.patient`,
`.knowledge` or `.session` — never a package-level alias that resolves to more
than one.
"""
