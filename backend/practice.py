"""A short parked quiz built from the signs logged on this drive."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from backend.catalog import get, NUMERIC_SIGNS
from backend.state import state
from backend.storage import log_answer
from backend.tts import speak


def _speed_value(row: dict) -> int | None:
    raw = row.get("value")
    if raw is None:
        match = re.search(r"\b(\d{1,3})\b", row.get("sign_text", ""))
        raw = match.group(1) if match else None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 5 <= value <= 85 else None


def build_session(detections: list[dict]) -> list[dict]:
    seen = []
    used = set()
    eligible = [row for row in detections
                if row.get("recognition_status", "resolved") == "resolved"]
    critical = [row for row in eligible if row.get("safety_critical")]
    rest = [row for row in eligible if not row.get("safety_critical")]
    for row in critical + rest:
        sign_id = row.get("sign_id")
        entry = get(sign_id) if sign_id else None
        if not entry or not entry.get("questions"):
            continue
        sign_text = row.get("sign_text") or entry["sign_text"]
        value = _speed_value(row) if sign_id in NUMERIC_SIGNS else None
        identity = (sign_id, value if sign_id in NUMERIC_SIGNS else sign_text.casefold().strip())
        if identity in used:
            continue
        used.add(identity)
        question = entry["questions"][0]
        item = {
            "event_id": row.get("event_id"),
            "sign_id": sign_id,
            "sign_text": sign_text,
            "value": value,
            "question": question["q"],
            "answer_keywords": question.get("answer_keywords") or [],
            "meaning": entry["meaning"],
            "meaning_es": entry["meaning_es"],
            "thumb_url": row.get("thumb_url", ""),
            "verified": bool(entry.get("verified")),
        }
        if sign_id == "speed_limit" and value is not None:
            item.update(
                sign_text=f"SPEED LIMIT {value}",
                question="What maximum speed, in miles per hour, is shown on this sign?",
                required_value=value,
                meaning=f"This sign shows a maximum speed of {value} miles per hour.",
                meaning_es=f"Este letrero indica una velocidad máxima de {value} millas por hora.",
            )
        seen.append(item)
        if len(seen) == 5:
            break
    return seen


def _answer_numbers(text: str) -> set[int]:
    numbers = {int(number) for number in re.findall(r"\b\d{1,3}\b", text)}
    ones = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9}
    tens = {"ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
            "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80}
    words = re.findall(r"[a-z]+", text)
    for index, word in enumerate(words):
        if word in tens:
            next_word = words[index + 1] if index + 1 < len(words) else ""
            numbers.add(tens[word] + ones.get(next_word, 0))
        elif word in ones and (index == 0 or words[index - 1] not in tens):
            numbers.add(ones[word])
    return numbers


def grade(item: dict, answer: str) -> str:
    text = answer.casefold().strip()
    if item.get("required_value") is not None:
        numbers = _answer_numbers(text)
        return "correct" if numbers == {item["required_value"]} else "incorrect"
    hits = [word for word in item["answer_keywords"]
            if re.search(r"(?<!\w)" + re.escape(word.casefold()) + r"(?!\w)", text)]
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
    item = {key: value for key, value in items[index].items() if key not in {"answer_keywords", "required_value"}}
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
