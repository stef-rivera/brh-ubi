import unittest
from backend.us_signs import resolve_votes,overlap

class SpecialistConsensusTests(unittest.TestCase):
    def test_stable_school(self):
        result=resolve_votes([dict(label='school',confidence=.8)]*3)
        self.assertEqual(result['sign_id'],'school_zone');self.assertFalse(result['tentative'])
    def test_ambiguous_is_tentative(self):
        result=resolve_votes([dict(label='school',confidence=.4)]*3)
        self.assertTrue(result['tentative'])
    def test_single_frame_cannot_announce(self):
        self.assertIsNone(resolve_votes([dict(label='school',confidence=.9)]))
    def test_numeric_speed(self):
        result=resolve_votes([dict(label='speedLimit40',confidence=.8)]*4)
        self.assertEqual(result['value'],40)
    def test_uncovered_combined_crossing_not_exact(self):
        result=resolve_votes([dict(label='pedestrianCrossing',confidence=.9)]*4)
        self.assertEqual(result['sign_id'],'unknown');self.assertTrue(result['tentative'])
    def test_overlap(self):
        self.assertEqual(overlap([0,0,10,10],[0,0,10,10]),1)
