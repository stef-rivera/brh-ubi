"""A short parked quiz built from the signs logged on this drive."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.catalog import get
from backend.state import state
from backend.storage import log_answer
from backend.tts import speak


def build_session(detections: list[dict]) -> list[dict]:
    seen = []
    used = set()
    critical = [row for row in detections if row.get("safety_critical")]
    rest = [row for row in detections if not row.get("safety_critical")]
    for row in critical + rest:
        sign_id = row.get("sign_id")
        if not sign_id or sign_id == "unknown" or sign_id in used:
            continue
        entry = get(sign_id)
        if not entry or not entry.get("questions"):
            continue
        used.add(sign_id)
        question = entry["questions"][0]
        seen.append(
            {
                "sign_id": sign_id,
                "sign_text": entry["sign_text"],
                "question": question["q"],
                "answer_keywords": question.get("answer_keywords") or [],
                "meaning": entry["meaning"],
                "meaning_es": entry["meaning_es"],
                "thumb_url": row.get("thumb_url", ""),
                "verified": bool(entry.get("verified")),
            }
        )
        if len(seen) == 5:
            break
    return seen


def grade(item: dict, answer: str) -> str:
    text = answer.lower()
    hits = [word for word in item["answer_keywords"] if word.lower() in text]
    if not hits:
        return "incorrect"
    if len(hits) == len(item["answer_keywords"]) or any(len(word) > 4 for word in hits):
        return "correct"
    return "partial"


def current_question() -> dict | None:
    with state.lock:
        items = state.practice_items
        index = state.practice_index
    if index >= len(items):
        return None
    item = {key: value for key, value in items[index].items() if key != "answer_keywords"}
    return {
        "type": "practice_question",
        "index": index + 1,
        "total": len(items),
        **item,
        "audio_url": speak(item["question"]),
    }


def submit(answer: str) -> dict:
    with state.lock:
        if state.practice_index >= len(state.practice_items):
            return {"type": "practice_done", "summary": _summary_locked()}
        item = state.practice_items[state.practice_index]
        drive_id = state.drive_id
    result = grade(item, answer)
    log_answer(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "drive_id": drive_id,
            "sign_id": item["sign_id"],
            "question": item["question"],
            "answer": answer,
            "grade": result,
            "channel": "ui",
        }
    )
    with state.lock:
        state.practice_index += 1
        done = state.practice_index >= len(state.practice_items)
        summary = _summary_locked() if done else None
    payload = {
        "type": "practice_result",
        "sign_id": item["sign_id"],
        "grade": result,
        "meaning": item["meaning"],
        "meaning_es": item["meaning_es"],
        "verified": item["verified"],
    }
    if done:
        payload["done"] = True
        payload["summary"] = summary
    return payload


def _summary_locked() -> dict:
    return {"asked": len(state.practice_items)}
