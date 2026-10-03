"""Reads frames on its own thread. Detection never blocks playback."""

from __future__ import annotations

import threading
import time

import cv2


class VideoSource:
    def __init__(self, source: str | int):
        self.source = source
        self._lock = threading.Lock()
        self._frame = None
        self._ts = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None

    def start(self) -> None:
        if self._running:
            return
        cap = self._open()
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open video source {self.source!r}")
        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="video", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def latest(self):
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy(), self._ts

    def _open(self) -> cv2.VideoCapture:
        if isinstance(self.source, int):
            cap = cv2.VideoCapture(self.source, cv2.CAP_AVFOUNDATION)
            return cap
        return cv2.VideoCapture(self.source)

    def _loop(self) -> None:
        cap = self._cap
        assert cap is not None
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        if fps < 1 or fps > 120:
            fps = 30.0
        frame_wait = 1.0 / fps
        is_file = not isinstance(self.source, int)
        next_tick = time.perf_counter()
        while self._running:
            ok, frame = cap.read()
            if not ok:
                if is_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                time.sleep(0.05)
                continue
            if is_file:
                ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            else:
                ts = time.time()
            with self._lock:
                self._frame = frame
                self._ts = ts
            if is_file:
                next_tick += frame_wait
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.perf_counter()
            else:
                time.sleep(0.03)
