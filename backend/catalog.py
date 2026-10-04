"""Sign catalog. Meanings come from this file, never from the model."""

from __future__ import annotations

import json
from pathlib import Path

from backend.config import ROOT

CATALOG_PATH = ROOT / "data" / "sign_catalog.json"


def load_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text())


def get(sign_id: str) -> dict | None:
    for entry in load_catalog():
        if entry["id"] == sign_id:
            return entry
    return None


def ids() -> list[str]:
    return [entry["id"] for entry in load_catalog()]


def prompt_list() -> str:
    lines = [f"{entry['id']}: {entry['sign_text']}" for entry in load_catalog()]
    return "\n".join(lines)


def is_critical(sign_id: str) -> bool:
    entry = get(sign_id)
    return bool(entry and entry.get("safety_critical"))


def announcement(sign_id: str, value: int | None = None) -> str:
    """Describe a recognized sign without implying it applies to this lane."""
    entry = get(sign_id)
    if not entry:
        return ""
    if sign_id == "speed_limit" and value is not None:
        return f"Speed limit {value} sign detected."
    return f"{entry['sign_text'].capitalize()} sign detected."
