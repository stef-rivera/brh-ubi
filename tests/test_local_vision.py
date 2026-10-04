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
 def test_number_requires_speed_evidence(self):
  self.assertIsNone(interpret_text([{'text':'40','confidence':1}],{}))
  self.assertEqual(interpret_text([{'text':'40','confidence':1}],{'speed limit sign':3})['value'],40)
 def test_bad_number_not_accepted(self):
  self.assertIsNone(interpret_text([{'text':'SPEED LIMIT 43','confidence':1}],{}))
 def test_local_reader_progresses_while_cloud_is_blocked(self):
  entered=threading.Event();release=threading.Event()
  def cloud(crop):entered.set();release.wait(2);return []
  count=iter([[],[{'text':'SPEED LIMIT 40','confidence':1}]])
  d=self.detector(fallback=cloud,ocr=lambda c:next(count))
  with patch('backend.local_vision.log_detection'),patch('backend.local_vision.bus.publish'):
   for target in (d.local_loop,d.cloud_loop):
    t=threading.Thread(target=target,daemon=True);t.start();d.threads.append(t)
   a=self.job('a');d.publish_pending(a);d.jobs.put(a);self.assertTrue(entered.wait(1))
   b=self.job('b');d.publish_pending(b);d.jobs.put(b)
   until=time.monotonic()+1
   while time.monotonic()<until and not any(r.get('sign_id')=='speed_limit' for r in state.detections):time.sleep(.01)
   self.assertTrue(any(r.get('sign_id')=='speed_limit' for r in state.detections))
   self.assertTrue(any(r.get('event_id')=='a' and r['recognition_status']=='pending' for r in state.detections))
   release.set();d.stop()
 def test_late_result_does_not_cross_drives(self):
  d=self.detector();job=self.job('a');d.publish_pending(job);state.drive_id='new-drive';state.detections=[]
  with patch('backend.local_vision.log_detection') as log:d.finish(job,dict(sign_id='stop',sign_text='STOP',confidence=.9));log.assert_not_called()
  self.assertEqual(state.detections,[])
 def test_slow_audio_does_not_block_recognition(self):
  d=self.detector();job=self.job('a');d.publish_pending(job)
  with patch('backend.local_vision.log_detection'),patch('backend.local_vision.bus.publish'):
   d.finish(job,dict(sign_id='stop',sign_text='STOP',confidence=.9))
  self.assertEqual(d.audio_jobs.qsize(),1);self.assertEqual(state.detections[0]['recognition_status'],'resolved')
 def test_known_symbol_without_words_uses_catalog_title(self):
  d=self.detector();job=self.job('a');d.publish_pending(job)
  with patch('backend.local_vision.log_detection'):
   d.finish(job,dict(sign_id='school_zone',sign_text='',confidence=.95))
  self.assertEqual(state.detections[0]['sign_text'],'SCHOOL')
 def test_queues_are_bounded(self):
  d=self.detector();self.assertEqual(d.jobs.maxsize,8);self.assertEqual(d.cloud_jobs.maxsize,6);self.assertEqual(d.audio_jobs.maxsize,8)
if __name__=='__main__':unittest.main()
