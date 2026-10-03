"""One cheap call each to Gemini, ElevenLabs, and Tiger Data.

Tiger Data is optional. A missing TIGER_DATABASE_URL prints SKIP.
Exit status is non-zero only when Gemini or ElevenLabs fails, or when a
Tiger URL is set and the connection fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402


def check_gemini() -> None:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents='Return a JSON object {"ok": true} and nothing else.',
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    text = (response.text or "").strip()
    if '"ok"' not in text or "true" not in text.lower():
        raise RuntimeError(f"unexpected response: {text[:200]}")


# Premade ElevenLabs voice used only when .env has no ELEVENLABS_VOICE_ID.
# Listing voices needs the voices_read permission, which a new key may not have.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


def _voice_id(_client) -> str:
    if settings.elevenlabs_voice_id:
        return settings.elevenlabs_voice_id
    print(f"  no ELEVENLABS_VOICE_ID set; trying premade voice {DEFAULT_VOICE_ID}")
    return DEFAULT_VOICE_ID


def check_elevenlabs() -> None:
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    voice_id = _voice_id(client)
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text="test",
        model_id=settings.elevenlabs_model,
    )
    data = b"".join(audio)
    out = Path("/tmp/el_test.mp3")
    out.write_bytes(data)
    if out.stat().st_size <= 0:
        raise RuntimeError("ElevenLabs returned an empty audio file")


def check_tiger() -> str:
    url = settings.tiger_database_url
    if not url:
        print("SKIP  Tiger Data  TIGER_DATABASE_URL is not set")
        return "skip"
    import psycopg2

    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT now()")
            cur.fetchone()
            cur.execute(
                "SELECT extversion FROM pg_extension WHERE extname='timescaledb'"
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("connected, but the timescaledb extension is not installed")
    return "pass"


def main() -> int:
    results: list[tuple[str, str]] = []
    failed = False

    for name, fn in (("Gemini", check_gemini), ("ElevenLabs", check_elevenlabs)):
        try:
            fn()
        except Exception as exc:
            failed = True
            print(f"FAIL  {name}  {type(exc).__name__}: {exc}")
            results.append((name, "fail"))
        else:
            print(f"PASS  {name}")
            results.append((name, "pass"))

    try:
        tiger = check_tiger()
    except Exception as exc:
        failed = True
        print(f"FAIL  Tiger Data  {type(exc).__name__}: {exc}")
    else:
        if tiger == "pass":
            print("PASS  Tiger Data")

    print("---")
    if failed:
        print("Smoke test failed. Fix the FAIL lines before Tier 1.")
        return 1
    print("Gemini and ElevenLabs are usable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
