import asyncio
from types import SimpleNamespace
import threading

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from backend import photon


def run(coroutine):
    return asyncio.run(coroutine)


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(photon, 'SESSION_PATH', tmp_path / 'sessions.json')
    monkeypatch.setattr(photon, '_sessions', {})
    monkeypatch.setattr(photon, '_lock', asyncio.Lock())
    monkeypatch.setenv('PHOTON_BRIDGE_TOKEN', 'test-only-token')
    monkeypatch.setenv('PHOTON_TEST_RECIPIENT', '+15551234567')
    monkeypatch.setenv('PHOTON_PROJECT_ID', 'test-project')
    monkeypatch.setenv('PHOTON_PROJECT_SECRET', 'test-secret')
    row=dict(event_id='event-1',sign_id='stop',sign_text='STOP',recognition_status='resolved',
             safety_critical=True,thumb_url='',human_reviewed=True)
    st=SimpleNamespace(lock=threading.Lock(),mode='parked',drive_id='drive-test',
                       detections=[row],practice_index=3)
    monkeypatch.setattr(photon,'state',st)
    sent=[]
    async def bridge(path,payload=None):
        if path=='/health': return {'connected':True,'status':'ready'}
        sent.append(payload)
        return {'accepted':True}
    monkeypatch.setattr(photon,'_bridge',bridge)
    answers=[]
    monkeypatch.setattr(photon,'log_answer',answers.append)
    return st,sent,answers


def start():
    return run(photon.start(photon.StartRequest(drive_id='drive-test')))


def answer(session, text='stop', message='reply-1', index=0, sender='+15551234567'):
    return run(photon.reply(photon.ReplyRequest(session_id=session,message_id=message,
       question_index=index,sender=sender,answer=text),authorization='Bearer test-only-token'))


class MonkeyPatch:
    def __init__(self): self.patchers=[]
    def setattr(self, target, name, value):
        p=patch.object(target,name,value);p.start();self.patchers.append(p)
    def setenv(self, name, value):
        p=patch.dict("os.environ",{name:value});p.start();self.patchers.append(p)
    def undo(self):
        for p in reversed(self.patchers):p.stop()


class PhotonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.monkeypatch=MonkeyPatch()
        self.fixture=setup(self.monkeypatch,Path(self.temp.name))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.monkeypatch.undo)

    def test_start_snapshot_and_duplicate_preserve_browser_quiz(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        st,sent,_=setup
        first=start(); second=start()
        assert first['session_id']==second['session_id']
        assert len(sent)==1 and first['total']==1
        assert 'Reply with your answer' in sent[0]['text']
        assert st.practice_index==3
    
    
    def test_excluded_and_pending_never_practiced(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        st,sent,_=setup
        st.detections=[dict(st.detections[0],feedback_action='ignore'),
                       dict(st.detections[0],recognition_status='pending')]
        with self.assertRaises(HTTPException) as error: start()
        assert error.exception.status_code==409 and not sent
    
    
    def test_reply_grades_completes_and_deduplicates(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        _,sent,answers=setup
        session=start()['session_id']
        first=answer(session,'come to a complete stop')
        duplicate=answer(session,'come to a complete stop')
        assert first['status']=='completed' and first['grade']=='correct'
        assert duplicate['duplicate'] and len(answers)==1 and len(sent)==2
        assert answers[0]['channel']=='photon'
        assert 'Practice complete' in sent[-1]['text']
    
    
    def test_wrong_answer_advances_independent_session(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        st,sent,answers=setup
        st.detections.append(dict(event_id='event-2',sign_id='yield',sign_text='YIELD',
                                 recognition_status='resolved'))
        session=start()['session_id']
        response=answer(session,'nothing')
        assert response['index']==2 and response['status']=='waiting_reply'
        assert response['grade']=='incorrect' and len(sent)==3
        assert answers[0]['grade']=='incorrect' and st.practice_index==3
    
    
    def test_driving_and_stale_drive_block_start(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        st,_,_=setup
        for mode,drive in [('driving','drive-test'),('parked','new-drive')]:
            st.mode=mode;st.drive_id=drive
            with self.assertRaises(HTTPException) as error: start()
            assert error.exception.status_code==409
    
    
    def test_resuming_drive_blocks_inbound(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        st,sent,answers=setup
        session=start()['session_id'];st.mode='driving'
        with self.assertRaises(HTTPException) as error: answer(session,'come to a stop')
        assert error.exception.status_code==409 and not answers and len(sent)==1
    
    
    def test_auth_and_recipient_restrictions(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        with self.assertRaises(HTTPException) as error:
            run(photon.start(photon.StartRequest(drive_id='drive-test',recipient='+15557654321')))
        assert error.exception.status_code==403
        session=start()['session_id']
        with self.assertRaises(HTTPException) as error: answer(session,'come to a stop',sender='+15557654321')
        assert error.exception.status_code==404
        with self.assertRaises(HTTPException) as error:
            run(photon.reply(photon.ReplyRequest(session_id=session,message_id='r',sender='+15551234567',answer='x',question_index=0),authorization='Bearer wrong'))
        assert error.exception.status_code==401
    
    
    def test_stop_does_not_grade(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        _,sent,answers=setup
        response=answer(start()['session_id'],'STOP')
        assert response['status']=='stopped' and not answers and len(sent)==1
    
    
    def test_delivery_failure_retries_same_outbox(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        _,sent,_=setup
        async def failed(path,payload=None):
            if path=='/health': return {'connected':True}
            raise HTTPException(503,'failure')
        original=photon._bridge
        monkeypatch.setattr(photon,'_bridge',failed)
        with self.assertRaises(HTTPException): start()
        photon._load(); session=next(iter(photon._sessions.values()))
        key=session['outbox'][0]['idempotency_key']
        monkeypatch.setattr(photon,'_bridge',original)
        result=start()
        assert result['session_id']==session['session_id']
        assert sent[0]['idempotency_key']==key
    
    
    def test_question_index_rejects_stale_reply(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        _,sent,answers=setup
        session=start()['session_id']
        response=answer(session,'anything',index=1)
        assert response['ignored'] and not answers and len(sent)==1
    
    
    def test_missing_config_health_honest(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        monkeypatch.setenv('PHOTON_BRIDGE_TOKEN','')
        result=run(photon.integration_status())
        assert not result['configured'] and not result['connected'] and result['status']=='needs_setup'
    
    
    def test_thumbnail_path_cannot_escape_repo(self):
        setup=self.fixture
        monkeypatch=self.monkeypatch
        assert photon._image({'thumb_url':'/thumbs/../../.env'}) is None
        assert photon._image({'thumb_url':'https://example.com/sign.jpg'}) is None
