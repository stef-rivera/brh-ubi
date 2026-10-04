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
        self.assertEqual(result['sign_id'],'pedestrian_crossing');self.assertNotEqual(result['sign_id'],'bicycle_pedestrian_crossing')
    def test_overlap(self):
        self.assertEqual(overlap([0,0,10,10],[0,0,10,10]),1)

    def test_all_checkpoint_classes_have_catalog_mapping(self):
        from backend.catalog import get
        from backend.us_signs import MAP
        labels=list(MAP)+[f'{prefix}{value}' for prefix,value in [('speedLimit',50),('rampSpeedAdvisory',45),('schoolSpeedLimit',25),('truckSpeedLimit',55),('zoneAhead',25)]]
        for label in labels:
            result=resolve_votes([dict(label=label,confidence=.9)]*3)
            self.assertIsNotNone(get(result['sign_id']),label)
    def test_advisory_speed_remains_distinct_from_limit(self):
        result=resolve_votes([dict(label='rampSpeedAdvisory45',confidence=.9)]*3)
        self.assertEqual(result['sign_id'],'ramp_speed_advisory')
        self.assertEqual(result['value'],45)
