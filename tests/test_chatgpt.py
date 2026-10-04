from contextlib import nullcontext
import base64
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import numpy as np
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend import chatgpt_client as c
from backend.main import app


class ChatGPTTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = patch.object(c,'LOCAL',Path(self.temp.name)/'chatgpt')
        self.path.start()
        c._pending.clear()
        self.key = rsa.generate_private_key(public_exponent=65537,key_size=2048)

    def tearDown(self):
        self.path.stop()
        self.temp.cleanup()

    def begin(self, cid=None):
        url = c.begin_login('http://127.0.0.1:8000/auth/callback','browser',cid)
        return {k:v[0] for k,v in parse_qs(urlparse(url).query).items()}

    def finish(self, q, nonce=None, audience='oaiapp_test', scope=c.SCOPES):
        token=jwt.encode(dict(iss=c.AUTH,aud=audience,sub='subject',exp=time.time()+3600,nonce=nonce or q['nonce'],email='test@example.test'),self.key,algorithm='RS256',headers={'kid':'test'})
        response=httpx.Response(200,json=dict(access_token='test-access',refresh_token='test-refresh',id_token=token,expires_in=3600,scope=scope))
        with patch.object(c.httpx,'post',return_value=response),patch.object(c.jwt,'PyJWKClient') as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value=SimpleNamespace(key=self.key.public_key())
            c.finish_login(dict(state=q['state'],code='test-code',client_id='oaiapp_test'),'browser')

    def test_pkce_and_stable_host(self):
        q=self.begin(); a=c._pending[q['state']]
        expected=base64.urlsafe_b64encode(hashlib.sha256(a['verifier'].encode()).digest()).decode().rstrip('=')
        self.assertEqual(q['code_challenge'],expected)
        self.assertEqual(q['ext_agent_host_id'],self.begin()['ext_agent_host_id'])
        self.assertIn(c.DIRECT,q['scope'])

    def test_callback_cannot_cross_browser_sessions(self):
        q=self.begin()
        with patch.object(c.httpx,'post') as post,self.assertRaises(c.ChatGPTError):
            c.finish_login(dict(state=q['state'],code='code',client_id='oaiapp_test'),'other-browser')
        post.assert_not_called()

    def test_valid_signature_and_private_storage(self):
        q=self.begin();self.finish(q)
        self.assertTrue(c.status()['connected'])
        self.assertEqual((c.LOCAL/'accounts.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(c.LOCAL.stat().st_mode & 0o777,0o700)
        self.assertNotIn('test-access',json.dumps(c.status()))
        self.assertEqual(self.begin('oaiapp_test')['client_id'],'oaiapp_test')

    def test_wrong_nonce_rejected(self):
        with self.assertRaises(c.ChatGPTError):self.finish(self.begin(),nonce='wrong')
        self.assertFalse(c.status()['connected'])

    def test_wrong_audience_rejected(self):
        with self.assertRaises(c.ChatGPTError):self.finish(self.begin(),audience='other')

    def test_missing_permission_rejected(self):
        with self.assertRaises(c.ChatGPTError):self.finish(self.begin(),scope='openid profile email')

    def test_callback_is_single_use(self):
        q=self.begin();self.finish(q)
        with self.assertRaises(c.ChatGPTError):self.finish(q)

    def test_declined_login_does_not_exchange_code(self):
        q=self.begin()
        with patch.object(c.httpx,'post') as post,self.assertRaises(c.ChatGPTError):
            c.finish_login(dict(state=q['state'],error='access_denied'),'browser')
        post.assert_not_called()

    def test_refresh_rotates_saved_credentials(self):
        q=self.begin();self.finish(q)
        data=c._read();data['accounts']['oaiapp_test']['expires_at']=0;c._save(data)
        with patch.object(c.httpx,'post',return_value=httpx.Response(200,json=dict(access_token='new-access',refresh_token='new-refresh',expires_in=3600))):
            self.assertEqual(c.access_token(),'new-access')
        self.assertEqual(c._read()['accounts']['oaiapp_test']['refresh_token'],'new-refresh')

    def test_stream_requires_completed_event(self):
        payload=json.dumps({'signs':[]})
        stream='data: '+json.dumps({'type':'response.output_text.delta','delta':payload})+'\n\n'
        with patch.object(c,'access_token',return_value='test'),patch.object(c.httpx,'stream',return_value=nullcontext(httpx.Response(200,text=stream))),self.assertRaises(c.ChatGPTError):
            c.read_frame(np.zeros((20,20,3),dtype='uint8'),'test-model')

    def test_complete_stream_parses_box(self):
        payload=json.dumps({'signs':[{'sign_id':'stop','sign_text':'STOP','box_2d':[100,200,500,600],'confidence':.9}]})
        stream='data: '+json.dumps({'type':'response.output_text.delta','delta':payload})+'\n\ndata: '+json.dumps({'type':'response.completed'})+'\n\n'
        with patch.object(c,'access_token',return_value='test'),patch.object(c.httpx,'stream',return_value=nullcontext(httpx.Response(200,text=stream))) as request:
            signs=c.read_frame(np.zeros((100,200,3),dtype='uint8'),'test-model')
        self.assertEqual(signs[0]['box'],[40,10,120,50])
        self.assertEqual(request.call_args.kwargs['json']['store'],False)
        self.assertEqual(request.call_args.kwargs['json']['stream'],True)

    def test_rate_limit_is_not_empty_detection(self):
        with patch.object(c,'access_token',return_value='test'),patch.object(c.httpx,'stream',return_value=nullcontext(httpx.Response(429,text='private provider body'))),self.assertRaisesRegex(c.ChatGPTError,'usage limit'):
            c.read_frame(np.zeros((20,20,3),dtype='uint8'),'test-model')

    def test_bad_boxes_are_discarded(self):
        rows=[{'sign_id':'stop','box_2d':[100,200,50,100]}, {'sign_id':'stop','box_2d':[0,0,float('nan'),100]}]
        self.assertEqual(c.parse_signs(json.dumps({'signs':rows}),1280,720),[])

    def test_successful_model_test_persists_and_disconnect_clears_it(self):
        q=self.begin();self.finish(q)
        c.record_test('test-model')
        self.assertEqual(c.status()['tested_models'],['test-model'])
        with patch.object(c.httpx,'post',return_value=httpx.Response(200)):
            c.disconnect()
        self.assertFalse(c.status()['connected'])
        self.assertEqual(c.status()['tested_models'],[])

    def test_stopped_detector_discards_late_result(self):
        from backend.detector import GeminiDetector
        from backend.state import state
        state.drive_id='test-drive'
        d=GeminiDetector(SimpleNamespace(ended=False),provider='chatgpt')
        def late(frame):
            d._stop.set()
            return [dict(sign_id='stop',sign_text='STOP',box=[0,0,10,10],confidence=.9)]
        d.reader=late
        with patch('backend.detector.log_detection') as log:
            d._handle(np.zeros((20,20,3),dtype='uint8'),1)
            log.assert_not_called()

    def test_finished_video_does_not_call_model(self):
        from backend.detector import GeminiDetector
        from backend.state import state
        state.drive_id='test-finished'
        reader=unittest.mock.Mock()
        d=GeminiDetector(SimpleNamespace(ended=True),reader=reader,provider='chatgpt')
        with patch('backend.detector.bus.publish'):
            d._loop()
        reader.assert_not_called()
        self.assertEqual(state.detection_status,'completed')

    def test_routes_block_cross_origin_and_untested_drive(self):
        with TestClient(app,base_url='http://127.0.0.1:8000') as web:
            r=web.post('/api/chatgpt/login',json={},headers={'Origin':'https://evil.example'})
            self.assertEqual(r.status_code,403)
            self.assertEqual(web.post('/api/drive/start',json={'detector':'chatgpt','model':'test-model'}).status_code,400)
            r=web.post('/api/chatgpt/login',json={})
            self.assertEqual(r.status_code,200)
            self.assertIn('HttpOnly',r.headers['set-cookie'])


if __name__=='__main__':unittest.main()
