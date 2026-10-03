"""One Gemini call that names signs in a frame. It does not define meanings."""

from __future__ import annotations

import json
import time

import cv2
from google import genai
from google.genai import types

from backend.catalog import ids, load_catalog, prompt_list
from backend.config import settings

_client: genai.Client | None = None

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "signs": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "sign_id": {"type": "STRING"},
                    "sign_text": {"type": "STRING"},
                    "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["sign_id", "sign_text", "box_2d", "confidence"],
            },
        }
    },
    "required": ["signs"],
}


def _client_or_new() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def downscale(frame, long_side: int = 640):
    height, width = frame.shape[:2]
    scale = long_side / max(height, width)
    if scale >= 1:
        return frame
    size = (int(width * scale), int(height * scale))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def _match_id(sign_id: str, sign_text: str, known: set[str], catalog: list[dict]) -> str:
    if sign_id in known and sign_id != "unknown":
        return sign_id
    text = sign_text.upper()
    best = "unknown"
    best_len = 0
    for entry in catalog:
        label = entry["sign_text"].upper()
        if label and label in text and len(label) > best_len:
            best = entry["id"]
            best_len = len(label)
    return best


def _box_to_pixels(box, width: int, height: int) -> list[int] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    ymin, xmin, ymax, xmax = [float(v) for v in box]
    x1 = int(max(0, min(width - 1, xmin / 1000 * width)))
    y1 = int(max(0, min(height - 1, ymin / 1000 * height)))
    x2 = int(max(0, min(width, xmax / 1000 * width)))
    y2 = int(max(0, min(height, ymax / 1000 * height)))
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def read_frame(frame_bgr) -> list[dict]:
    """Return signs Gemini sees. Boxes are pixels of the original frame."""
    height, width = frame_bgr.shape[:2]
    small = downscale(frame_bgr)
    ok, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return []
    prompt = (
        "You are looking at one dashcam frame. Find US road signs.\n"
        "Match each sign to one catalog id. If it is not in the catalog, use unknown.\n"
        "Do not explain the law. Do not invent a meaning.\n"
        "box_2d is [ymin, xmin, ymax, xmax] with each value from 0 to 1000.\n"
        "Catalog:\n"
        f"{prompt_list()}"
    )
    known = set(ids()) | {"unknown"}
    catalog = load_catalog()
    payload = None
    last_error = None
    for _attempt in range(2):
        try:
            response = _client_or_new().models.generate_content(
                model=settings.gemini_model,
                contents=[
                    types.Part.from_bytes(data=encoded.tobytes(), mime_type="image/jpeg"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SCHEMA,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            payload = json.loads(response.text or "{}")
            break
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if "503" not in message and "UNAVAILABLE" not in message:
                break
            time.sleep(1)
    if payload is None:
        print(f"gemini read_frame failed: {type(last_error).__name__}: {last_error}")
        return []

    found = []
    for raw in payload.get("signs") or []:
        sign_id = _match_id(str(raw.get("sign_id") or "unknown"), str(raw.get("sign_text") or ""), known, catalog)
        box = _box_to_pixels(raw.get("box_2d"), width, height)
        found.append(
            {
                "sign_id": sign_id,
                "sign_text": str(raw.get("sign_text") or ""),
                "box": box,
                "confidence": float(raw.get("confidence") or 0),
            }
        )
    return found
