import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock
from fastapi import HTTPException
from backend import voice_coach, main
from backend.state import state

class VoiceCoachTests(unittest.TestCase):
    def test_corrected_signs_only_and_ignored_signs_removed(self):
        rows=[{'recognition_status':'resolved','sign_text':'NEXT EXIT','meaning':'Use the next exit for the named destination.'}, {'recognition_status':'resolved','sign_text':'NEXT EXIT'}, {'recognition_status':'resolved','sign_text':'STOP','feedback_action':'ignore'}, {'recognition_status':'pending','sign_text':'YIELD'}, {'recognition_status':'resolved','sign_text':'SPEED LIMIT 40','exclude_from_practice':True}]
        lines=voice_coach.logged_sign_lines(rows)
        self.assertEqual(lines,['NEXT EXIT — Use the next exit for the named destination.'])
        self.assertNotIn('STOP',voice_coach.coach_instructions(rows))

    def test_missing_credential_does_not_make_network_call(self):
        with patch.object(voice_coach,'settings',SimpleNamespace(xai_api_key=None)), patch.object(voice_coach.httpx,'post') as call:
            with self.assertRaises(RuntimeError):voice_coach.mint_voice_session([])
            call.assert_not_called()

    def test_only_ephemeral_token_is_returned_to_browser(self):
        response=Mock();response.json.return_value={'value':'temporary-token','expires_at':123}
        with patch.object(voice_coach,'settings',SimpleNamespace(xai_api_key='server-key')),patch.object(voice_coach.httpx,'post',return_value=response):
            result=voice_coach.mint_voice_session([])
        self.assertEqual(result['token'],'temporary-token')
        self.assertNotIn('server-key',str(result))

    def test_voice_route_requires_park_and_reports_unavailable(self):
        old_mode=state.mode
        try:
            state.mode='driving'
            with self.assertRaises(HTTPException) as error:main.practice_voice()
            self.assertEqual(error.exception.status_code,400)
            state.mode='parked'
            with patch.object(main,'mint_voice_session',side_effect=RuntimeError('private failure')):
                with self.assertRaises(HTTPException) as error:main.practice_voice()
                self.assertEqual(error.exception.status_code,503)
                self.assertNotIn('private failure',error.exception.detail)
        finally:state.mode=old_mode
