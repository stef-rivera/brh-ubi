"""In-memory demo state. One drive at a time."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class AppState:
    mode: str = "idle"
    drive_id: str = ""
    detector: str = "gemini"
    source: str = "file"
    detections: list = field(default_factory=list)
    practice_index: int = 0
    practice_items: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "mode": self.mode,
                "drive_id": self.drive_id,
                "detector": self.detector,
                "source": self.source,
                "detections": list(self.detections),
                "practice_index": self.practice_index,
                "practice_total": len(self.practice_items),
            }


state = AppState()
