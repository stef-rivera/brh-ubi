"""Local US-sign boxes from the LISA YOLOv11 model. Gemini reads one crop per sign."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from backend.config import ROOT, settings
from backend.detector import record_detection
from backend.gemini_client import read_crop
from backend.video import VideoSource


def map_class(name: str) -> tuple[str, str]:
    """Turn a LISA class name into a catalog id and the words on the sign."""
    if name == "stop":
        return "stop", "STOP"
    if name == "yield":
        return "yield", "YIELD"
    if name == "doNotEnter":
        return "do_not_enter", "DO NOT ENTER"
    if name == "pedestrianCrossing":
        return "pedestrian_crossing", "PEDESTRIAN CROSSING"
    if name.startswith("speedLimit"):
        digits = "".join(char for char in name if char.isdigit())
        text = f"SPEED LIMIT {digits}".strip()
        return "speed_limit", text
    return "unknown", name


class YoloDetector:
    def __init__(self, video: VideoSource):
        self.video = video
        self._running = False
        self._thread: threading.Thread | None = None
        self._model = None
        self._seen_tracks: set[str] = set()
        self._seen_signs: dict[str, float] = {}
        self._last_ts = None

    def start(self) -> None:
        if self._running:
            return
        weights = Path(settings.yolo_weights)
        if not weights.is_absolute():
            weights = ROOT / weights
        if not weights.exists():
            raise RuntimeError(
                f"Missing YOLO weights at {weights}. Download the LISA model to that path."
            )
        from ultralytics import YOLO

        self._model = YOLO(str(weights))
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="yolo", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None

    def _loop(self) -> None:
        while self._running:
            item = self.video.latest()
            if item is None:
                time.sleep(0.05)
                continue
            frame, ts = item
            if ts == self._last_ts:
                time.sleep(0.02)
                continue
            self._last_ts = ts
            self._handle(frame, ts)

    def _handle(self, frame, ts_video: float) -> None:
        model = self._model
        if model is None:
            return
        result = model.track(frame, persist=True, conf=0.4, verbose=False, imgsz=640)[0]
        boxes = result.boxes
        if boxes is None:
            return
        height, width = frame.shape[:2]
        now = time.time()
        for box in boxes:
            x1, y1, x2, y2 = [int(round(value)) for value in box.xyxy[0].tolist()]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)
            if min(x2 - x1, y2 - y1) < settings.yolo_min_box_px:
                continue
            name = result.names[int(box.cls[0])]
            sign_id, sign_text = map_class(name)
            track_id = int(box.id[0]) if box.id is not None else None
            key = str(track_id) if track_id is not None else f"{sign_id}:{x1 // 40}:{y1 // 40}"
            if key in self._seen_tracks:
                continue
            self._seen_tracks.add(key)
            last = self._seen_signs.get(sign_id)
            if last is not None and now - last < settings.dedup_window_s:
                continue
            self._seen_signs[sign_id] = now
            pad = 8
            crop = frame[max(0, y1 - pad) : min(height, y2 + pad), max(0, x1 - pad) : min(width, x2 + pad)]
            reading = read_crop(crop)
            if reading and reading["sign_id"] != "unknown":
                sign_id = reading["sign_id"]
                if reading["sign_text"]:
                    sign_text = reading["sign_text"]
            elif reading and reading["sign_text"]:
                sign_text = reading["sign_text"]
            record_detection(
                frame,
                ts_video,
                {
                    "sign_id": sign_id,
                    "sign_text": sign_text,
                    "box": [x1, y1, x2, y2],
                    "confidence": float(box.conf[0]),
                },
                "yolo",
            )
