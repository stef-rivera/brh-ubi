import unittest
from backend.catalog import driving_announcement, get
from backend.local_vision import interpret_text, driving_audio_allowed, is_street_name

class DirectionCueTests(unittest.TestCase):
    def test_text_selects_precise_direction(self):
        for text,sign in [('SECOND RIGHT','second_right'),('2ND RIGHT','second_right'),('2 RIGHT','second_right'),('FUEL NEXT EXIT','next_exit')]:
            self.assertEqual(interpret_text([{'text':text,'confidence':.95}],{})['sign_id'],sign)
            self.assertTrue(driving_audio_allowed({'sign_id':sign,'sign_text':text},'data/night-drive.mp4'))
    def test_cues_direct_attention_to_upcoming_signs(self):
        self.assertEqual(driving_announcement('second_right'),'Second right ahead. Watch for the upcoming turns.')
        self.assertEqual(driving_announcement('next_exit'),'Next exit coming up. Watch for the exit signs.')
        self.assertTrue(get('next_exit')['questions'])
        self.assertFalse(is_street_name('SECOND RIGHT TO MAIN ROAD'))
    def test_sign_color_does_not_assign_an_exit(self):
        self.assertIsNone(interpret_text([{'text':'BLUE','confidence':.95}],{}))
