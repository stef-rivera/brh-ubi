import queue,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
import cv2
from backend.local_vision import LocalDetector,box_is_clearer,choose_cloud_reading,color_sign_boxes,interpret_text,last_kept_at,paint_look,reading_was_guessed,should_read_sign
from backend.tts import spanish_line
from backend.state import state

class LocalTests(unittest.TestCase):
 def setUp(self):
  state.drive_id='queue-test';state.detections=[];state.pipeline_metrics={}
  publisher=patch('backend.local_vision.bus.publish');publisher.start();self.addCleanup(publisher.stop)
 def detector(self,**kw):return LocalDetector(SimpleNamespace(ended=False),model=Mock(),**kw)
 def job(self,ident):return dict(id=ident,crop=np.zeros((50,50,3),dtype='uint8'),ts=1,box=[0,0,50,50],confidence=.9,labels={'speed limit sign':3},created=time.monotonic(),thumb='')
 def test_sure_sign_is_read_once_per_20_seconds(self):
  self.assertTrue(should_read_sign(17.0, .76, 25, None, None))
  self.assertFalse(should_read_sign(17.0, .6, 23, None, None))
  self.assertFalse(should_read_sign(18.0, .9, 40, None, 17.0))
  self.assertTrue(should_read_sign(37.1, .8, 30, None, 17.0))
 def test_a_slightly_clearer_board_is_read_again(self):
  self.assertTrue(box_is_clearer((39.5, 66), 39.6, 76))
 def test_a_guessed_word_is_sent_on_for_a_real_read(self):
  guessed=dict(sign_text='ROAD WORK AHEAD EXPECT DELAY')
  self.assertTrue(reading_was_guessed('ROAD NDEA AHEAD EXPECT DELAS', guessed))
  self.assertFalse(reading_was_guessed('SPEED UIMIT 40', dict(sign_text='SPEED LIMIT 40')))
 def test_grok_does_not_invent_school_or_one_way(self):
  self.assertIsNone(choose_cloud_reading([{'sign_id':'school_zone','sign_text':'SCHOOL','confidence':.9}],{'yellow sign':1}))
  self.assertIsNone(choose_cloud_reading([{'sign_id':'one_way','sign_text':'ONE WAY','confidence':.9}],{'red sign':1}))
 def test_grok_words_become_the_logged_sentence(self):
  found=choose_cloud_reading([{'sign_id':'road_work','sign_text':'ROAD WORK AHEAD EXPECT DELAYS','confidence':.8}],{'orange sign':1})
  self.assertEqual(found['sign_text'],'ROAD WORK AHEAD EXPECT DELAYS')
 def test_spanish_follows_the_logged_sign(self):
  self.assertIn('ROAD WORK', spanish_line('ROAD WORK AHEAD EXPECT DELAY','Hay obras mas adelante. Reduce la velocidad y mira el carril.'))
  self.assertNotIn('Detente por completo', spanish_line('SPEED LIMIT 40','No conduzcas mas rapido que el numero en el letrero.'))
 def test_failed_read_allows_a_closer_look(self):
  self.assertFalse(box_is_clearer((37.8, 24), 38.0, 26))
  self.assertTrue(box_is_clearer((37.8, 24), 39.5, 64))
  self.assertEqual(last_kept_at({'speed_limit': 17.2}, 'white sign'), 17.2)
  self.assertFalse(should_read_sign(36.0, .8, 40, None, 17.2))
 def test_repeated_sign_is_read_without_overlap(self):
  self.assertFalse(should_read_sign(16.2, .6, 28, None, None))
  self.assertTrue(should_read_sign(16.8, .6, 28, 16.2, None))
  self.assertFalse(should_read_sign(17.2, .6, 40, 16.8, 16.8))
 def _paint(self, frame, hsv, x, y, w, h):
  patch=np.zeros((h,w,3),dtype='uint8'); patch[:]=hsv
  frame[y:y+h, x:x+w]=cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
 def test_color_keeps_guide_signs_and_skips_street_blades_and_snow(self):
  frame=np.zeros((720,1280,3),dtype='uint8')
  self._paint(frame,(3,200,180),700,360,70,40)
  self._paint(frame,(60,180,140),200,360,50,18)
  self._paint(frame,(60,180,140),400,300,160,50)
  self._paint(frame,(0,10,230),100,640,500,70)
  names={box['name'] for box in color_sign_boxes(frame)}
  self.assertEqual(names, {'orange sign','green sign'})
 def test_green_exit_is_kept_and_street_name_is_not(self):
  found=interpret_text([{'text':'EXIT 12','confidence':.9}],{'green sign':1})
  self.assertEqual(found['sign_text'],'EXIT 12')
  self.assertIsNone(interpret_text([{'text':'Pleasant Hill Rd','confidence':1}],{'green sign':1}))
 def test_one_letter_misses_are_filled_in(self):
  found=interpret_text([{'text':'AHEAD EYPECT DELAT','confidence':.9}],{'orange sign':1})
  self.assertEqual(found['sign_text'],'ROAD WORK AHEAD EXPECT DELAY')
  # The real crop starts with ROAD, which must not be thrown out as a street name.
  found=interpret_text([{'text':'ROAD NDEA AHEAD EXPECT DELAS','confidence':.9}],{'orange sign':1})
  self.assertEqual(found['sign_text'],'ROAD WORK AHEAD EXPECT DELAY')
  # The short tail is the same sign, still blurry. It must not count as the reading.
  self.assertIsNone(interpret_text([{'text':'EXPECT DELA','confidence':1}],{'orange sign':1}))
  self.assertIsNone(interpret_text([{'text':'HOAU NISX','confidence':.9}],{'orange sign':1}))
 def test_orange_sentence_is_kept(self):
  found=interpret_text([{'text':'LEFT LANE CLOSED','confidence':.9}],{'orange sign':1})
  self.assertEqual(found['sign_id'],'road_work')
  self.assertEqual(found['sign_text'],'LEFT LANE CLOSED')
 def test_street_names_are_ignored(self):
  self.assertIsNone(interpret_text([{'text':'Pleasant Hill Rd','confidence':1}],{}))
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
   a=self.job('a');a['box']=[0,0,80,80];a['labels']={'orange sign':1};d.publish_pending(a);d.jobs.put(a);self.assertTrue(entered.wait(1))
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
 def test_coach_uses_the_signs_from_this_drive(self):
  from backend.voice_coach import coach_instructions
  text=coach_instructions([
    {'recognition_status':'resolved','sign_text':'SPEED LIMIT 40','sign_id':'speed_limit'},
    {'recognition_status':'pending','sign_text':'Reading sign…'},
    {'recognition_status':'resolved','sign_text':'ROAD WORK AHEAD EXPECT DELAYS','sign_id':'road_work'},
    {'recognition_status':'resolved','sign_text':'SPEED LIMIT 40','sign_id':'speed_limit'},
  ])
  self.assertIn('SPEED LIMIT 40', text)
  self.assertIn('ROAD WORK AHEAD EXPECT DELAYS', text)
  self.assertEqual(text.count('SPEED LIMIT 40'), 1)
  self.assertIn('actually logged', text)
 def test_coach_does_not_invent_signs_from_another_clip(self):
  from backend.voice_coach import coach_instructions
  stop=coach_instructions([{'recognition_status':'resolved','sign_text':'STOP','sign_id':'stop'}])
  self.assertIn('STOP', stop)
  self.assertNotIn('ROAD WORK', stop)
  self.assertNotIn('speed limit', stop.lower())
  empty=coach_instructions([])
  self.assertNotIn('ROAD WORK', empty)
  self.assertNotIn('40', empty)
 def test_glance_holds_a_box_then_lets_it_go(self):
  d=self.detector()
  d._remember('model', [[10,20,40,80]], 17.0)
  d._remember('color', [[100,100,160,140]], 17.2)
  self.assertEqual(len(d.glance(17.4)), 2)
  self.assertEqual(d.glance(19.0), [])
 def test_hitbox_is_an_outline_without_a_label(self):
  frame=np.zeros((100,120,3),dtype='uint8')
  paint_look(frame, [[10,20,40,50]])
  self.assertEqual(frame[20,10].tolist(), [255,255,255])
  self.assertEqual(frame[35,25].tolist(), [0,0,0])
 def test_speed_limit_is_spoken(self):
  d=self.detector();job=self.job('a');d.publish_pending(job)
  with patch('backend.local_vision.log_detection'),patch('backend.local_vision.bus.publish'):
   d.finish(job,dict(sign_id='speed_limit',sign_text='SPEED LIMIT 40',confidence=.9))
  queued=d.audio_jobs.get_nowait()
  self.assertEqual(queued[1],'Speed limit 40.')
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
