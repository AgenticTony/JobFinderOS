"""
Cross-board dedupe keys — the same job posted on two boards arrives with
different IDs and URLs, so we normalize title + company (or location) into
a collision-resistant key. Stored on job_postings.dedupe_key and checked
at store time and match time.
"""

import hashlib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(value: str | None) -> str:
    return _NON_ALNUM.sub("", (value or "").lower())


def dedupe_key_for(title: str | None, company: str | None, location: str | None = None) -> str:
    t = _norm(title)
    c = _norm(company) or _norm(location)
    return hashlib.md5(f"{t}|{c}".encode()).hexdigest()[:16]
