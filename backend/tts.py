"""Cached ElevenLabs speech. The browser voice is the fallback when this fails."""

from __future__ import annotations

import hashlib

from elevenlabs.client import ElevenLabs

from backend.config import ROOT, settings

AUDIO = ROOT / "data" / "audio"
_client: ElevenLabs | None = None


def speak(text: str) -> str:
    """Return an /audio URL for this line, or an empty string."""
    cleaned = " ".join(text.split())
    if not cleaned or not settings.elevenlabs_voice_id:
        return ""
    AUDIO.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(cleaned.encode()).hexdigest()[:16] + ".mp3"
    path = AUDIO / name
    if not path.exists() or path.stat().st_size == 0:
        global _client
        if _client is None:
            _client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        try:
            data = b"".join(
                _client.text_to_speech.convert(
                    voice_id=settings.elevenlabs_voice_id,
                    text=cleaned,
                    model_id=settings.elevenlabs_model,
                )
            )
        except Exception as exc:
            print(f"elevenlabs speak failed: {type(exc).__name__}: {exc}")
            return ""
        if not data:
            return ""
        path.write_bytes(data)
    return f"/audio/{name}"
