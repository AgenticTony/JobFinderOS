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
  4. FLOORS ARE MET: every requirements.txt floor (name>=version) is
     satisfied by the prod lock pin. CI installs the lock, so a floor-only
     bump (the Dependabot shape) went green while production kept running
     the older pin the floor claims to exclude.

Stdlib only, exit code 1 with a message per problem. Runs as a CI step
before install; also runnable locally: python scripts/check_dev_lock.py
"""

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROD = BACKEND / "requirements.lock"
DEV = BACKEND / "requirements.dev.lock"
REQUIREMENTS = BACKEND / "requirements.txt"

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


def _norm(name: str) -> str:
    """PEP 503 name normalisation (PyJWT == pyjwt, python_dotenv == python-dotenv)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _release(version: str):
    """Plain numeric release as an int tuple, or None (pre-releases, local
    versions — this stdlib check cannot order them, so callers fail closed)."""
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        return None
    parts = [int(x) for x in version.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


_FLOOR = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*>=\s*(\S+)")


def unmet_floors(requirements: Path, prod: dict) -> list:
    """Problems for every requirements.txt floor the prod lock does not meet.

    Only `name[extras]>=version` is understood; any other specifier is
    reported rather than skipped, so the check can never pass by not looking.
    """
    locked = {_norm(name): version for name, version in prod.items()}
    problems = []
    for raw in requirements.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _FLOOR.fullmatch(line)
        if not match:
            problems.append(
                f"requirements.txt: unsupported specifier {line!r} — this "
                f"check understands name>=version only")
            continue
        name, _extras, floor = match.groups()
        pin = locked.get(_norm(name))
        if pin is None:
            problems.append(f"{name} is required but not pinned in "
                            f"requirements.lock")
            continue
        want, have = _release(floor), _release(pin)
        if want is None or have is None:
            problems.append(
                f"{name}: cannot compare floor {floor} with pin {pin} "
                f"(non-numeric version)")
        elif have < want:
            problems.append(
                f"{name}: requirements.txt asks for >={floor} but "
                f"requirements.lock pins {pin} — production would not get it; "
                f"bump both locks")
    return problems


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

    problems.extend(unmet_floors(REQUIREMENTS, prod))

    if problems:
        print("requirements lock drift detected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"lock split OK: {len(prod)} prod pins, all present in the "
          f"dev lock ({len(dev)} pins) at identical versions; "
          f"toolchain dev-only: {', '.join(DEV_ONLY)}; every "
          f"requirements.txt floor is met by the prod lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
