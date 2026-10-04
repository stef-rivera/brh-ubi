import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from fastapi import HTTPException
from backend import feedback
from backend.state import state


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.patches = [patch.object(feedback, 'DIRECTORY', self.directory), patch.object(feedback, 'save_detection'), patch.object(feedback.bus, 'publish'), patch.object(feedback, 'current_question', return_value=None)]
        for item in self.patches: item.start()
        self.original = {'drive_id': 'drive-a', 'event_id': 'event-a', 'recognition_status': 'tentative', 'sign_id': 'yield', 'sign_text': 'YIELD', 'confidence': .31, 'source': 'local-specialist', 'box': [10, 10, 40, 40], 'ts_video': 2, 'audio_url': '/old.mp3', 'meaning': 'old'}
        with state.lock:
            self.old_state = (state.drive_id, state.mode, state.detections, state.practice_items, state.practice_index)
            state.drive_id = 'drive-a'; state.mode = 'driving'; state.detections = [copy.deepcopy(self.original)]

    def tearDown(self):
        with state.lock:
            state.drive_id, state.mode, state.detections, state.practice_items, state.practice_index = self.old_state
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def request(self, action, **kwargs):
        return feedback.apply_feedback(feedback.FeedbackBody(drive_id='drive-a', event_id='event-a', action=action, **kwargs))

    def test_correct_preserves_original_and_confidence_and_clears_audio(self):
        result = self.request('correct', sign_id='speed_limit', value=40)['row']
        self.assertEqual(result['sign_text'], 'SPEED LIMIT 40')
        self.assertEqual(result['confidence'], .31)
        self.assertEqual(result['original_prediction'], self.original)
        self.assertEqual(result['audio_url'], '')
        self.assertFalse(result['verified'])
        self.assertEqual(result['source'], 'human')
        self.request('correct', sign_id='stop')
        self.assertEqual(json.loads((self.directory/'records.json').read_text())[0]['original_prediction'], self.original)

    def test_removed_played_candidate_can_still_be_ignored(self):
        feedback.register_prediction(self.original)
        state.detections = []
        result = self.request('ignore')['row']
        self.assertEqual(result['feedback_action'], 'ignore')
        self.assertEqual(result['recognition_status'], 'excluded')
        self.assertEqual(state.detections[0]['event_id'], 'event-a')

    def test_pending_candidate_can_be_ignored_without_waiting(self):
        state.detections[0]['recognition_status'] = 'pending'
        self.assertEqual(self.request('ignore')['row']['recognition_status'], 'excluded')

    def test_exclude_and_undo(self):
        self.assertEqual(self.request('not_sign')['row']['recognition_status'], 'excluded')
        self.assertTrue(self.request('ignore')['row']['exclude_from_practice'])
        restored = self.request('undo')['row']
        self.assertEqual(restored['sign_id'], 'yield')
        self.assertFalse(restored['human_reviewed'])

    def test_stale_invalid_and_pending_do_not_write(self):
        with self.assertRaises(HTTPException): self.request('correct', sign_id='made-up')
        with self.assertRaises(HTTPException): self.request('correct', sign_id='speed_limit', value=43)
        self.assertFalse((self.directory/'records.json').exists())
        state.detections[0]['recognition_status'] = 'pending'
        with self.assertRaises(HTTPException) as caught: self.request('confirm')
        self.assertEqual(caught.exception.status_code, 409)
        state.drive_id = 'other'
        with self.assertRaises(HTTPException) as caught: self.request('confirm')
        self.assertEqual(caught.exception.status_code, 404)

    def test_parked_practice_rebuilds_and_excludes_removed(self):
        state.mode = 'parked'; state.practice_index = 4
        result = self.request('correct', sign_id='speed_limit', value=35)
        self.assertTrue(result['practice_reset'])
        self.assertEqual(state.practice_items[0]['value'], 35)
        self.assertEqual(state.practice_index, 0)
        self.request('not_sign')
        self.assertEqual(state.practice_items, [])

    def test_raw_images_and_same_clip_review_match(self):
        clip = self.directory / 'clip.mp4'; clip.write_bytes(b'original video')
        job = {'id': 'event-a', 'ts': 2, 'box': [10,10,40,40], 'crop': np.zeros((30,30,3), dtype=np.uint8), 'frame': np.zeros((80,80,3), dtype=np.uint8)}
        feedback.store_raw_example(job, 'drive-a', clip)
        feedback.register_prediction(self.original)
        self.request('correct', sign_id='stop')
        review = feedback.lookup_review(job, clip)
        self.assertEqual(review['sign_id'], 'stop')
        self.assertTrue(feedback.apply_review_fields(self.original, review)['feedback_replayed'])
        self.assertIsNone(feedback.lookup_review({**job, 'ts': 5}, clip))
        self.assertIsNone(feedback.lookup_review({**job, 'box': [60,60,80,80]}, clip))
        self.assertTrue(Path(feedback.feedback_image('event-a', 'drive-a').path).exists())
        clip.write_bytes(b'changed video')
        self.assertIsNone(feedback.lookup_review(job, clip))

    def test_undo_disables_replay(self):
        clip = self.directory / 'clip'; clip.write_bytes(b'video')
        job = {'id': 'event-a', 'ts': 2, 'box': [10,10,40,40]}
        feedback.store_raw_example(job, 'drive-a', clip)
        self.request('confirm'); self.request('undo')
        self.assertIsNone(feedback.lookup_review(job, clip))

    def test_replay_undo_keeps_current_identity_and_clears_origin(self):
        clip = self.directory / 'clip'; clip.write_bytes(b'video')
        job = {'id': 'event-a', 'ts': 2, 'box': [10,10,40,40]}
        feedback.store_raw_example(job, 'drive-a', clip)
        self.request('correct', sign_id='stop')
        review = feedback.lookup_review(job, clip)
        current = {**self.original, 'drive_id': 'drive-b', 'event_id': 'event-b', 'thumb_url': '/current.jpg'}
        replayed = feedback.apply_review_fields(current, review)
        feedback.register_prediction(replayed)
        state.drive_id = 'drive-b'; state.detections = [replayed]
        restored = feedback.apply_feedback(feedback.FeedbackBody(drive_id='drive-b', event_id='event-b', action='undo'))['row']
        self.assertEqual(restored['event_id'], 'event-b')
        self.assertEqual(restored['drive_id'], 'drive-b')
        self.assertEqual(restored['thumb_url'], '/current.jpg')
        self.assertEqual(restored['sign_id'], 'yield')
        self.assertIsNone(feedback.lookup_review(job, clip))

    def test_export_is_explicitly_not_detector_training_ready(self):
        from scripts.export_feedback import export
        clip = self.directory / 'clip'; clip.write_bytes(b'video')
        job = {'id': 'event-a', 'ts': 2, 'box': [10,10,40,40], 'crop': np.zeros((30,30,3), dtype=np.uint8)}
        feedback.store_raw_example(job, 'drive-a', clip)
        self.request('confirm')
        manifest = export(self.directory / 'export')
        self.assertFalse(manifest['training_ready'])
        self.assertEqual(len(manifest['examples']), 1)
        example = manifest['examples'][0]
        self.assertFalse(example['frame_objects_fully_reviewed'])
        self.assertTrue((self.directory/'export'/example['images']['crop']).is_file())

class ReplayMotionTests(unittest.TestCase):
    def test_actual_shifted_review_boxes_match(self):
        self.assertIsNotNone(feedback._review_distance({'ts_video':9.13,'box':[872,357,909,402]}, {'ts':9.21,'box':[901,346,942,399]}))
        self.assertIsNotNone(feedback._review_distance({'ts_video':56.72,'box':[842,308,902,370]}, {'ts':56.64,'box':[829,319,883,376]}))
        self.assertIsNotNone(feedback._review_distance({'ts_video':17.22,'box':[819,347,855,390]}, {'ts':17.30,'box':[836,337,876,385]}))

    def test_neighboring_sign_is_not_borrowed(self):
        self.assertIsNone(feedback._review_distance({'ts_video':56.31,'box':[792,346,834,390]}, {'ts':56.17,'box':[786,393,813,412]}))
        self.assertIsNone(feedback._review_distance({'ts_video':7.67,'box':[735,404,748,419]}, {'ts':7.44,'box':[869,319,919,367]}))

class TimestampReplayTests(unittest.TestCase):
    def test_same_entry_time_matches_different_best_frame(self):
        record={'ts_video':56.72,'original_prediction':{'first_seen_video':52.90},'box':[842,308,902,370]}
        self.assertIsNotNone(feedback._timestamp_distance(record,{'ts':54.5,'first_seen_video':53.0,'box':[730,380,755,408]}))
        self.assertIsNone(feedback._timestamp_distance(record,{'ts':56.3,'first_seen_video':54.4,'box':[78,405,152,433]}))
        self.assertIsNone(feedback._timestamp_distance(record,{'ts':56.2,'first_seen_video':56.1,'box':[786,393,813,412]}))

    def test_review_applies_before_later_closeup(self):
        record={'ts_video':20.7,'first_seen_video':15.4,'box':[1562,180,1685,413]}
        self.assertIsNotNone(feedback._timestamp_distance(record,{'ts':15.5,'first_seen_video':15.433,'box':[1180,390,1210,445]}))
        self.assertIsNone(feedback._timestamp_distance(record,{'ts':15.7,'first_seen_video':15.433,'box':[791,473,823,508]}))
        self.assertIsNone(feedback._timestamp_distance(record,{'ts':24,'first_seen_video':15.433,'box':[1562,180,1685,413]}))

    def test_saved_ignore_matches_earlier_track_fragment(self):
        record={'ts_video':15.27,'first_seen_video':13.766,'box':[1382,330,1434,345]}
        self.assertIsNotNone(feedback._timestamp_distance(record,{'ts':13.43,'first_seen_video':12.966,'box':[1234,402,1273,415]}))
        self.assertIsNone(feedback._timestamp_distance(record,{'ts':13.43,'first_seen_video':12.966,'box':[410,400,440,414]}))
