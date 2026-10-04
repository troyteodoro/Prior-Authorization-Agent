"""Put the repo root on `sys.path` for every test.

There is no installed package (working rule 9), and pytest prepends the test
file's own directory, not the repo root. Same insertion
`scripts/check_skeleton.py` and `spike/spike_001/run.py` already make.

Also home to `AcceptAllVerifier`, T-17's test double (D78). It lives here and
not in `pa_agent/` on purpose: an accept-all verifier importable from the
package is the silent skip Article V would not survive, but a test exercising
criteria logic still needs its determinations to assemble without a recording.
`tests/test_verifier.py` is the file that tests verification itself and does
not use this class for the behavior under test.

And home to the guard that holds *the suite spends no model call* (T-141,
D146): no test reaches a non-loopback host, in process or in any subprocess it
starts.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

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


# --------------------------------------------------------------------------
# The suite spends no model call and reaches no network (A13, T-141, D146)
# --------------------------------------------------------------------------

#: What every subprocess the suite starts inherits. A measurement key sits in
#: `pa_agent/agent/.env` in a working checkout, and `cli._load_env` only
#: `setdefault`s from it, so a placeholder set here wins over the real key. The
#: proxies send any request a live client makes to a port nothing listens on,
#: and the credentials path names no file, so Vertex ADC cannot authenticate
#: either. Measured: a `google-genai` request under these dies with
#: `httpx.ConnectError` at the loopback proxy.
NO_SPEND_ENV = {
    "GOOGLE_API_KEY": "the-test-suite-spends-nothing",
    "GEMINI_API_KEY": "the-test-suite-spends-nothing",
    "GOOGLE_APPLICATION_CREDENTIALS": str(REPO_ROOT / "tests" / "no-such-credentials.json"),
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "HTTP_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "localhost,127.0.0.1,::1",
}

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class NetworkBlocked(RuntimeError):
    """A test reached for a non-loopback host. The suite is a gate, and no gate
    touches the network (D45)."""


def _is_loopback(host) -> bool:
    if host is None:
        return True
    host = host.decode() if isinstance(host, bytes) else str(host)
    return host in _LOOPBACK or host.startswith("127.")


@pytest.fixture(autouse=True, scope="session")
def _the_suite_spends_nothing():
    """Two layers, because many tests run the CLI as a subprocess and an
    in-process patch does not reach a child.

    In process, a non-loopback `connect` or name lookup raises before a packet
    leaves. For every child, the environment above makes a live client fail
    locally, on a placeholder key and a refused proxy.
    """
    saved_env = {key: os.environ.get(key) for key in NO_SPEND_ENV}
    os.environ.update(NO_SPEND_ENV)

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            raise NetworkBlocked(f"the suite tried to connect to {address!r}")
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            raise NetworkBlocked(f"the suite tried to connect to {address!r}")
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            raise NetworkBlocked(f"the suite tried to resolve {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
        socket.getaddrinfo = real_getaddrinfo
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
