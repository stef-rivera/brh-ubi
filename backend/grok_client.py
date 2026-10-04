"""Grok reads one sign crop. The local model still finds the box."""

from __future__ import annotations

import base64
import json
import re

import cv2
import httpx

from backend.catalog import ids, load_catalog, prompt_list
from backend.config import settings
from backend.gemini_client import _match_id


def read_crops(crop_bgr) -> list[dict]:
    found = read_crop(crop_bgr)
    return [found] if found else []


def read_crop(crop_bgr) -> dict | None:
    """Return sign_id, sign_text, and confidence for one tight crop."""
    if crop_bgr is None or getattr(crop_bgr, "size", 0) == 0 or not settings.xai_api_key:
        return None
    ok, encoded = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return None
    prompt = (
        "This image is a tight crop of one US road sign from a dashcam.\n"
        "Read the words that are actually visible, including any number.\n"
        "Match sign_id to one catalog id. If it is not in the catalog, use unknown.\n"
        "Do not explain the law. Do not add words that are not on the sign.\n"
        "Reply with JSON only: {\"sign_id\": \"...\", \"sign_text\": \"...\", \"confidence\": 0.0}\n"
        "Catalog:\n"
        f"{prompt_list()}"
    )
    image = "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")
    body = {
        "model": settings.xai_model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": image, "detail": "high"},
                    {"type": "input_text", "text": prompt},
                ],
            }
        ],
        "reasoning": {"effort": "low"},
    }
    try:
        response = httpx.post(
            f"{settings.xai_base_url}/responses",
            headers={"Authorization": f"Bearer {settings.xai_api_key}"},
            json=body,
            timeout=40,
        )
        response.raise_for_status()
        raw = _parse_json(_response_text(response.json()))
    except Exception as exc:
        detail = exc.response.text[:240] if isinstance(exc, httpx.HTTPStatusError) else str(exc)
        print(f"grok read_crop failed: {type(exc).__name__}: {detail}")
        return None
    sign_text = str(raw.get("sign_text") or "").strip()
    if not sign_text:
        return None
    known = set(ids()) | {"unknown"}
    return {
        "sign_id": _match_id(str(raw.get("sign_id") or "unknown"), sign_text, known, load_catalog()),
        "sign_text": sign_text,
        "confidence": float(raw.get("confidence") or 0),
    }


def _response_text(payload: dict) -> str:
    parts = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if text:
                parts.append(text)
    if parts:
        return "\n".join(parts)
    return str(payload.get("output_text") or "")


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    fenced = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(0)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
