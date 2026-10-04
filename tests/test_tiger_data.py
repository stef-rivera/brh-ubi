import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.tiger_data import TigerIntegration, summarize


class FakeCursor:
    def __init__(self, fail=False): self.fail = fail; self.calls = []; self.rows = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, params=None):
        self.calls.append((query, params))
        if self.fail and 'INSERT INTO' in query: raise RuntimeError('secret URL')
        self.rows = []
    def fetchall(self): return self.rows


class FakeConnection:
    def __init__(self, fail=False): self.cur = FakeCursor(fail); self.committed = False
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return self.cur
    def commit(self): self.committed = True
    def close(self): pass


class TigerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = TigerIntegration(self.root/'outbox.db', database_url='')
    def tearDown(self): self.service.stop(); self.temp.cleanup()
    def row(self, **updates):
        return {'drive_id': 'test-drive', 'event_id': 'sign-1', 'sign_id': 'stop',
                'ts_wall': '2026-10-04T12:00:00+00:00', 'recognition_status': 'resolved', **updates}
    def test_versioned_idempotency_and_exclusion(self):
        self.assertTrue(self.service.enqueue('detection', self.row()))
        self.assertFalse(self.service.enqueue('detection', self.row(audio_url='/new.mp3')))
        self.assertTrue(self.service.enqueue('detection', self.row(recognition_status='excluded', feedback_action='ignore')))
        result = self.service.progress()
        self.assertEqual(result['pending'], 2)
        self.assertEqual(result['recent_sessions'][0]['signs'], 0)
        self.assertEqual(result['backend'], 'local')
        self.assertEqual(result['status'], 'not_configured')
    def test_grades_only_and_accuracy_fraction(self):
        for ts, grade in [('12:00:01', 'correct'), ('12:00:02', 'incorrect'), ('12:00:03', 'partial')]:
            self.service.enqueue('answer', self.row(ts='2026-10-04T'+ts+'Z', grade=grade))
        self.service.enqueue('answer', self.row(answer='voice conversation, no grade'))
        result = self.service.progress()
        self.assertEqual(result['answers'], 3)
        self.assertAlmostEqual(result['accuracy'], .3333)
        self.assertEqual(result['struggling_signs'][0]['missed'], 2)
    def test_durable_restart_and_backfill_without_duplicates(self):
        (self.root/'data').mkdir()
        (self.root/'data'/'detections_log.json').write_text(json.dumps([self.row(drive_id='2026-10-04T08-01-02'), self.row(drive_id='queue-test')]))
        (self.root/'data'/'answers_log.json').write_text(json.dumps([self.row(drive_id='2026-10-04T08-01-02', ts='2026-10-04T12:00:01Z', grade='incorrect')]))
        directory = self.root/'.local'/'feedback'; directory.mkdir(parents=True)
        (directory/'records.json').write_text(json.dumps([{'key':'saved', 'drive_id':'2026-10-04T08-01-02', 'event_id':'sign-1', 'history':[{'action':'ignore','saved_at':'2026-10-04T12:00:03Z'}]}]))
        self.service.backfill(self.root); self.service.backfill(self.root)
        restarted = TigerIntegration(self.root/'outbox.db', database_url='')
        self.assertEqual(restarted.progress()['pending'], 3)
        self.assertEqual(restarted.progress()['answers'], 1)
    def test_failed_remote_commit_keeps_outbox_and_retry_acks(self):
        self.service.database_url = 'configured'
        self.service.enqueue('detection', self.row())
        failed = FakeConnection(fail=True)
        with patch.object(self.service, '_connect', return_value=failed):
            with self.assertRaises(RuntimeError): self.service.sync_once()
        self.assertEqual(self.service.progress()['pending'], 1)
        remote = FakeConnection()
        with patch.object(self.service, '_connect', return_value=remote):
            self.assertEqual(self.service.sync_once(), 1)
        self.assertTrue(remote.committed)
        self.assertEqual(self.service.progress()['pending'], 0)
        inserts = [params for query,params in remote.cur.calls if 'INSERT INTO' in query]
        self.assertEqual(len(inserts), 1)
        self.assertTrue(any('time_bucket' in query for query,_ in remote.cur.calls))
        with patch.object(self.service, '_connect', side_effect=AssertionError('idle network')):
            self.assertEqual(self.service.sync_once(), 0)
    def test_no_credentials_means_no_network(self):
        self.service.enqueue('detection', self.row())
        with patch.object(self.service, '_connect', side_effect=AssertionError('network attempted')):
            self.assertEqual(self.service.sync_once(), 0)
        self.assertIsNone(self.service.progress()['accuracy'])


if __name__ == '__main__': unittest.main()
