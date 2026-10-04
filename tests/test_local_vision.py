import queue,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from backend.local_vision import LocalDetector,interpret_text
from backend.state import state

class LocalTests(unittest.TestCase):
 def setUp(self):
  state.drive_id='queue-test';state.detections=[];state.pipeline_metrics={}
  publisher=patch('backend.local_vision.bus.publish');publisher.start();self.addCleanup(publisher.stop)
 def detector(self,**kw):return LocalDetector(SimpleNamespace(ended=False),model=Mock(),**kw)
 def job(self,ident):return dict(id=ident,crop=np.zeros((50,50,3),dtype='uint8'),ts=1,box=[0,0,50,50],confidence=.9,labels={'speed limit sign':3},created=time.monotonic(),thumb='')
 def test_roadwork_ocr_fragment_is_not_street_name(self):
  self.assertIsNone(interpret_text([{'text':'ROAD #I AHEAD EAPEC','confidence':1}],{}))
  self.assertEqual(interpret_text([{'text':'Pleasant Hill Rd','confidence':1}],{})['sign_text'],'Pleasant Hill Rd')
 def test_number_requires_speed_evidence(self):
  self.assertIsNone(interpret_text([{'text':'40','confidence':1}],{}))
  self.assertEqual(interpret_text([{'text':'40','confidence':1}],{'speed limit sign':3})['value'],40)
 def test_bad_number_not_accepted(self):
  self.assertIsNone(interpret_text([{'text':'SPEED LIMIT 43','confidence':1}],{}))
 def test_live_reader_never_calls_cloud(self):
  cloud=Mock(side_effect=AssertionError('Cloud called'))
  d=self.detector(fallback=cloud,ocr=lambda c:[])
  t=threading.Thread(target=d.local_loop,daemon=True);t.start();d.threads.append(t)
  job=self.job('a');d.publish_pending(job);d.jobs.put(job)
  until=time.monotonic()+1
  while time.monotonic()<until and d.jobs.unfinished_tasks:time.sleep(.01)
  cloud.assert_not_called();self.assertEqual(d.stats['cloud_requests'],0)
  self.assertEqual(state.detections[0]['recognition_status'],'unresolved');d.stop()
 def test_reopened_unresolved_row_is_updated_not_duplicated(self):
  d=self.detector();job=self.job('a');d.publish_pending(job)
  with patch('backend.local_vision.log_detection'):d.finish(job,error='Unreadable')
  d.publish_pending(job)
  self.assertEqual(len(state.detections),1);self.assertEqual(d.stats['unresolved'],0)
  self.assertEqual(d.stats['pending'],1)
 def test_same_track_has_only_one_inflight_reading(self):
  d=self.detector();job=self.job('a')
  self.assertTrue(d.enqueue(job));self.assertFalse(d.enqueue(job))
  self.assertEqual(d.jobs.qsize(),1);self.assertEqual(d.stats['pending'],1)
  d.completed.add('b');self.assertFalse(d.enqueue(self.job('b')))
 def test_late_result_does_not_cross_drives(self):
  d=self.detector();job=self.job('a');d.publish_pending(job);state.drive_id='new-drive';state.detections=[]
  with patch('backend.local_vision.log_detection') as log:d.finish(job,dict(sign_id='stop',sign_text='STOP',confidence=.9));log.assert_not_called()
  self.assertEqual(state.detections,[])
 def test_slow_audio_does_not_block_recognition(self):
  d=self.detector();job=self.job('a');job['ocr']=[{'text':'STOP','confidence':.9}];d.publish_pending(job)
  with patch('backend.local_vision.log_detection'),patch('backend.local_vision.bus.publish'):
   d.finish(job,dict(sign_id='stop',sign_text='STOP',confidence=.9))
  self.assertEqual(d.audio_jobs.qsize(),1);self.assertEqual(state.detections[0]['recognition_status'],'resolved')
 def test_known_symbol_without_words_uses_catalog_title(self):
  d=self.detector();job=self.job('a');d.publish_pending(job)
  with patch('backend.local_vision.log_detection'):
   d.finish(job,dict(sign_id='school_zone',sign_text='',confidence=.95))
  self.assertEqual(state.detections[0]['sign_text'],'SCHOOL')
 def test_queues_are_bounded(self):
  d=self.detector();self.assertEqual(d.jobs.maxsize,8);self.assertEqual(d.audio_jobs.maxsize,8)
if __name__=='__main__':unittest.main()
