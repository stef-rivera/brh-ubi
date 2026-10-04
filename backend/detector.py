"""Sample a frame every few seconds and ask Gemini what signs are in it."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2

from backend.catalog import get, is_critical
from backend.config import ROOT, settings
from backend.events import bus
from backend.gemini_client import read_frame
from backend.state import state
from backend.storage import log_detection
from backend.tts import speak
from backend.video import VideoSource


def _similar(a, b) -> bool:
    if a is None or b is None:
        return False
    small_a = cv2.resize(a, (64, 36))
    small_b = cv2.resize(b, (64, 36))
    diff = cv2.absdiff(small_a, small_b).mean()
    return diff < 4.0


def _thumb(frame, box, drive_id: str, index: int) -> str:
    height, width = frame.shape[:2]
    if box:
        x1, y1, x2, y2 = box
        pad = 12
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(width, x2 + pad)
        y2 = min(height, y2 + pad)
        crop = frame[y1:y2, x1:x2].copy()
        if box:
            cv2.rectangle(crop, (pad, pad), (crop.shape[1] - pad, crop.shape[0] - pad), (40, 40, 220), 2)
    else:
        crop = cv2.resize(frame, (320, 180))
    folder = ROOT / "data" / "thumbs" / drive_id
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{index}.jpg"
    cv2.imwrite(str(folder / name), crop)
    return f"/thumbs/{drive_id}/{name}"


class GeminiDetector:
    def __init__(self, video: VideoSource, reader=None, provider="gemini"):
        self.reader = reader or read_frame
        self.provider = provider
        self.drive_id = state.drive_id
        self._stop = threading.Event()
        self.video = video
        self._running = False
        self._thread: threading.Thread | None = None
        self._seen: dict[str, float] = {}
        self._last_small = None

    def start(self) -> None:
        if self._running:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="detector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        if thread is None or not thread.is_alive():
            self._thread = None

    def _status(self, status, message=""):
        with state.lock:
            if state.drive_id != self.drive_id:
                return
            state.detection_status = status
            state.detection_error = message
        bus.publish({"type": "detector_status", "status": status, "message": message})

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                if self.video.ended:
                    self._status("completed", "Video complete. Park to practice; Start drive runs another pass.")
                    break
                started = time.perf_counter()
                item = self.video.latest()
                if item is None:
                    self._stop.wait(0.05)
                    continue
                if item is not None:
                    frame, ts = item
                    if not _similar(self._last_small, frame):
                        self._last_small = frame
                        self._handle(frame, ts)
                interval = max(5.0, settings.detect_interval_s) if self.provider == "chatgpt" else settings.detect_interval_s
                self._stop.wait(max(0, interval - (time.perf_counter() - started)))
        except Exception as exc:
            from backend.chatgpt_client import ChatGPTError
            message = str(exc) if isinstance(exc, ChatGPTError) else "Detection failed. Park and check the server configuration before retrying."
            if not self._stop.is_set():
                self._status("error", message)
        finally:
            self._running = False

    def _handle(self, frame, ts_video: float) -> None:
        now = time.time()
        for found in self.reader(frame):
            if self._stop.is_set() or state.drive_id != self.drive_id:
                return
            sign_id = found["sign_id"]
            seen_key = sign_id if sign_id != "unknown" else "unknown:" + found["sign_text"].casefold()
            last = self._seen.get(seen_key)
            if last is not None and now - last < settings.dedup_window_s:
                continue
            self._seen[seen_key] = now
            entry = get(sign_id)
            with state.lock:
                index = len(state.detections) + 1
                drive_id = state.drive_id
            thumb_url = _thumb(frame, found["box"], drive_id, index)
            critical = is_critical(sign_id)
            row = {
                "drive_id": drive_id,
                "ts_video": round(float(ts_video), 2),
                "ts_wall": datetime.now(timezone.utc).isoformat(),
                "sign_id": sign_id,
                "sign_text": (entry or {}).get("sign_text") or found["sign_text"],
                "safety_critical": critical,
                "speak_now": critical,
                "box": found["box"],
                "confidence": found["confidence"],
                "source": self.provider,
                "thumb_url": thumb_url,
                "meaning": (entry or {}).get("meaning", ""),
                "meaning_es": (entry or {}).get("meaning_es", ""),
                "verified": bool((entry or {}).get("verified")),
                "cue_text": (entry or {}).get("cue_text", ""),
            }
            with state.lock:
                state.detections.append(row)
            log_detection(row)
            bus.publish({"type": "detection", **row})
            if critical and row["cue_text"]:
                bus.publish(
                    {
                        "type": "cue",
                        "sign_id": sign_id,
                        "text": row["cue_text"],
                        "audio_url": speak(row["cue_text"]),
                    }
                )
