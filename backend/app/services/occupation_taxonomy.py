"""Occupation-name taxonomy — validated profession codes for JobTech.

JobTech classifies EVERY ad into Arbetsförmedlingen's occupation
taxonomy, and the search API filters by concept code
(`occupation-name`). Searching by code catches ads whose TITLE never
contains the free-text query — "Systemförvaltare" surfaces for a
developer because the source classified it as such. That is the recall
win free-text queries cannot deliver.

Fabrication safety: the AI only ever suggests LABELS; this module is
the single authority that resolves a label to a real code via the
official concepts feed. Unresolved labels are dropped with a log line
— a made-up code can never reach the API.

The public concepts feed serves occupation-name (3,262 concepts) and
occupation-field (21); occupation-group is not publicly served —
names are the right precision for a user's profession anyway.
"""

import logging
import re
from typing import Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

CONCEPTS_URL = "https://taxonomy.api.jobtechdev.se/v1/taxonomy/main/concepts"

# Per-process cache: normalized label -> {"code", "label"}.
# None = not fetched yet; {} = fetched-but-empty (feed failure) —
# resolution then returns nothing and hunts fall back to free-text
# queries only, never break.
_BY_LABEL: Optional[Dict[str, Dict[str, str]]] = None


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _names() -> Dict[str, Dict[str, str]]:
    """Fetch and cache the occupation-name concepts (once per process)."""
    global _BY_LABEL
    if _BY_LABEL is not None:
        return _BY_LABEL
    table: Dict[str, Dict[str, str]] = {}
    try:
        resp = httpx.get(
            CONCEPTS_URL,
            params={"version": 1, "type": "occupation-name"},
            timeout=settings.SCRAPE_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        resp.raise_for_status()
        for c in resp.json():
            if c.get("taxonomy/deprecated"):
                continue
            label = c.get("taxonomy/preferred-label")
            code = c.get("taxonomy/id")
            if label and code:
                table[_normalize(label)] = {"code": str(code), "label": str(label)}
    except Exception as e:  # noqa: BLE001 — taxonomy outage must not break hunts
        logger.warning("[occupation-taxonomy] concepts fetch failed (%s) — "
                       "falling back to free-text queries only", e)
    _BY_LABEL = table
    logger.info("[occupation-taxonomy] %d occupation-name concepts loaded", len(table))
    return table


def _prefix_match(candidate: str, table: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Compound-label resolution: official names are often compound —
    'Systemutvecklare/Programmerare', 'Sjuksköterska, grundutbildad' —
    so a candidate that is the HEAD of exactly ONE compound label
    resolves to it. Multiple hits = ambiguous = dropped (fabrication
    safety: 'utvecklare' matches many labels and resolves to nothing)."""
    cand = _normalize(candidate)
    if not cand:
        return None
    hits = []
    for norm, entry in table.items():
        if norm == cand:
            return entry
        for sep in ("/", ",", " ("):
            if norm.startswith(cand + sep):
                hits.append(entry)
                break
    return hits[0] if len(hits) == 1 else None


def resolve_labels(labels: List[str]) -> List[Dict[str, str]]:
    """Label -> [{"code", "label"}], exact match first, then a UNIQUE
    compound-prefix match. Anything unresolved or ambiguous is dropped
    (logged) — never fabricated into codes."""
    table = _names()
    out: List[Dict[str, str]] = []
    seen = set()
    dropped = []
    for raw in labels or []:
        hit = table.get(_normalize(raw)) or _prefix_match(raw, table)
        if not hit:
            dropped.append(str(raw))
            continue
        if hit["code"] not in seen:
            seen.add(hit["code"])
            out.append(hit)
    if dropped:
        logger.info("[occupation-taxonomy] dropped %d unresolvable/ambiguous label(s): %s",
                    len(dropped), dropped[:10])
    return out


def valid_codes() -> set:
    return {v["code"] for v in _names().values()}


def labels_for_codes(codes: List[str]) -> List[Dict[str, str]]:
    """Rehydrate [{"code","label"}] for stored codes; unknown codes
    (taxonomy drift between saves) are silently dropped."""
    by_code = {v["code"]: v for v in _names().values()}
    out = []
    for c in codes or []:
        hit = by_code.get(str(c))
        if hit:
            out.append(hit)
    return out


def validate_codes(codes: List[str]) -> List[Dict[str, str]]:
    """Server-side boundary check for client-submitted codes."""
    return labels_for_codes(codes)
