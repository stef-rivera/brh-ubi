import time,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import cv2,numpy as np
from backend.local_vision import LocalDetector,is_street_name,street_blade_reason
from backend.local_symbols import LocalSymbolClassifier,LABELS
from backend.state import state

class StreetFilterTests(unittest.TestCase):
 def job(self,crop=None):return dict(id='a',box=[0,0,120,25],crop=crop if crop is not None else np.zeros((25,120,3),np.uint8))
 def test_street_suffixes(self):
  for text in ['Pleasant Hill Rd','Merrimac St','Oak Avenue','Main Street','Cedar Ln']:
   self.assertTrue(is_street_name(text),text)
 def test_guide_and_construction_messages_are_kept(self):
  for text in ['MAIN ST EXIT 23','OAK ROAD NORTH','ROAD WORK AHEAD','LEFT LANE CLOSED','ONE WAY']:
   self.assertFalse(is_street_name(text),text)
 def test_tiny_green_blade_without_readable_text(self):
  crop=cv2.cvtColor(np.full((25,120,3),(60,180,130),np.uint8),cv2.COLOR_HSV2BGR)
  self.assertTrue(street_blade_reason(self.job(crop),[]))
  self.assertFalse(street_blade_reason(self.job(crop),[dict(text='EXIT 23',confidence=.9)]))
 def test_small_approaching_blade_with_squareish_proposal(self):
  crop=cv2.cvtColor(np.full((24,36,3),(60,180,130),np.uint8),cv2.COLOR_HSV2BGR)
  job=self.job(crop);job['box']=[828,359,852,371]
  self.assertTrue(street_blade_reason(job,[]))
 def test_large_green_guide_and_white_regulatory_sign_kept(self):
  crop=cv2.cvtColor(np.full((70,300,3),(60,180,130),np.uint8),cv2.COLOR_HSV2BGR)
  job=self.job(crop);job['box']=[0,0,300,70]
  self.assertFalse(street_blade_reason(job,[]))
  self.assertFalse(street_blade_reason(self.job(np.full((25,120,3),255,np.uint8)),[]))
 def test_filtered_track_removed_without_speech_and_not_retried(self):
  state.drive_id='filter';state.detections=[dict(event_id='a',recognition_status='pending')]
  detector=LocalDetector(SimpleNamespace(ended=False),model=Mock())
  detector.stats['pending']=1
  with patch('backend.local_vision.bus.publish'):
   detector.drop_irrelevant(self.job(),'Street-name blade')
  self.assertEqual(state.detections,[]);self.assertEqual(detector.stats['pending'],0)
  self.assertEqual(detector.stats['filtered_street_names'],1)
  self.assertIn('a',detector.completed);self.assertEqual(detector.audio_jobs.qsize(),0)
 def test_business_negative_wins_before_forced_guess(self):
  classifier=LocalSymbolClassifier.__new__(LocalSymbolClassifier)
  scores=[.2]*len(LABELS);scores[8]=.30;scores[3]=.27
  classifier._scores=lambda crops:[scores]*len(crops)
  self.assertIn('business',classifier([np.zeros((30,30,3),np.uint8)])['irrelevant_reason'])
 def test_blurry_traffic_candidate_still_gets_demo_guess(self):
  classifier=LocalSymbolClassifier.__new__(LocalSymbolClassifier)
  scores=[.2]*len(LABELS);scores[9]=.30;scores[3]=.27
  classifier._scores=lambda crops:[scores]*len(crops)
  self.assertTrue(classifier([np.zeros((30,30,3),np.uint8)])['demo_guess'])

class BusinessOCRTests(unittest.TestCase):
 def test_price_board_and_business_text_are_filtered(self):
  from backend.local_symbols import business_sign_reason
  for text in ['Sinclair GASOLINE CASH 4.299','PIZZA FACTORY','SOLAR HEAT AIR']:
   self.assertTrue(business_sign_reason([dict(text=text)]))
  for text in ['SPEED LIMIT 50','FREEWAY ENTRANCE','CAMPING EXIT 4']:
   self.assertFalse(business_sign_reason([dict(text=text)]))
