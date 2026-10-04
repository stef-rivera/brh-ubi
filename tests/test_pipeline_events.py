import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
import numpy as np
from backend.local_vision import LocalDetector
from backend.recognition_cache import RecognitionCache
from backend.state import state

class PipelineEventTests(unittest.TestCase):
 def setUp(self):
  state.drive_id='event-tests';state.detections=[]
  self.publisher=patch('backend.local_vision.bus.publish');self.bus=self.publisher.start();self.addCleanup(self.publisher.stop)
  self.log=patch('backend.local_vision.log_detection');self.log.start();self.addCleanup(self.log.stop)
 def make(self,voice=None):
  d=LocalDetector(SimpleNamespace(ended=False),model=Mock(),voice=voice or (lambda text:'/audio/test.mp3'))
  job=dict(id='one',crop=np.zeros((50,50,3),dtype='uint8'),ts=1,box=[0,0,50,50],confidence=.9,labels={},created=time.monotonic(),thumb='')
  d.publish_pending(job);return d,job
 def test_speed_sign_is_announced(self):
  d,job=self.make();d.finish(job,dict(sign_id='speed_limit',sign_text='SPEED LIMIT 40',confidence=.9,value=40))
  self.assertEqual(d.audio_jobs.qsize(),1)
  self.assertEqual(state.detections[0]['audio_status'],'queued')
  self.assertIn('40',state.detections[0]['audio_text'])
 def test_cached_text_gains_new_catalog_meaning(self):
  d,job=self.make();d.finish(job,dict(sign_id='unknown',sign_text='NO TURN ON RED',confidence=.89),source='local+cached-chatgpt')
  self.assertEqual(state.detections[0]['sign_id'],'no_turn_on_red')
  self.assertEqual(d.audio_jobs.qsize(),1)
 def test_stale_audio_is_not_announced(self):
  d,job=self.make();job['created']=time.monotonic()-4
  d.finish(job,dict(sign_id='stop',sign_text='STOP',confidence=.9))
  import threading
  t=threading.Thread(target=d.audio_loop,daemon=True);t.start()
  until=time.monotonic()+1
  while time.monotonic()<until and d.audio_jobs.unfinished_tasks:time.sleep(.01)
  self.assertEqual(state.detections[0]['audio_status'],'skipped')
  self.assertEqual(d.stats['audio_ready'],0);d.stop();t.join(1)
 def test_failed_voice_is_visible_and_does_not_count_ready(self):
  d,job=self.make(voice=lambda text:'')
  d.finish(job,dict(sign_id='speed_limit',sign_text='SPEED LIMIT 40',confidence=.9,value=40))
  # Stop loop after one processed job without suppressing its result.
  def empty_voice(text):
   return ''
  import threading
  t=threading.Thread(target=d.audio_loop,daemon=True);t.start()
  until=time.monotonic()+1
  while time.monotonic()<until and d.audio_jobs.unfinished_tasks:time.sleep(.01)
  self.assertEqual(state.detections[0]['audio_status'],'failed')
  self.assertEqual(d.stats['audio_ready'],0);self.assertEqual(d.stats['audio_failed'],1)
  d.stop();t.join(1)
 def test_park_marks_pending_reading_cancelled(self):
  d,job=self.make();d.stop()
  self.assertEqual(state.detections[0]['recognition_status'],'unresolved')
  self.assertIn('stopped',state.detections[0]['recognition_error'])
 def test_cache_requires_same_video_time_and_box(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder);video=path/'clip.mp4';video.write_bytes(b'clip-a')
   cache=RecognitionCache(str(video),path/'cache.json')
   job={'ts':1.0,'box':[10,10,50,50]};found={'sign_id':'school_zone','confidence':.95,'sign_text':'SCHOOL'}
   cache.save(job,found)
   self.assertEqual(cache.lookup({'ts':1.1,'box':[11,11,51,51]})['sign_id'],'school_zone')
   self.assertIsNone(cache.lookup({'ts':3,'box':[10,10,50,50]}))
   video.write_bytes(b'clip-b')
   self.assertIsNone(RecognitionCache(str(video),path/'cache.json').lookup(job))
if __name__=='__main__':unittest.main()
