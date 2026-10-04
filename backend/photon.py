"""Parked sign practice over Photon. Vision and the browser quiz stay independent."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import uuid
import urllib.request
import urllib.error

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.config import ROOT, settings
from backend.practice import build_session, grade
from backend.state import state
from backend.storage import log_answer

router = APIRouter()
SESSION_PATH = ROOT / '.local' / 'photon' / 'sessions.json'
_lock = asyncio.Lock()
_sessions: dict[str, dict] = {}


def _load() -> None:
    global _sessions
    try:
        data = json.loads(SESSION_PATH.read_text())
        _sessions = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        _sessions = {}


def _save() -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(_sessions, indent=2))
    tmp.replace(SESSION_PATH)


def _token() -> str:
    return os.getenv('PHOTON_BRIDGE_TOKEN', '').strip()


def _recipient() -> str:
    return (os.getenv('PHOTON_TEST_RECIPIENT') or settings.photon_test_recipient or '').strip()


def _canonical(value: str) -> str:
    return ''.join(c for c in value if c.isdigit()) if value.startswith('+') else value.casefold()


def _configured() -> bool:
    return bool((os.getenv('PHOTON_PROJECT_ID') or settings.photon_project_id) and
                (os.getenv('PHOTON_PROJECT_SECRET') or settings.photon_project_secret) and
                _recipient() and _token())


async def _bridge(path: str, payload: dict | None = None) -> dict:
    def request():
        encoded = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(settings.photon_bridge_url.rstrip('/') + path, data=encoded,
              headers={'Authorization': 'Bearer ' + _token(), 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    try:
        return await asyncio.to_thread(request)
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        # Never include provider response bodies or secrets in UI errors.
        raise HTTPException(503, 'Photon bridge unavailable. Start the bridge and check its connection status.') from exc


def _view(session: dict | None = None, *, connected: bool = False, message: str = '') -> dict:
    return dict(configured=_configured(), connected=connected,
                status=session.get('status', 'ready') if session else 'ready',
                session_id=session.get('session_id') if session else None,
                index=min(session['index'] + 1, len(session['items'])) if session else 0,
                total=len(session['items']) if session else 0,
                message=message or (session.get('error', '') if session else ''))


@router.get('/api/integrations/photon/status')
async def integration_status():
    if not _configured():
        return {**_view(), 'status': 'needs_setup', 'message':
            'Set Photon project ID, project secret, approved recipient, and bridge token; enable an iMessage line in Photon.'}
    try:
        health = await _bridge('/health')
    except HTTPException as exc:
        return {**_view(), 'status': 'disconnected', 'message': exc.detail}
    return {**_view(connected=bool(health.get('connected'))),
            'status': health.get('status', 'disconnected'), 'message': health.get('message', '')}


class StartRequest(BaseModel):
    drive_id: str
    recipient: str | None = None


class ReplyRequest(BaseModel):
    session_id: str
    message_id: str = Field(min_length=1, max_length=256)
    sender: str
    answer: str = Field(min_length=1, max_length=2000)
    question_index: int = Field(ge=0)


def _ensure_parked(drive_id: str) -> None:
    with state.lock:
        if state.mode != 'parked' or state.drive_id != drive_id:
            raise HTTPException(409, 'Park this drive before continuing iMessage practice.')


def _image(item: dict) -> dict | None:
    # Thumbnails are local captures. Do not let transport fetch arbitrary URLs/files.
    thumb = item.get('thumb_url', '')
    if not thumb.startswith('/thumbs/'):
        return None
    root = (ROOT / 'data' / 'thumbs').resolve()
    path = (root / thumb.removeprefix('/thumbs/')).resolve()
    if path.parent != root or path.suffix.lower() not in {'.jpg', '.jpeg', '.png'} or not path.is_file():
        return None
    if path.stat().st_size > 2_000_000:
        return None
    return dict(base64=base64.b64encode(path.read_bytes()).decode(), name=path.name,
                mimeType='image/png' if path.suffix.lower() == '.png' else 'image/jpeg')


def _question(session: dict) -> dict:
    index = session['index']
    item = session['items'][index]
    return dict(idempotency_key=f"{session['session_id']}:question:{index}",
                session_id=session['session_id'], question_index=index,
                recipient=session['recipient'],
                text=f"Road English • {index + 1}/{len(session['items'])}\n{item['question']}\nReply with your answer. Reply STOP to end practice.",
                image=_image(item))


async def _deliver(session: dict) -> None:
    _ensure_parked(session['drive_id'])
    for outgoing in list(session.get('outbox', [])):
        _ensure_parked(session['drive_id'])
        response = await _bridge('/send', outgoing)
        if not response.get('accepted'):
            raise HTTPException(503, 'Photon did not accept the message. Check bridge status.')
        session['outbox'].remove(outgoing)
        _save()
    session['status'] = 'completed' if session['index'] >= len(session['items']) else 'waiting_reply'
    session.pop('error', None)
    _save()


@router.post('/api/photon/practice/start')
async def start(request: StartRequest):
    if not _configured():
        raise HTTPException(503, 'Photon setup is incomplete. Open integration status for the required settings.')
    recipient = request.recipient or _recipient()
    if _canonical(recipient) != _canonical(_recipient()):
        raise HTTPException(403, 'Recipient must match the approved PHOTON_TEST_RECIPIENT.')
    _ensure_parked(request.drive_id)
    health = await _bridge('/health')
    if not health.get('connected'):
        raise HTTPException(503, 'Photon iMessage line is not connected.')
    async with _lock:
        _load()
        existing = next((s for s in _sessions.values() if s['drive_id'] == request.drive_id
                         and _canonical(s['recipient']) == _canonical(recipient)
                         and s['status'] not in {'stopped'}), None)
        if existing:
            if existing.get('outbox'):
                await _deliver(existing)
            return _view(existing, connected=True, message='This drive’s iMessage practice is already complete.' if existing['status'] == 'completed' else 'iMessage practice is waiting for your reply.')
        with state.lock:
            # Exclusions are explicit even for historical rows without recognition_status.
            rows = [dict(r) for r in state.detections if not r.get('exclude_from_practice')
                    and r.get('feedback_action') not in {'ignore', 'not_sign'}]
        items = build_session(rows)
        if not items:
            raise HTTPException(409, 'No captured, resolved signs are available for practice yet.')
        session = dict(session_id=uuid.uuid4().hex, drive_id=request.drive_id, recipient=recipient,
                       items=items, index=0, status='sending', seen_messages=[], answers=[], outbox=[])
        session['outbox'] = [_question(session)]
        _sessions[session['session_id']] = session
        _save()
        try:
            await _deliver(session)
        except HTTPException:
            session['status'] = 'delivery_failed'
            session['error'] = 'Could not deliver. Press Send again to retry the same session.'
            _save()
            raise
        return _view(session, connected=True, message='First captured sign sent. Reply in iMessage to practice.')


@router.get('/api/photon/practice/status')
async def practice_status(drive_id: str | None = None):
    async with _lock:
        _load()
        target = drive_id or state.drive_id
        sessions = [s for s in _sessions.values() if s['drive_id'] == target]
        return _view(sessions[-1] if sessions else None)


@router.post('/api/photon/practice/reply')
async def reply(request: ReplyRequest, authorization: str = Header(default='')):
    expected = 'Bearer ' + _token()
    if not _token() or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, 'Invalid bridge authentication.')
    async with _lock:
        _load()
        session = _sessions.get(request.session_id)
        if not session or _canonical(request.sender) != _canonical(session['recipient']):
            raise HTTPException(404, 'Practice conversation not found.')
        if request.message_id in session['seen_messages']:
            if session.get('outbox'):
                await _deliver(session)
            return {**_view(session, connected=True), 'duplicate': True}
        _ensure_parked(session['drive_id'])
        if request.answer.strip() == 'STOP' or request.answer.strip().casefold() in {'stop practice', 'cancel practice', 'end practice'}:
            session['status'] = 'stopped'
            session['outbox'] = []
            session['seen_messages'].append(request.message_id)
            _save()
            return _view(session, connected=True, message='Practice stopped.')
        if session['status'] in {'completed', 'stopped'} or request.question_index != session['index']:
            return {**_view(session), 'ignored': True, 'message': 'This reply is for an earlier question.'}
        item = session['items'][session['index']]
        result = grade(item, request.answer)
        answer = dict(ts=datetime.now(timezone.utc).isoformat(), drive_id=session['drive_id'],
                      event_id=item.get('event_id'), sign_id=item['sign_id'], question=item['question'],
                      answer=request.answer, grade=result, channel='photon',
                      session_id=session['session_id'], message_id=request.message_id)
        log_answer(answer)
        session['answers'].append(answer)
        session['seen_messages'].append(request.message_id)
        old_index = session['index']
        session['index'] += 1
        feedback = f"{result.capitalize()}. {item['meaning']}"
        if session['index'] == len(session['items']):
            correct = sum(a['grade'] == 'correct' for a in session['answers'])
            feedback += f"\nPractice complete: {correct}/{len(session['items'])} correct."
        session['outbox'] = [dict(idempotency_key=f"{session['session_id']}:feedback:{old_index}",
                                  session_id=session['session_id'], question_index=old_index,
                                  recipient=session['recipient'], text=feedback)]
        if session['index'] < len(session['items']):
            session['outbox'].append(_question(session))
        session['status'] = 'sending'
        _save()
        await _deliver(session)
        return {**_view(session, connected=True), 'grade': result}
