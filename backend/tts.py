"""Cached ElevenLabs speech. The browser voice is the fallback when this fails."""

from __future__ import annotations

import hashlib
import threading

from elevenlabs.client import ElevenLabs

from backend.config import ROOT, settings

AUDIO = ROOT / "data" / "audio"
_client: ElevenLabs | None = None
_cache_lock = threading.Lock()


def speak(text: str) -> str:
    # Concurrent practice/replay requests should create only one cached file.
    with _cache_lock:
        return _speak_cached(text)


def _speak_cached(text: str) -> str:
    """Return an /audio URL for this line, or an empty string."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    AUDIO.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(cleaned.encode()).hexdigest()[:16] + ".mp3"
    path = AUDIO / name
    if not path.exists() or path.stat().st_size == 0:
        if not settings.elevenlabs_voice_id or not settings.elevenlabs_api_key:
            return ""
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
        temporary = path.with_suffix(".mp3.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    return f"/audio/{name}"
