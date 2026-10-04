"""Cached ElevenLabs speech. The browser voice is the fallback when this fails."""

from __future__ import annotations

import hashlib

from elevenlabs.client import ElevenLabs

from backend.config import ROOT, settings

AUDIO = ROOT / "data" / "audio"
_client: ElevenLabs | None = None


def spanish_line(sign_text: str, meaning_es: str) -> str:
    """Spanish for the sign that was logged, not a fixed stop-sign script."""
    said = " ".join((sign_text or "").split())
    meaning = " ".join((meaning_es or "").split())
    if said and meaning:
        return f"El letrero dice: {said}. {meaning}"
    return meaning or said


def speak(text: str, language: str = "en") -> str:
    """Return an /audio URL for this line, or an empty string."""
    cleaned = " ".join(text.split())
    if not cleaned or not settings.elevenlabs_voice_id:
        return ""
    language = "es" if language.startswith("es") else "en"
    AUDIO.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{language}:{cleaned}".encode()).hexdigest()[:16] + ".mp3"
    path = AUDIO / name
    if not path.exists() or path.stat().st_size == 0:
        global _client
        if _client is None:
            _client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        try:
            kwargs = dict(voice_id=settings.elevenlabs_voice_id, text=cleaned, model_id=settings.elevenlabs_model)
            if language == "es":
                kwargs["language_code"] = "es"
            data = b"".join(_client.text_to_speech.convert(**kwargs))
        except Exception as exc:
            print(f"elevenlabs speak failed: {type(exc).__name__}: {exc}")
            return ""
        if not data:
            return ""
        path.write_bytes(data)
    return f"/audio/{name}"
