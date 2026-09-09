"""T-43 — the declared environment equals the installed one, and nothing is undeclared.

Three checks, all offline:

  1. Every pin in `requirements.txt` equals the version actually installed in
     the running interpreter's environment.
  2. Every third-party import in tracked Python resolves to a distribution
     named in `requirements.txt`. The import set is parsed out of the source
     with `ast`, never kept by hand.
  3. `google-adk` is still pinned at exactly 2.8.0, the version every verified
     API fact in CLAUDE.md was established against.

Run it with the interpreter whose environment is under test:

    ./venv/bin/python scripts/check_env.py

Why the pins follow the environment rather than the other way around: every
recorded number in `docs/decisions.md` was produced by the installed set, and a
pin that disagrees with the environment that produced a measurement is not a
stricter pin, it is a false one. See D49.

Limits, stated because a green check invites trust: this compares the file to
the environment. It cannot tell you the environment is *correct* — a venv wrong
in the same way the file is wrong passes. Model provenance rests on
`PINNED_MODEL` and the recorded measurements, not on this.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys

from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO / "requirements.txt"

# The version every verified ADK fact in CLAUDE.md was established against.
# Moving it means re-reading the installed API, not editing this line. (D16)
REQUIRED_ADK_VERSION = "2.8.0"

# `google` is a namespace package shared by distributions this project pins
# separately, so `packages_distributions()` cannot disambiguate it. These are
# resolved by the dotted path the import actually names. (D49)
NAMESPACE_DISTRIBUTIONS = {
    "google.adk": "google-adk",
    "google.genai": "google-genai",
}


class CheckFailed(Exception):
    """A check that did not pass, with the reason a reader needs."""


def _canonical(name: str) -> str:
    """PEP 503 normalization, so `google_adk` and `google-adk` are one name."""
    return name.lower().replace("_", "-").replace(".", "-")


def tracked_python_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise CheckFailed(f"`git ls-files` failed: {result.stderr.strip()}")
    files = [REPO / line for line in result.stdout.split()]
    if not files:
        raise CheckFailed("`git ls-files '*.py'` matched nothing; wrong directory?")
    return files


def first_party_names(files: list[Path]) -> set[str]:
    """Module names importable from within this repo.

    Detected from the tree rather than listed, so a new local module does not
    have to be remembered here. Covers `pa_agent`, and also `conftest` and any
    sibling a test or spike imports directly.
    """
    names: set[str] = set()
    for directory in {f.parent for f in files} | {REPO}:
        for child in directory.iterdir():
            if child.suffix == ".py":
                names.add(child.stem)
            elif (child / "__init__.py").exists():
                names.add(child.name)
    return names


def imported_modules(files: list[Path]) -> dict[str, set[str]]:
    """Every module imported by tracked Python, mapped to the files importing it.

    `from google import genai` resolves to `google.genai`, not `google`: the
    namespace package is shared and the dotted path is what identifies the
    distribution.
    """
    found: dict[str, set[str]] = {}

    def record(module: str, path: Path) -> None:
        found.setdefault(module, set()).add(str(path.relative_to(REPO)))

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise CheckFailed(f"{path.relative_to(REPO)} does not parse: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    record(alias.name, path)
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import: first-party by construction
                    continue
                module = node.module or ""
                if module == "google":
                    for alias in node.names:
                        record(f"google.{alias.name}", path)
                elif module:
                    record(module, path)
    return found


def distribution_for(module: str) -> str | None:
    """The distribution providing `module`, or None if it cannot be resolved."""
    for dotted, dist in NAMESPACE_DISTRIBUTIONS.items():
        if module == dotted or module.startswith(dotted + "."):
            return dist
    top = module.split(".")[0]
    providers = metadata.packages_distributions().get(top)
    if not providers:
        return None
    if len(providers) > 1:
        # Ambiguous: a shared namespace with no entry above. Failing here is
        # the point — a dependency the checker cannot name is the case it
        # exists for.
        raise CheckFailed(
            f"import `{module}` maps to several distributions {sorted(providers)}; "
            f"add its dotted path to NAMESPACE_DISTRIBUTIONS in {Path(__file__).name}"
        )
    return providers[0]


def parse_requirements() -> dict[str, str]:
    if not REQUIREMENTS.exists():
        raise CheckFailed(f"{REQUIREMENTS.relative_to(REPO)} does not exist")
    pins: dict[str, str] = {}
    for lineno, raw in enumerate(REQUIREMENTS.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            raise CheckFailed(
                f"requirements.txt:{lineno}: `{line}` is not an exact pin. "
                "Ranges let the set drift again silently, which is the defect "
                "T-43 closes (D49)."
            )
        name, version = line.split("==", 1)
        pins[_canonical(name.strip())] = version.strip()
    if not pins:
        raise CheckFailed("requirements.txt declares nothing")
    return pins


def check_pins_match_installed(pins: dict[str, str]) -> str:
    mismatched = []
    for name, pinned in sorted(pins.items()):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            mismatched.append(f"{name}: pinned {pinned}, not installed")
            continue
        if installed != pinned:
            mismatched.append(f"{name}: pinned {pinned}, installed {installed}")
    if mismatched:
        raise CheckFailed(
            "requirements.txt disagrees with the environment:\n    "
            + "\n    ".join(mismatched)
            + f"\n  Interpreter: {sys.executable}"
        )
    return f"{len(pins)} pins equal the installed versions"


def check_every_import_declared(pins: dict[str, str]) -> str:
    files = tracked_python_files()
    local = first_party_names(files)
    modules = imported_modules(files)

    third_party: dict[str, set[str]] = {}
    for module, importers in modules.items():
        top = module.split(".")[0]
        if top in sys.stdlib_module_names or top in local:
            continue
        third_party[module] = importers

    undeclared, unresolvable = [], []
    for module, importers in sorted(third_party.items()):
        dist = distribution_for(module)
        if dist is None:
            unresolvable.append(f"{module} (imported by {', '.join(sorted(importers))})")
        elif _canonical(dist) not in pins:
            undeclared.append(
                f"{module} -> {dist} (imported by {', '.join(sorted(importers))})"
            )

    if unresolvable:
        raise CheckFailed(
            "imports that resolve to no installed distribution:\n    "
            + "\n    ".join(unresolvable)
        )
    if undeclared:
        raise CheckFailed(
            "third-party imports missing from requirements.txt:\n    "
            + "\n    ".join(undeclared)
            + "\n  A direct import arriving transitively can be moved by an "
            "unrelated upgrade, under measured code, with no gate noticing (D49)."
        )
    dists = {distribution_for(m) for m in third_party}
    return f"{len(third_party)} third-party imports resolve to {len(dists)} declared distributions"


def check_adk_pin(pins: dict[str, str]) -> str:
    pinned = pins.get("google-adk")
    if pinned is None:
        raise CheckFailed("google-adk is not pinned")
    if pinned != REQUIRED_ADK_VERSION:
        raise CheckFailed(
            f"google-adk is pinned at {pinned}, requires exactly "
            f"{REQUIRED_ADK_VERSION}. Every ADK API fact in CLAUDE.md was "
            "established by reading the installed 2.8.0 source; moving the pin "
            "means re-reading it, not editing this check."
        )
    return f"google-adk pinned at {REQUIRED_ADK_VERSION}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/check_env.py",
        description="The declared environment equals the installed one (T-43).",
    )
    parser.parse_args(argv)

    try:
        pins = parse_requirements()
    except CheckFailed as exc:
        print(f"  FAIL  requirements.txt\n        {exc}", file=sys.stderr)
        return 1

    checks = (
        ("pins equal installed versions", lambda: check_pins_match_installed(pins)),
        ("every third-party import declared", lambda: check_every_import_declared(pins)),
        ("google-adk pinned at 2.8.0", lambda: check_adk_pin(pins)),
    )

    failed = 0
    print(f"  environment: {sys.executable}")
    for name, check in checks:
        try:
            detail = check()
        except CheckFailed as exc:
            print(f"  FAIL  {name}\n        {exc}", file=sys.stderr)
            failed += 1
        else:
            print(f"  ok    {name} — {detail}")

    if failed:
        print(f"\n  {failed} check(s) failed", file=sys.stderr)
        return 1
    print("\n  environment matches the declared set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
