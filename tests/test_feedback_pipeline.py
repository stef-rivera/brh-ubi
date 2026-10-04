import tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from backend import feedback
from backend.local_vision import LocalDetector
from backend.state import state

class FeedbackPipelineTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  for item in [patch.object(feedback,'DIRECTORY',Path(self.temp.name)),patch('backend.local_vision.bus.publish'),patch('backend.local_vision.log_detection')]:
   item.start();self.addCleanup(item.stop)
  state.drive_id='feedback-pipeline';state.detections=[]
  self.d=LocalDetector(SimpleNamespace(ended=False),model=Mock(),ocr=lambda crop:[])
  self.job=dict(id='sign',ts=1,box=[0,0,40,40],crop=np.zeros((40,40,3),dtype='uint8'),confidence=.4,thumb='',created=time.monotonic())
  self.d.publish_pending(self.job)
 def test_late_machine_result_cannot_overwrite_human(self):
  row=state.detections[0];row.update(sign_id='stop',sign_text='STOP',recognition_status='resolved',human_reviewed=True,source='human')
  self.d.finish(self.job,dict(sign_id='speed_limit',sign_text='SPEED LIMIT 40',value=40,confidence=.9))
  self.assertEqual(row['sign_id'],'stop');self.assertEqual(self.d.audio_jobs.qsize(),0)
  self.assertFalse(self.d.enqueue(self.job));self.assertFalse(self.d.can_announce(self.job))
 def test_reviewed_replay_preserves_current_identity_and_can_announce(self):
  review={'action':'correct','record_key':'saved','row':{'sign_id':'stop','sign_text':'STOP','recognition_status':'resolved','feedback_action':'correct','printed_text':'STOP'},'original_prediction':{'sign_id':'yield'}}
  self.d.finish_review(self.job,review)
  row=state.detections[0]
  self.assertEqual(row['event_id'],'sign');self.assertEqual(row['source'],'human-replay')
  self.assertTrue(row['human_reviewed']);self.assertEqual(self.d.audio_jobs.qsize(),1)
  self.assertEqual(self.d.stats['resolved'],1);self.assertEqual(self.d.stats['pending'],0)

 def test_repeat_of_same_saved_review_is_removed_without_second_cue(self):
  state.detections.append({'event_id':'earlier','feedback_record_key':'saved','human_reviewed':True})
  self.d.finish_review(self.job,{'record_key':'saved','row':{'sign_id':'stop'}})
  self.assertEqual([r['event_id'] for r in state.detections],['earlier'])
  self.assertEqual(self.d.audio_jobs.qsize(),0)

 def test_symbol_without_printed_words_has_no_driving_audio(self):
  self.d.video.source='data/night-drive.mp4'
  for sign_id in ['school_zone','lane_ends']:
   state.detections=[];self.d.publish_pending(self.job)
   self.d.finish(self.job,{'sign_id':sign_id,'sign_text':sign_id,'tentative':True})
   self.assertEqual(self.d.audio_jobs.qsize(),0)
   self.assertEqual(state.detections[0]['audio_status'],'skipped')
 def test_reviewed_symbol_is_also_silent(self):
  self.d.video.source='data/night-drive.mp4'
  self.d.finish_review(self.job,{'record_key':'saved','row':{'sign_id':'school_zone','recognition_status':'resolved','printed_text':''}})
  self.assertEqual(self.d.audio_jobs.qsize(),0)
  self.assertEqual(state.detections[0]['audio_status'],'skipped')

 def test_lane_ends_stays_silent_even_with_ocr_words(self):
  self.d.video.source='data/night-drive.mp4'
  self.job['ocr']=[{'text':'MERGE','confidence':.99}]
  for sign_id in ['lane_ends']:
   state.detections=[];self.d.publish_pending(self.job)
   self.d.finish(self.job,{'sign_id':sign_id,'sign_text':sign_id,'tentative':True})
   self.assertEqual(self.d.audio_jobs.qsize(),0)
   self.assertEqual(state.detections[0]['audio_status'],'skipped')

 def test_daytime_symbols_keep_previous_audio_behavior(self):
  self.d.video.source='data/drive.mp4'
  self.d.finish_review(self.job,{'record_key':'saved','row':{'sign_id':'school_zone','recognition_status':'resolved'}})
  self.assertEqual(self.d.audio_jobs.qsize(),1)
 def test_night_text_sign_speaks_without_ocr_readability_requirement(self):
  self.d.video.source='data/night-drive.mp4'
  self.d.finish(self.job,{'sign_id':'stop','sign_text':'STOP','confidence':.9})
  self.assertEqual(self.d.audio_jobs.qsize(),1)

 def test_night_cues_have_time_to_wait_for_previous_speech(self):
  self.d.video.source='data/night-drive.mp4'
  self.job['created']=time.monotonic()-4
  self.assertTrue(self.d.relevant(self.job))
  self.assertEqual(self.d.audio_window(),8)
  self.d.video.source='data/drive.mp4'
  self.assertFalse(self.d.relevant(self.job))
  self.assertEqual(self.d.audio_window(),2)

 def test_saved_ignore_prevents_audio_queue(self):
  review={'action':'ignore','record_key':'saved-ignore','row':{'recognition_status':'excluded','feedback_action':'ignore','exclude_from_practice':True}}
  with patch('backend.local_vision.lookup_review',return_value=review):
   self.d.queue_announcement(self.job,'No turn on red.')
  self.assertEqual(self.d.audio_jobs.qsize(),0)
  self.assertEqual(state.detections[0]['recognition_status'],'excluded')

 def test_requested_yellow_sign_audio_is_restored_at_night(self):
  from backend.local_vision import driving_audio_allowed
  for sign_id in ['deer_crossing','merge','keep_right','thru_merge_left']:
   self.assertTrue(driving_audio_allowed({'sign_id':sign_id},'data/night-drive.mp4'))
