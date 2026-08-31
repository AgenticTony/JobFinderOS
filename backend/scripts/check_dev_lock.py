#!/usr/bin/env python
"""INFRA-30 guard: the lock split cannot drift silently.

Asserts, comparing requirements.lock (production — what BOTH Docker
images install) against requirements.dev.lock (CI/dev — what the test
and lint toolchain installs):

  1. SUPERSET: every prod pin appears in the dev lock at the exact same
     version. A bump that lands only in one file is how "works in CI,
     breaks in prod" (or vice versa) starts.
  2. TOOLCHAIN IS DEV-ONLY: pytest/ruff (and pytest's private deps
     iniconfig/pluggy) must never re-enter the prod lock — they used to
     ship in both production images.
  3. TOOLCHAIN IS PRESENT in the dev lock, or CI could not lint/test.

Stdlib only, exit code 1 with a message per problem. Runs as a CI step
before install; also runnable locally: python scripts/check_dev_lock.py
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROD = BACKEND / "requirements.lock"
DEV = BACKEND / "requirements.dev.lock"

# pytest/ruff are the point of INFRA-30; iniconfig/pluggy are pytest's
# private deps (marker-evaluated reverse deps: nothing else in the lock
# requires them). If a future PROD dependency legitimately needs one of
# these, move it out of this tuple in the same commit that adds it.
DEV_ONLY = ("iniconfig", "pluggy", "pytest", "ruff")


def pins(path: Path) -> dict:
    """name -> version for every 'name==version' line (comments ignored)."""
    out = {}
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        if not sep:
            raise SystemExit(f"{path.name}: unpinned requirement {line!r} "
                             "(locks must be name==version)")
        out[name.strip().lower()] = version.strip()
    return out


def main() -> int:
    prod, dev = pins(PROD), pins(DEV)
    problems = []

    for name, version in sorted(prod.items()):
        if name not in dev:
            problems.append(
                f"prod pin missing from requirements.dev.lock: "
                f"{name}=={version}")
        elif dev[name] != version:
            problems.append(
                f"version drift for {name}: prod has {version}, "
                f"dev has {dev[name]} — bump both locks together")

    for name in DEV_ONLY:
        if name in prod:
            problems.append(
                f"{name} must not ship in requirements.lock — it is "
                f"dev/CI toolchain (INFRA-30; production images install "
                f"the prod lock)")
        if name not in dev:
            problems.append(
                f"{name} missing from requirements.dev.lock — CI could "
                f"not run the suite/lint")

    if problems:
        print("requirements lock drift detected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"lock split OK: {len(prod)} prod pins, all present in the "
          f"dev lock ({len(dev)} pins) at identical versions; "
          f"toolchain dev-only: {', '.join(DEV_ONLY)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
