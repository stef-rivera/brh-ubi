"""Append-only JSON log. Database writes can fail without stopping the demo."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.config import ROOT

LOG_PATH = ROOT / "data" / "detections_log.json"
ANSWERS_PATH = ROOT / "data" / "answers_log.json"
_lock = threading.Lock()


def _read(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(path)


def log_detection(row: dict) -> None:
    with _lock:
        rows = _read(LOG_PATH)
        rows.append(row)
        _write(LOG_PATH, rows)


def log_answer(row: dict) -> None:
    with _lock:
        rows = _read(ANSWERS_PATH)
        rows.append(row)
        _write(ANSWERS_PATH, rows)


def detections_for(drive_id: str) -> list[dict]:
    with _lock:
        return [row for row in _read(LOG_PATH) if row.get("drive_id") == drive_id]


def save_detection(row: dict) -> None:
    """Keep the final recognition/audio state for a candidate without duplicate log rows."""
    with _lock:
        rows = _read(LOG_PATH)
        event_id = row.get("event_id")
        for index, existing in enumerate(rows):
            if event_id and existing.get("event_id") == event_id and existing.get("drive_id") == row.get("drive_id"):
                rows[index] = row
                break
        else:
            rows.append(row)
        _write(LOG_PATH, rows)
