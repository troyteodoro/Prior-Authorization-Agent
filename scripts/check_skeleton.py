#!/usr/bin/env python3
"""T-03 exit condition: the repo skeleton stands up and ADK 2.8.0 runs on this machine.

Returns zero only if all four hold:

  1. The target layout from CLAUDE.md is present.
  2. `google-adk` imports at exactly 2.8.0.
  3. `pa_agent.agent` imports and exposes a reachable `root_agent`.
  4. A backgrounded `adk web` answers HTTP 200 on `/list-apps` and names the
     agent, before this script terminates it.

Check 4 is the one the task exists for. Checks 1 through 3 never flake and never
prove ADK works here. Per D16 the probe requests `/list-apps` rather than `/`,
because on 2.8.0 `GET /` returns 307 to `/dev-ui/` and a probe of the root fails
against a healthy server. `/list-apps` also fails when the server starts but does
not discover the agent, a state `/dev-ui/` reports as healthy.

Usage:
    python scripts/check_skeleton.py [--port N] [--timeout S] [-v]
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_ADK_VERSION = "2.8.0"

# The agent folder `adk web` is pointed at, and the app name it should report.
AGENTS_DIR = REPO_ROOT / "pa_agent"
EXPECTED_APP = "agent"

DEFAULT_PORT = 8000
DEFAULT_TIMEOUT = 60.0

REQUIRED_DIRS = [
    "pa_agent",
    "pa_agent/agent",
    "data/policies/source",
    "spike/spike_001",
    "scripts",
    "tests",
    "eval",
    "docs",
]

REQUIRED_FILES = [
    "pa_agent/__init__.py",
    "pa_agent/agent/__init__.py",
    "pa_agent/agent/agent.py",
    "requirements.txt",
    ".gitignore",
]


class CheckFailed(Exception):
    """A check did not pass. The message is the reason, written for a reader."""


def check_layout() -> str:
    missing_dirs = [d for d in REQUIRED_DIRS if not (REPO_ROOT / d).is_dir()]
    missing_files = [f for f in REQUIRED_FILES if not (REPO_ROOT / f).is_file()]

    if missing_dirs or missing_files:
        lines = []
        if missing_dirs:
            lines.append("missing directories: " + ", ".join(missing_dirs))
        if missing_files:
            lines.append("missing files: " + ", ".join(missing_files))
        raise CheckFailed("; ".join(lines))

    return f"{len(REQUIRED_DIRS)} directories, {len(REQUIRED_FILES)} files"


def check_adk_version() -> str:
    try:
        adk = importlib.import_module("google.adk")
    except ImportError as exc:
        raise CheckFailed(
            f"google-adk does not import: {exc}. "
            f"Install with `pip install -r requirements.txt` inside the venv."
        ) from exc

    version = getattr(adk, "__version__", None)
    if version is None:
        raise CheckFailed("google.adk imported but exposes no __version__")

    if version != REQUIRED_ADK_VERSION:
        raise CheckFailed(
            f"google-adk is {version}, requires exactly {REQUIRED_ADK_VERSION}. "
            f"The pin is load-bearing: the /list-apps probe below is specific to "
            f"{REQUIRED_ADK_VERSION} (D16)."
        )

    return f"google-adk {version}"


def check_agent_imports() -> str:
    try:
        pkg = importlib.import_module("pa_agent.agent")
    except ImportError as exc:
        raise CheckFailed(f"pa_agent.agent does not import: {exc}") from exc

    # `adk create` writes `__init__.py` as `from . import agent`, so root_agent
    # sits on the submodule. Accept it on either, so a later re-export in the
    # package __init__ does not break this check.
    root_agent = getattr(pkg, "root_agent", None)
    if root_agent is None:
        submodule = getattr(pkg, "agent", None)
        root_agent = getattr(submodule, "root_agent", None)

    if root_agent is None:
        raise CheckFailed(
            "pa_agent.agent imports but no root_agent is reachable on it or on "
            "pa_agent.agent.agent"
        )

    return f"pa_agent.agent -> root_agent {getattr(root_agent, 'name', '?')!r}"


def port_is_occupied(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def find_adk() -> str:
    """Prefer the `adk` next to the running interpreter, so the check tests the
    venv it was invoked with rather than whatever is first on PATH."""
    sibling = Path(sys.executable).parent / "adk"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)

    found = shutil.which("adk")
    if found:
        return found

    raise CheckFailed(
        f"no `adk` executable beside {sys.executable} or on PATH. "
        f"Run this with the project venv's python."
    )


def check_adk_web(port: int, timeout: float, verbose: bool) -> str:
    # Preflight. D16 keeps port 8000 rather than taking an ephemeral one, so a
    # stale `adk web` fails this check instead of being routed around. Detecting
    # it here makes that failure legible rather than a confused HTTP result.
    if port_is_occupied(port):
        raise CheckFailed(
            f"port {port} is already serving. A stale `adk web` is the usual "
            f"cause; this check will not reuse it, because a stale server proves "
            f"nothing about the current tree. Stop it, or pass --port."
        )

    adk = find_adk()
    log_sink = None if verbose else subprocess.DEVNULL

    proc = subprocess.Popen(
        [adk, "web", "--port", str(port), str(AGENTS_DIR)],
        cwd=REPO_ROOT,
        stdout=log_sink,
        stderr=log_sink,
    )

    url = f"http://127.0.0.1:{port}/list-apps"
    deadline = time.monotonic() + timeout
    last_error = "never responded"

    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise CheckFailed(
                    f"`adk web` exited with code {proc.returncode} before "
                    f"answering. Re-run with -v to see its output."
                )

            try:
                with urllib.request.urlopen(url, timeout=2.0) as response:
                    status = response.status
                    body = response.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, OSError) as exc:
                last_error = str(exc)
                time.sleep(0.5)
                continue

            if status != 200:
                raise CheckFailed(f"GET /list-apps returned {status}, expected 200")

            if EXPECTED_APP not in body:
                raise CheckFailed(
                    f"GET /list-apps returned 200 but does not name "
                    f"{EXPECTED_APP!r}: {body.strip()}. The server started and "
                    f"did not discover the agent."
                )

            elapsed = timeout - (deadline - time.monotonic())
            return f"200 from /list-apps in {elapsed:.1f}s, apps {body.strip()}"

        raise CheckFailed(
            f"`adk web` did not answer {url} within {timeout:.0f}s "
            f"(last error: {last_error})"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show `adk web` output"
    )
    args = parser.parse_args()

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    checks = [
        ("target layout present", check_layout),
        (f"google-adk pinned at {REQUIRED_ADK_VERSION}", check_adk_version),
        ("pa_agent.agent imports", check_agent_imports),
        (
            f"adk web answers on :{args.port}",
            lambda: check_adk_web(args.port, args.timeout, args.verbose),
        ),
    ]

    failures = 0
    for name, check in checks:
        try:
            detail = check()
        except CheckFailed as exc:
            print(f"FAIL  {name}\n      {exc}")
            failures += 1
            break  # each check presumes the ones before it
        else:
            print(f"ok    {name}  ({detail})")

    if failures:
        print("\nT-03 does not close.")
        return 1

    print("\nT-03 skeleton check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
