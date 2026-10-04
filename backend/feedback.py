"""Human review and raw examples. Corrections are not automatic model training."""
from __future__ import annotations
import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from backend.catalog import get, load_catalog, NUMERIC_SIGNS
from backend.config import ROOT
from backend.events import bus
from backend.practice import build_session, current_question
from backend.state import state
from backend.storage import save_detection

DIRECTORY = ROOT / '.local' / 'feedback'
_lock = threading.RLock()
router = APIRouter()


def _read():
    try:
        rows = json.loads((DIRECTORY / 'records.json').read_text())
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def _write(rows):
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = DIRECTORY / 'records.json.tmp'
    temporary.write_text(json.dumps(rows, indent=2))
    temporary.replace(DIRECTORY / 'records.json')


def _key(drive_id, event_id):
    return hashlib.sha256(f'{drive_id}:{event_id}'.encode()).hexdigest()[:24]


@lru_cache(maxsize=16)
def _fingerprint(path, size, modified):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def source_hash(source):
    if not isinstance(source, (str, Path)):
        return None
    try:
        path = Path(source).resolve(); info = path.stat()
        return _fingerprint(str(path), info.st_size, info.st_mtime_ns)
    except OSError:
        return None


def store_raw_example(job, drive_id, video_source):
    """Save unmarked arrays; a single candidate box is not full-frame annotation."""
    event_id = str(job.get('id', job.get('event_id', '')))
    key = _key(drive_id, event_id)
    with _lock:
        assets = DIRECTORY / 'assets'; assets.mkdir(parents=True, exist_ok=True)
        paths = {}
        for name in ('crop', 'frame'):
            array = job.get(name)
            if array is not None and getattr(array, 'size', 0):
                path = assets / f'{key}-{name}.jpg'
                if cv2.imwrite(str(path), array):
                    paths[name] = str(path.relative_to(DIRECTORY))
        rows = _read()
        record = next((r for r in rows if r['key'] == key), None)
        if record is None:
            record = {'key': key, 'drive_id': drive_id, 'event_id': event_id, 'history': []}
            rows.append(record)
        record.update(source_sha256=source_hash(video_source), ts_video=float(job.get('ts', 0)), first_seen_video=float(job.get('first_seen_video', job.get('ts', 0))), box=list(job.get('box', [])), assets=paths, training_ready=False)
        _write(rows)
    return key


def register_prediction(row):
    """Freeze the unreviewed prediction once, separate from current card state."""
    with _lock:
        rows = _read(); key = _key(row['drive_id'], row['event_id'])
        record = next((r for r in rows if r['key'] == key), None)
        if record is None:
            record = {'key': key, 'drive_id': row['drive_id'], 'event_id': row['event_id'], 'history': [], 'training_ready': False}
            rows.append(record)
        if row.get('recognition_status') != 'pending' and not row.get('human_reviewed'):
            record['latest_prediction'] = copy.deepcopy(row)
        if 'original_prediction' not in record and row.get('recognition_status') != 'pending':
            original = copy.deepcopy(row.get('original_prediction') or row)
            original.update({name: row[name] for name in ('drive_id', 'event_id', 'ts_video', 'ts_wall', 'box', 'thumb_url', 'first_seen_video') if name in row})
            record['original_prediction'] = original
        _write(rows)


def _iou(a, b):
    if len(a) != 4 or len(b) != 4:
        return 0
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    area = max(0, a[2]-a[0])*max(0, a[3]-a[1])+max(0, b[2]-b[0])*max(0, b[3]-b[1])-intersection
    return intersection / area if area else 0


def _review_distance(record, job):
    """Same-clip matching tolerates motion between sampled frames, not label similarity."""
    dt = abs(record.get('ts_video', 0) - job['ts'])
    a, b = record.get('box', []), job['box']
    if dt > .9 or len(a) != 4 or len(b) != 4:
        return None
    aw, ah = a[2]-a[0], a[3]-a[1]
    bw, bh = b[2]-b[0], b[3]-b[1]
    if min(aw, ah, bw, bh) <= 0:
        return None
    overlap = _iou(a, b)
    if overlap >= .5:
        return dt + (1-overlap)*.1
    # Perspective motion grows with frame separation. Separate signs at the
    # same instant must still be nearly coincident, with comparable shapes.
    dx = abs((a[0]+a[2]-b[0]-b[2])/2) / max(aw, bw)
    dy = abs((a[1]+a[3]-b[1]-b[3])/2) / max(ah, bh)
    limit = min(1.15, .75 + dt*1.5)
    scale = max(aw/bw, bw/aw, ah/bh, bh/ah)
    if dx <= limit and dy <= limit and scale <= 2.0:
        return dt + (dx+dy)*.15 + .1
    return None


def _timestamp_distance(record, job):
    """Use track entry time as an anchor even if its best crop changes later."""
    original = record.get('original_prediction', {})
    anchor = record.get('first_seen_video', original.get('first_seen_video'))
    start = job.get('first_seen_video')
    if anchor is None or start is None or abs(anchor-start) > 1.25:
        return None
    # The review may use a close-up many seconds after the sign first appeared.
    # Match its whole observed interval, rather than only the last 4.5 seconds.
    end = max(anchor, record.get('ts_video', anchor))
    if not anchor - 1.25 <= job['ts'] <= end + .9:
        return None
    a, b = record.get('box', []), job['box']
    if len(a) != 4 or len(b) != 4:
        return None
    # Timestamps alone cannot distinguish simultaneous signs on opposite sides.
    ax, bx = (a[0]+a[2])/2, (b[0]+b[2])/2
    elapsed = abs(record.get('ts_video', anchor) - job['ts'])
    # Perspective moves a roadside sign outward as the car approaches.
    # Avoid a fixed 640-pixel dividing line: clips have different resolutions.
    if abs(ax-bx) > min(550, 250 + elapsed * 60):
        return None
    ah, bh = a[3]-a[1], b[3]-b[1]
    if min(ah, bh) <= 0:
        return None
    aspect_a, aspect_b = (a[2]-a[0])/ah, (b[2]-b[0])/bh
    if max(aspect_a/aspect_b, aspect_b/aspect_a) > 2:
        return None
    return .35 + abs(anchor-start) + abs(ax-bx)/1000


def lookup_review(job, source):
    fingerprint = source_hash(source)
    if not fingerprint:
        return None
    with _lock:
        matches = []
        for record in _read():
            if record.get('source_sha256') != fingerprint or record.get('feedback', {}).get('action') in (None, 'undo'):
                continue
            distance = _review_distance(record, job)
            if distance is None:
                distance = _timestamp_distance(record, job)
            if distance is not None:
                matches.append((distance, record))
    if not matches:
        return None
    # A recent review of the same candidate supersedes older replay reviews.
    best = min(distance for distance, _ in matches)
    nearby = [record for distance, record in matches if distance <= best + .12]
    record = max(nearby, key=lambda r: r['feedback'].get('saved_at', ''))
    return copy.deepcopy({**record['feedback'], **{k: record['reviewed_row'].get(k) for k in ('sign_id', 'sign_text', 'value')}, 'row': record['reviewed_row'], 'original_prediction': record.get('original_prediction'), 'record_key': record['key']})


def apply_review_fields(row, review):
    """Apply a same-clip human review while preserving the current event identity."""
    fields = ('sign_id', 'sign_text', 'value', 'recognition_status', 'exclude_from_practice',
              'meaning', 'meaning_es', 'verified', 'safety_critical', 'cue_text',
              'tentative', 'demo_guess', 'feedback_action', 'driving_audio_eligible')
    result = copy.deepcopy(row)
    saved = review['row']
    result.update({name: saved.get(name) for name in fields if name in saved})
    result.update(source='human-replay', original_source=row.get('source'),
                  human_reviewed=True, feedback_replayed=True, feedback_record_key=review['record_key'], feedback_origin_record=review['record_key'],
                  recognition_error='', audio_url='', audio_text='', audio_error='',
                  audio_status='skipped', speak_now=False)
    # Keep the original machine prediction honest; never inflate its score.
    result['original_prediction'] = copy.deepcopy(review.get('original_prediction'))
    return result


class FeedbackBody(BaseModel):
    drive_id: str
    event_id: str
    action: Literal['confirm', 'correct', 'not_sign', 'ignore', 'undo']
    sign_id: str | None = None
    sign_text: str | None = None
    value: int | None = None


def _review_row(row, body):
    reviewed = copy.deepcopy(row)
    if body.action in ('not_sign', 'ignore'):
        reviewed.update(recognition_status='excluded', exclude_from_practice=True, meaning='', meaning_es='', safety_critical=False)
    else:
        sign_id = body.sign_id if body.action == 'correct' else row.get('sign_id')
        entry = get(sign_id)
        if not entry:
            raise HTTPException(400, 'Select a supported catalog sign type.')
        value = body.value if body.action == 'correct' else row.get('value')
        if sign_id in NUMERIC_SIGNS and (value is None or not 5 <= value <= 85 or value % 5):
            raise HTTPException(400, 'Speed must be 5–85 mph in increments of five.')
        reviewed.update(sign_id=sign_id, sign_text=(body.sign_text or entry['sign_text']).strip()[:160] if body.action == 'correct' else row.get('sign_text', entry['sign_text']), value=value if sign_id in NUMERIC_SIGNS else None, recognition_status='resolved', exclude_from_practice=False, meaning=entry['meaning'], meaning_es=entry.get('meaning_es', ''), verified=bool(entry.get('verified')), safety_critical=bool(entry.get('safety_critical')), cue_text=entry.get('cue_text', ''), tentative=False, demo_guess=False)
        if sign_id in NUMERIC_SIGNS:
            reviewed['sign_text'] = f"{entry['sign_text']} {value}"
    reviewed['driving_audio_eligible'] = bool(reviewed.get('printed_text') and get(reviewed.get('sign_id')) and body.action not in ('ignore', 'not_sign'))
    reviewed.update(original_source=row.get('original_source', row.get('source')), source='human', human_reviewed=True, feedback_action=body.action, recognition_error='', audio_url='', audio_text='', audio_error='', audio_status='skipped', speak_now=False)
    return reviewed


def apply_feedback(body):
    with state.lock:
        if body.drive_id != state.drive_id:
            raise HTTPException(404, 'This drive is no longer active.')
        index = next((i for i, row in enumerate(state.detections) if row.get('event_id') == body.event_id), None)
        if index is None:
            # Played cards remain reviewable even if tracking later removes them.
            # Recover only this active drive's persisted server-side candidate.
            with _lock:
                archived = next((r for r in _read() if r['key'] == _key(body.drive_id, body.event_id)), None)
            if archived is None:
                raise HTTPException(404, 'Sign candidate not found.')
            row = copy.deepcopy(archived.get('reviewed_row') or archived.get('latest_prediction') or archived.get('original_prediction'))
            if not row:
                row = dict(drive_id=body.drive_id,event_id=body.event_id,recognition_status='unresolved',sign_id='unknown',sign_text='Unresolved sign',ts_video=archived.get('ts_video',0),box=archived.get('box',[]))
        else:
            row = copy.deepcopy(state.detections[index])
        if row.get('recognition_status') == 'pending' and body.action not in ('ignore', 'not_sign'):

            raise HTTPException(409, 'Wait for this candidate to finish recognition.')
        with _lock:
            rows = _read(); key = _key(body.drive_id, body.event_id)
            record = next((r for r in rows if r['key'] == key), None)
            if record is None:
                record = {'key': key, 'drive_id': body.drive_id, 'event_id': body.event_id, 'history': [], 'training_ready': False}
                rows.append(record)
            original = record.setdefault('original_prediction', copy.deepcopy(row.get('original_prediction') or row))
            reviewed = copy.deepcopy(original) if body.action == 'undo' else _review_row(row, body)
            if body.action == 'undo':
                reviewed.update({name: row[name] for name in ('drive_id', 'event_id', 'ts_video', 'ts_wall', 'box', 'thumb_url', 'first_seen_video') if name in row})
            reviewed['original_prediction'] = copy.deepcopy(original)
            if body.action == 'undo':
                reviewed.update(human_reviewed=False, feedback_action='undo', audio_url='', audio_text='', audio_status='skipped')
            feedback = {**body.model_dump(), 'saved_at': datetime.now(timezone.utc).isoformat()}
            record.update(feedback=feedback, reviewed_row=reviewed)
            record['history'].append(feedback)
            origin_key = row.get('feedback_origin_record')
            if body.action == 'undo' and origin_key and origin_key != key:
                origin = next((r for r in rows if r['key'] == origin_key), None)
                if origin:
                    origin['feedback'] = dict(feedback, replay_undo=True)
                    origin.setdefault('history', []).append(origin['feedback'])
            _write(rows)
        if index is None:state.detections.append(reviewed)
        else:state.detections[index] = reviewed
        parked = state.mode == 'parked'
        if parked:
            state.practice_items = build_session(state.detections)
            state.practice_index = 0
    save_detection(reviewed)
    from backend.tiger_data import enqueue_event
    enqueue_event('review', feedback)
    bus.publish({'type': 'detection_update', **reviewed})
    bus.publish({'type': 'feedback_saved', **feedback})
    question = None
    if parked:
        question = current_question() or {'type': 'practice_done', 'summary': {'asked': 0}}
        bus.publish(question)
    return {'row': reviewed, 'feedback': feedback, 'practice_reset': parked, 'next': question}


@router.post('/api/feedback')
def feedback_save(body: FeedbackBody):
    return apply_feedback(body)


@router.get('/api/catalog')
def feedback_catalog():
    return {'signs': [{'id': row['id'], 'sign_text': row['sign_text'], 'requires_value': row['id'] in NUMERIC_SIGNS} for row in load_catalog()]}


@router.get('/api/feedback')
def feedback_list():
    with _lock:
        return [row for row in _read() if row.get('feedback')]


@router.get('/api/feedback/image')
@router.get('/api/feedback/{event_id}/image')
def feedback_image(event_id: str, drive_id: str, kind: Literal['crop', 'frame'] = 'crop'):
    with _lock:
        record = next((r for r in _read() if r['key'] == _key(drive_id, event_id)), None)
    asset = record.get('assets', {}).get(kind) if record else None
    if not asset:
        raise HTTPException(404, 'Raw image unavailable.')
    path = (DIRECTORY / asset).resolve()
    if not path.is_relative_to(DIRECTORY.resolve()) or not path.is_file():
        raise HTTPException(404, 'Raw image unavailable.')
    return FileResponse(path, media_type='image/jpeg')
