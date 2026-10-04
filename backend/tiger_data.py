"""Tiger Cloud event history; durable local outbox keeps network off the live path."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from backend.config import ROOT, settings

logger = logging.getLogger(__name__)
router = APIRouter()
DETECTION_FIELDS = ('drive_id', 'event_id', 'ts_wall', 'ts_video', 'sign_id', 'sign_text',
                    'recognition_status', 'human_reviewed', 'feedback_action',
                    'exclude_from_practice', 'feedback_record_key', 'source', 'value')


def summarize(events: list[dict]) -> dict:
    """Only explicitly graded answers contribute to accuracy, including partials."""
    sessions = defaultdict(lambda: {'detections': {}, 'answers': [], 'last_seen': ''})
    sign_results = defaultdict(list)
    for event in events:
        row = event['payload']; drive = row.get('drive_id')
        if not drive:
            continue
        session = sessions[drive]
        session['last_seen'] = max(session['last_seen'], event['occurred_at'])
        if event['kind'] == 'detection':
            session['detections'][row.get('event_id', event['event_key'])] = row
        if event['kind'] == 'answer' and row.get('grade') in {'correct', 'partial', 'incorrect'}:
            session['answers'].append(row)
            if row.get('sign_id'):
                sign_results[row['sign_id']].append(row['grade'])
    recent = []
    for drive, data in sessions.items():
        answers = data['answers']
        recent.append({'drive_id': drive, 'last_seen': data['last_seen'],
                       'signs': sum(r.get('recognition_status') != 'excluded' and not r.get('exclude_from_practice') for r in data['detections'].values()),
                       'answers': len(answers), 'correct': sum(r['grade'] == 'correct' for r in answers)})
    recent.sort(key=lambda row: row['last_seen'], reverse=True)
    struggling = []
    for sign, grades in sign_results.items():
        missed = sum(grade != 'correct' for grade in grades)
        if missed:
            struggling.append({'sign_id': sign, 'attempts': len(grades), 'missed': missed,
                               'accuracy': round(100 * (len(grades) - missed) / len(grades), 1)})
    struggling.sort(key=lambda row: (-row['missed'], row['accuracy'], row['sign_id']))
    total = sum(len(data['answers']) for data in sessions.values())
    correct = sum(row['correct'] for row in recent)
    return {'sessions': len(sessions), 'answers': total,
            'accuracy': round(correct / total, 4) if total else None,
            'recent_sessions': recent[:10], 'struggling_signs': struggling[:10]}


class TigerIntegration:
    def __init__(self, path: Path | None = None, database_url: str | None = None):
        self.path = path or ROOT / '.local' / 'tiger' / 'outbox.sqlite3'
        self.database_url = database_url if database_url is not None else settings.tiger_database_url
        self.lock = threading.RLock()
        self.wake = threading.Event(); self.stopping = threading.Event()
        self.thread = None; self.connected = False; self.last_error = None
        self.remote_progress = None; self.initialized = False

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS outbox (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
            occurred_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            synced INTEGER NOT NULL DEFAULT 0, recorded_at TEXT NOT NULL)''')
        if 'recorded_at' not in {row[1] for row in db.execute('PRAGMA table_info(outbox)')}:
            db.execute("ALTER TABLE outbox ADD COLUMN recorded_at TEXT NOT NULL DEFAULT '2000-01-01T00:00:00+00:00'")
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, kind: str, payload: dict) -> bool:
        # Do not store audio URLs, crops, credentials or unbounded prediction internals.
        row = ({key: payload[key] for key in DETECTION_FIELDS if key in payload}
               if kind == 'detection' else dict(payload))
        if kind == 'answer':
            row = {key: row[key] for key in ('ts', 'drive_id', 'sign_id', 'question', 'answer', 'grade', 'channel', 'event_id') if key in row}
        serialized = json.dumps(row, sort_keys=True, separators=(',', ':'), default=str)
        key = hashlib.sha256((kind + ':v1:' + serialized).encode()).hexdigest()
        raw_time = row.get('saved_at') or row.get('ts_wall') or row.get('ts')
        try:
            parsed = datetime.fromisoformat(str(raw_time).replace('Z', '+00:00'))
            if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
            occurred = parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            # Stable fallback makes a historical backfill idempotent across restarts.
            occurred = '2000-01-01T00:00:00+00:00'
        with self.lock, self._db() as db:
            result = db.execute('INSERT OR IGNORE INTO outbox (event_key,occurred_at,kind,payload,recorded_at) VALUES (?,?,?,?,?)', (key, occurred, kind, serialized, datetime.now(timezone.utc).isoformat()))
        self.wake.set()
        return result.rowcount == 1

    def backfill(self, root: Path = ROOT):
        for name, kind in (('detections_log.json', 'detection'), ('answers_log.json', 'answer')):
            try:
                rows = json.loads((root / 'data' / name).read_text())
                for row in rows:
                    if isinstance(row, dict) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}', str(row.get('drive_id', ''))):
                        self.enqueue(kind, row)
            except (OSError, ValueError, TypeError):
                continue
        try:
            records = json.loads((root / '.local' / 'feedback' / 'records.json').read_text())
            for record in records:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}', str(record.get('drive_id', ''))):
                    continue
                for review in record.get('history', []):
                    self.enqueue('review', {**review, 'drive_id': record.get('drive_id'),
                                          'event_id': record.get('event_id'), 'record_key': record.get('key')})
        except (OSError, ValueError, TypeError):
            pass

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stopping.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name='tiger-outbox')
        self.thread.start()

    def stop(self):
        self.stopping.set(); self.wake.set()
        if self.thread: self.thread.join(timeout=1)

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self.database_url, connect_timeout=3, options='-c statement_timeout=5000')

    def sync_once(self) -> int:
        if not self.database_url: return 0
        # The worker is the only network writer. Mark synced only after remote commit.
        with self.lock, self._db() as local:
            batch = local.execute('SELECT * FROM outbox WHERE synced=0 ORDER BY seq LIMIT 100').fetchall()
        if not batch and self.connected and self.initialized and self.remote_progress is not None:
            return 0
        with closing(self._connect()) as remote:
            with remote.cursor() as cur:
                if not self.initialized:
                    cur.execute((ROOT / 'sql' / 'tiger_events.sql').read_text())
                for event in batch:
                    row = json.loads(event['payload'])
                    cur.execute('''INSERT INTO road_coach_events (occurred_at,event_key,kind,drive_id,sign_id,grade,payload,recorded_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (occurred_at,event_key) DO NOTHING''',
                        (event['occurred_at'], event['event_key'], event['kind'], row.get('drive_id') or '', row.get('sign_id'), row.get('grade'), event['payload'], event['recorded_at']))
            remote.commit()
            self.initialized = True
            with remote.cursor() as cur:
                # Analytics run in Tiger: latest sign state and daily answer buckets.
                cur.execute('''SELECT time_bucket('1 day', occurred_at) AS day, COUNT(*),
                    COUNT(*) FILTER (WHERE grade='correct') FROM road_coach_events
                    WHERE kind='answer' AND grade IN ('correct','partial','incorrect') GROUP BY day ORDER BY day DESC LIMIT 30''')
                daily = [{'day': day.isoformat(), 'answers': answers, 'correct': correct} for day, answers, correct in cur.fetchall()]
                cur.execute('''SELECT kind,event_key,occurred_at,payload FROM road_coach_events
                    WHERE kind='answer' OR (kind='detection' AND event_key IN
                       (SELECT DISTINCT ON (drive_id,payload->>'event_id') event_key FROM road_coach_events
                        WHERE kind='detection' ORDER BY drive_id,payload->>'event_id',recorded_at DESC,event_key DESC))
                    ORDER BY recorded_at,event_key''')
                events = [{'kind': k, 'event_key': key, 'occurred_at': at.isoformat(), 'payload': row}
                          for k, key, at, row in cur.fetchall()]
                self.remote_progress = {**summarize(events), 'daily_answers': daily}
        with self.lock, self._db() as local:
            local.executemany('UPDATE outbox SET synced=1 WHERE event_key=?', [(e['event_key'],) for e in batch])
        self.connected = True; self.last_error = None
        return len(batch)

    def _run(self):
        self.backfill()
        while not self.stopping.is_set():
            try:
                count = self.sync_once()
            except Exception as exc:
                self.connected = False
                # Exception messages from drivers may include credentials/URLs; expose type only.
                self.last_error = f'Tiger connection or migration failed ({type(exc).__name__}); events are saved locally.'
                logger.warning(self.last_error)
                count = 0
            if count == 100: continue
            if self.last_error:
                self.stopping.wait(10)
            else:
                self.wake.wait(3)
            self.wake.clear()

    def progress(self):
        with self.lock, self._db() as db:
            rows = db.execute('SELECT * FROM outbox ORDER BY seq').fetchall()
        events = [{**dict(row), 'payload': json.loads(row['payload'])} for row in rows]
        pending = sum(not row['synced'] for row in rows)
        # Local data includes unsynced newest answers; cloud result used once caught up.
        remote = self.connected and pending == 0 and self.remote_progress is not None
        result = dict(self.remote_progress) if remote else summarize(events)
        result.update(backend='tiger' if remote else 'local',
                      status='connected' if self.connected else ('retrying' if self.database_url else 'not_configured'),
                      events_synced=len(rows)-pending, pending=pending,
                      configured=bool(self.database_url))
        if self.last_error: result['error'] = self.last_error
        return result


integration = TigerIntegration()


def enqueue_event(kind: str, payload: dict):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}', str(payload.get('drive_id', ''))):
        return
    try:
        integration.enqueue(kind, payload)
    except Exception:
        logger.exception('Could not persist Tiger outbox event; primary local log remains available for backfill.')


@router.get('/api/progress')
def progress():
    return integration.progress()


@router.get('/api/integrations/tiger/status')
def status():
    data = integration.progress()
    return {key: data[key] for key in ('backend', 'status', 'configured', 'events_synced', 'pending', 'error') if key in data}
