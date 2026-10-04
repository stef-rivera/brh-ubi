"""Loopback-only ChatGPT sign-in and a bounded one-frame test."""
import secrets
import time
from urllib.parse import urlencode

import cv2
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend import chatgpt_client as client
from backend.config import ROOT, settings

router = APIRouter()



def _local(request: Request, write=False):
    if request.url.hostname not in ('127.0.0.1','localhost') or request.client.host not in ('127.0.0.1','::1','testclient'):
        raise HTTPException(403,'ChatGPT sign-in is only available on this computer.')
    if write:
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403,'Open this action from the local app.')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403,'Open this action from the local app.')


class LoginBody(BaseModel):
    account_id: str | None = None


class TestBody(BaseModel):
    model: str


@router.get('/api/chatgpt/status')
def status(request: Request):
    _local(request)
    return client.status()


@router.post('/api/chatgpt/login')
def login(body: LoginBody, request: Request):
    _local(request,True)
    from backend.main import _stop_pipeline
    _stop_pipeline()
    session = request.cookies.get('chatgpt_login') or secrets.token_urlsafe(32)
    redirect = f'http://127.0.0.1:{request.url.port or 8000}/auth/callback'
    try:
        url = client.begin_login(redirect,session,body.account_id)
    except client.ChatGPTError as exc:
        raise HTTPException(400,str(exc)) from None
    from fastapi.responses import JSONResponse
    response = JSONResponse({'url':url})
    response.set_cookie('chatgpt_login',session,httponly=True,samesite='lax',max_age=600)
    response.headers['Cache-Control']='no-store'
    return response


@router.get('/auth/callback')
def callback(request: Request):
    _local(request)
    try:
        client.finish_login(dict(request.query_params),request.cookies.get('chatgpt_login',''))
        destination = '/?chatgpt=connected'
    except client.ChatGPTError as exc:
        destination = '/?' + urlencode({'chatgpt':'failed','message':str(exc)})
    response = RedirectResponse(destination,status_code=303)
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    response.delete_cookie('chatgpt_login')
    return response


@router.get('/api/chatgpt/models')
def models(request: Request):
    _local(request)
    try:
        return {'models':client.models()}
    except client.ChatGPTError as exc:
        raise HTTPException(400,str(exc)) from None


@router.post('/api/chatgpt/disconnect')
def disconnect(request: Request):
    _local(request,True)
    from backend.main import _stop_pipeline
    _stop_pipeline()
    return {'revoked':client.disconnect()}


@router.post('/api/chatgpt/test')
def test_frame(body: TestBody, request: Request):
    _local(request,True)
    from backend.main import _stop_pipeline
    _stop_pipeline()
    try:
        initial_account = client.status()["active"]
        choices = client.models()
        if body.model not in {m['id'] for m in choices}:
            raise client.ChatGPTError('Choose a model available to your signed-in account.')
        cap = cv2.VideoCapture(str(ROOT/settings.video_path))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC,1000)
            ok,frame = cap.read()
        finally:
            cap.release()
        if not ok:
            raise client.ChatGPTError('Could not read the configured video.')
        started = time.monotonic()
        signs = client.read_frame(frame,body.model)
        account = client.status()['active']
        if not account or account != initial_account:
            raise client.ChatGPTError('ChatGPT was disconnected during the test.')
        client.record_test(body.model)
        return {'ok':True,'seconds':round(time.monotonic()-started,2),'signs':signs,'model':body.model}
    except client.ChatGPTError as exc:
        raise HTTPException(400,str(exc)) from None


def require_tested(model: str):
    status = client.status()
    if not status['connected'] or model not in status['tested_models']:
        raise HTTPException(400,'Connect ChatGPT and run Test one frame with this model first.')
