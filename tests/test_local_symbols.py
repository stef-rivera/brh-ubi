import unittest
from backend.local_symbols import agree,LABELS
class SymbolTests(unittest.TestCase):
 def row(self,index,top=.38,second=.30):
  values=[.1]*len(LABELS);values[index]=top;values[-1]=second;return values
 def test_single_view_cannot_resolve(self):self.assertIsNone(agree([self.row(0)]))
 def test_conflicting_views_abstain(self):self.assertIsNone(agree([self.row(0),self.row(1)]))
 def test_two_supported_views_resolve(self):self.assertEqual(agree([self.row(1),self.row(1)])['sign_id'],'bicycle_pedestrian_crossing')
 def test_business_distractor_abstains(self):self.assertIsNone(agree([self.row(8),self.row(8)]))
 def test_small_margin_abstains(self):self.assertIsNone(agree([self.row(0,.38,.37)]*2))

class DemoNearestLabelTests(unittest.TestCase):
    def test_low_similarity_still_has_honest_demo_guess(self):
        import numpy as np
        from backend.local_symbols import LocalSymbolClassifier
        classifier=LocalSymbolClassifier.__new__(LocalSymbolClassifier)
        classifier._scores=lambda crops:[[.1]*len(LABELS) for _ in crops]
        result=classifier([np.zeros((12,12,3),dtype=np.uint8)])
        self.assertIsNotNone(result)
        self.assertTrue(result['demo_guess']);self.assertTrue(result['tentative'])
        self.assertEqual(result['symbol_similarity'],.1)
