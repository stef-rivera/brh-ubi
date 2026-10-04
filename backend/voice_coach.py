"""A parked voice practice session. The API key stays on the server."""

from __future__ import annotations

import httpx

from backend.config import settings

TOKEN_URL = "https://api.x.ai/v1/realtime/client_secrets"


def logged_sign_lines(detections: list[dict]) -> list[str]:
    """The sentences that were actually kept on this drive, in the order they appeared."""
    lines = []
    seen = set()
    for row in detections:
        if row.get("recognition_status") != "resolved":
            continue
        text = " ".join(str(row.get("sign_text") or "").split())
        if not text or text == "Reading sign…":
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(text)
    return lines


def coach_instructions(detections: list[dict]) -> str:
    """Short spoken practice from the signs on this drive, then one dispatcher question about those signs."""
    signs = logged_sign_lines(detections)
    if signs:
        passed = "Signs logged on this drive: " + "; ".join(signs) + "."
        followup = (
            "After those signs, become a dispatcher checking in on the radio. "
            "Ask one practical question a driver would get about the signs that were actually logged. "
            "Use only those signs. Do not mention anything that was not logged. "
        )
    else:
        passed = "No signs were logged on this drive."
        followup = (
            "Ask them to describe the road they just drove. "
            "Then become a dispatcher checking in on the radio and ask one ordinary question, such as where they are headed. "
            "Do not mention a sign they did not pass. "
        )
    return (
        "You are a calm American English coach talking with a truck driver who just parked. "
        "Speak in short sentences. Do not lecture. Do not list every sign at once. "
        f"{passed} "
        "If signs were logged, ask about one of them, then the next, one question at a time. "
        "If they answer well, say so and move on. If they miss it, give the plain meaning once and ask them to say it back. "
        "If they ask to hear a sign in Spanish, give one short Spanish sentence, then return to English. "
        f"{followup}"
        "Stay in that scene for two or three short turns. Then tell them practice is done and stop asking questions."
    )


def mint_voice_session(detections: list[dict]) -> dict:
    """Return a short-lived browser token and the coach script. Never return the API key."""
    if not settings.xai_api_key:
        raise RuntimeError("Voice practice needs an xAI key.")
    instructions = coach_instructions(detections)
    response = httpx.post(
        TOKEN_URL,
        headers={"Authorization": f"Bearer {settings.xai_api_key}", "Content-Type": "application/json"},
        json={"expires_after": {"seconds": 600}},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("value")
    secret = payload.get("client_secret")
    if not token and isinstance(secret, dict):
        token = secret.get("value")
    if not token and isinstance(secret, str):
        token = secret
    if not token:
        raise RuntimeError("Voice practice did not return a session token.")
    return {"token": token, "expires_at": payload.get("expires_at"), "instructions": instructions}
