"""Settings loaded from the repo-root .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        value = os.getenv(name.lower())
    if value is None or not value.strip():
        return default
    return value.strip()


def _require(name: str) -> str:
    value = _get(name)
    if not value:
        raise RuntimeError(f"Missing {name}. Add it to .env (see .env.example).")
    return value


def _flag(name: str) -> bool:
    return (_get(name, "0") or "0").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    sign_reader: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str | None
    elevenlabs_model: str
    tiger_database_url: str | None
    video_path: str
    yolo_weights: str
    yolo_min_box_px: int
    detector: str
    detect_interval_s: float
    dedup_window_s: float
    defer_cues: bool
    offline: bool
    photon_bridge_url: str
    photon_project_id: str | None
    photon_project_secret: str | None
    photon_test_recipient: str | None


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-3.5-flash") or "gemini-3.5-flash",
        sign_reader=_get("SIGN_READER", "gemini") or "gemini",
        elevenlabs_api_key=_require("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=_get("ELEVENLABS_VOICE_ID"),
        elevenlabs_model=_get("ELEVENLABS_MODEL", "eleven_flash_v2_5") or "eleven_flash_v2_5",
        tiger_database_url=_get("TIGER_DATABASE_URL"),
        video_path=_get("VIDEO_PATH", "data/drive.mp4") or "data/drive.mp4",
        yolo_weights=_get("YOLO_WEIGHTS", "data/weights/us_signs.pt") or "data/weights/us_signs.pt",
        yolo_min_box_px=int(_get("YOLO_MIN_BOX_PX", "48") or "48"),
        detector=_get("DETECTOR", "gemini") or "gemini",
        detect_interval_s=float(_get("DETECT_INTERVAL_S", "2.0") or "2.0"),
        dedup_window_s=float(_get("DEDUP_WINDOW_S", "15") or "15"),
        defer_cues=_flag("DEFER_CUES"),
        offline=_flag("OFFLINE"),
        photon_bridge_url=_get("PHOTON_BRIDGE_URL", "http://localhost:3001") or "http://localhost:3001",
        photon_project_id=_get("PHOTON_PROJECT_ID"),
        photon_project_secret=_get("PHOTON_PROJECT_SECRET"),
        photon_test_recipient=_get("PHOTON_TEST_RECIPIENT"),
    )


settings = load_settings()
