"""Shared schema helpers for JSON list columns (TalentHive stores JSON as Text)."""

import json
from typing import Any, List, Optional


def parse_json_list(raw: Optional[str], default: Optional[List[Any]] = None) -> List[Any]:
    """Parse a JSON text column into a list, tolerating None/bad JSON."""
    if not raw:
        return default or []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else default or []
    except (json.JSONDecodeError, TypeError):
        return default or []


def dump_json_list(value: Optional[List[Any]]) -> Optional[str]:
    """Serialize a list into JSON text for storage."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)
