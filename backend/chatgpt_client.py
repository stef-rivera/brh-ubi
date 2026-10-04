"""Local ChatGPT-plan OAuth and image inference. Never uses a paid API key."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import cv2
import httpx
import jwt

from backend.config import ROOT
from backend.catalog import ids, prompt_list

AUTH = 'https://auth.openai.com'
API = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
DIRECT = 'chatgpt.tokens.use.direct'
LOCAL = ROOT / '.local' / 'chatgpt'
_lock = threading.RLock()
_pending: dict[str, dict] = {}


class ChatGPTError(RuntimeError):
    """Safe, user-facing error without credentials or provider response bodies."""


def _read() -> dict:
    path = LOCAL / 'accounts.json'
    return json.loads(path.read_text()) if path.exists() else {'accounts': {}, 'active': None}


def _save(data: dict) -> None:
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(LOCAL, 0o700)
    path = LOCAL / 'accounts.json'
    temp = LOCAL / f'.{uuid.uuid4().hex}.tmp'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _http_error(response: httpx.Response, action: str) -> None:
    if response.is_success:
        return
    if response.status_code == 401:
        raise ChatGPTError('ChatGPT sign-in expired or was rejected. Connect again.')
    if response.status_code == 429:
        raise ChatGPTError('ChatGPT usage limit reached. Detection has stopped. Check ChatGPT Settings → Usage.')
    if response.status_code == 403:
        raise ChatGPTError('ChatGPT plan access is unavailable for this request. Check the app permission in ChatGPT Settings → Usage.')
    raise ChatGPTError(f'{action} failed (HTTP {response.status_code}). No Gemini fallback was used.')


def status() -> dict:
    with _lock:
        data = _read()
        active = data['accounts'].get(data.get('active'), {})
        return {
            'connected': bool(active.get('access_token') and DIRECT in active.get('scopes', [])),
            'active': data.get('active'),
            'tested_models': active.get('tested_models',[]),
            'accounts': [{'id': cid, 'label': f"{a.get('email') or 'ChatGPT account'} · {cid[-6:]}"} for cid, a in data['accounts'].items()],
        }


def record_test(model: str) -> None:
    with _lock:
        data = _read()
        a = data['accounts'].get(data.get('active'))
        if not a or not a.get('access_token'):
            raise ChatGPTError('Connect your ChatGPT account first.')
        a['tested_models'] = [model] + [m for m in a.get('tested_models',[]) if m != model]
        _save(data)


def begin_login(redirect_uri: str, browser_session: str, account_id: str | None = None) -> str:
    with _lock:
        data = _read()
        if not data.get('host_id'):
            data['host_id'] = 'urn:uuid:' + str(uuid.uuid4())
            _save(data)
        account = data['accounts'].get(account_id) if account_id else None
        if account_id and not account:
            raise ChatGPTError('Unknown saved account.')
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        client_id = account_id or 'dynamic_agent_client'
        params = dict(client_id=client_id, ext_agent_host_id=data['host_id'], response_type='code', redirect_uri=redirect_uri, scope=SCOPES, resource=API, state=state, nonce=nonce, code_challenge_method='S256', code_challenge=challenge)
        if account:
            if account.get('id_token'):
                params['id_token_hint'] = account['id_token']
            if account.get('email'):
                params['login_hint'] = account['email']
        else:
            params['agent_name_hint'] = 'Road English Coach'
        for old in list(_pending):
            if _pending[old]['expires'] < time.time():
                del _pending[old]
        _pending[state] = dict(nonce=nonce, verifier=verifier, redirect_uri=redirect_uri, client_id=client_id, subject=(account or {}).get('subject'), session=browser_session, expires=time.time()+600)
        return AUTH + '/api/accounts/authorize?' + urlencode(params)


def finish_login(params: dict, browser_session: str) -> None:
    with _lock:
        state = params.get('state', '')
        attempt = _pending.get(state)
        if not attempt or attempt['expires'] < time.time() or not secrets.compare_digest(attempt['session'], browser_session):
            raise ChatGPTError('Sign-in session is invalid or expired. Start again from the app.')
        del _pending[state]
        if params.get('error'):
            raise ChatGPTError('ChatGPT sign-in was declined or could not be completed.')
        cid = params.get('client_id') or attempt['client_id']
        if not cid or cid == 'dynamic_agent_client' or not params.get('code'):
            raise ChatGPTError('ChatGPT registration did not return a client ID and code.')
        if attempt['client_id'] != 'dynamic_agent_client' and cid != attempt['client_id']:
            raise ChatGPTError('Returned registration does not match the selected account.')
        try:
            response = httpx.post(AUTH + '/api/accounts/oauth/token', data=dict(grant_type='authorization_code', client_id=cid, code=params['code'], code_verifier=attempt['verifier'], redirect_uri=attempt['redirect_uri'], resource=API), timeout=30)
            _http_error(response, 'ChatGPT code exchange')
            tokens = response.json()
            key = jwt.PyJWKClient(AUTH + '/.well-known/jwks.json').get_signing_key_from_jwt(tokens['id_token'])
            claims = jwt.decode(tokens['id_token'], key.key, algorithms=['RS256'], audience=cid, issuer=AUTH, options={'require': ['exp','iss','aud','sub','nonce']})
            if not secrets.compare_digest(claims['nonce'], attempt['nonce']):
                raise ChatGPTError('Sign-in nonce mismatch. Start again.')
            if attempt['subject'] and claims['sub'] != attempt['subject']:
                raise ChatGPTError('Signed-in account does not match the selected registration.')
            scopes = tokens.get('scope', '').split()
            if DIRECT not in scopes or 'resource.invoke' not in scopes:
                raise ChatGPTError('Permission to use your ChatGPT plan was not granted. Connect again and enable plan access.')
            if not tokens.get('access_token'):
                raise ChatGPTError('ChatGPT did not return an access token.')
            data = _read()
            existing = data['accounts'].get(cid)
            if existing and existing['subject'] != claims['sub']:
                raise ChatGPTError('Registration identity mismatch.')
            data['accounts'][cid] = dict(client_id=cid, subject=claims['sub'], email=claims.get('email',''), scopes=scopes, access_token=tokens['access_token'], refresh_token=tokens.get('refresh_token'), id_token=tokens['id_token'], expires_at=time.time()+float(tokens.get('expires_in',3600)))
            data['active'] = cid
            _save(data)
        except ChatGPTError:
            raise
        except Exception:
            raise ChatGPTError('Could not securely validate ChatGPT sign-in. Start a new sign-in attempt.') from None


def access_token() -> str:
    with _lock:
        data = _read()
        a = data['accounts'].get(data.get('active'))
        if not a or not a.get('access_token') or DIRECT not in a.get('scopes',[]):
            raise ChatGPTError('Connect your ChatGPT account first.')
        if a['expires_at'] <= time.time()+60:
            if not a.get('refresh_token'):
                raise ChatGPTError('ChatGPT sign-in expired. Connect again.')
            try:
                r = httpx.post(AUTH+'/api/accounts/oauth/token', data=dict(grant_type='refresh_token', client_id=a['client_id'], refresh_token=a['refresh_token'], resource=API), timeout=30)
                _http_error(r, 'ChatGPT session renewal')
                t = r.json()
                a.update(access_token=t['access_token'], refresh_token=t['refresh_token'], expires_at=time.time()+float(t.get('expires_in',3600)))
                if 'scope' in t:
                    a['scopes'] = t['scope'].split()
                if 'id_token' in t:
                    a['id_token'] = t['id_token']
                _save(data)
            except ChatGPTError:
                raise
            except Exception:
                raise ChatGPTError('ChatGPT session renewal failed. Connect again.') from None
        if DIRECT not in a['scopes']:
            raise ChatGPTError('ChatGPT plan permission is no longer enabled.')
        return a['access_token']


def disconnect() -> bool:
    """Clear local tokens even if revocation cannot be confirmed."""
    with _lock:
        data = _read()
        a = data['accounts'].get(data.get('active'))
        confirmed = True
        if a:
            if a.get('refresh_token'):
                confirmed = False
                for delay in (0, 0.5, 1):
                    time.sleep(delay)
                    try:
                        r = httpx.post(AUTH+'/api/accounts/oauth/revoke', data=dict(token=a['refresh_token'], token_type_hint='refresh_token', client_id=a['client_id']), timeout=10)
                        if r.status_code == 200:
                            confirmed = True
                            break
                        if r.status_code < 500:
                            break
                    except httpx.HTTPError:
                        pass
            for name in ('access_token','refresh_token','id_token','tested_models'):
                a.pop(name, None)
            data['active'] = None
            _save(data)
        return confirmed


def models() -> list[dict]:
    try:
        r = httpx.get(API+'/models', headers={'Authorization':'Bearer '+access_token()}, timeout=30)
        _http_error(r, 'ChatGPT model list')
        return [{'id':m['slug'], 'name':m.get('display_name',m['slug'])} for m in r.json().get('models',[]) if m.get('visibility')=='list']
    except httpx.HTTPError:
        raise ChatGPTError('Could not reach ChatGPT to load available models.') from None


def parse_signs(text: str, width: int, height: int) -> list[dict]:
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n',1)[1].rsplit('```',1)[0]
    try:
        payload = json.loads(text)
        rows = payload['signs']
        if not isinstance(rows,list):
            raise ValueError()
        found = []
        known = set(ids())
        for raw in rows:
            box = raw['box_2d']
            if len(box) != 4:
                continue
            ymin,xmin,ymax,xmax = map(float,box)
            confidence = float(raw.get('confidence',0))
            if not all(math.isfinite(v) for v in (ymin,xmin,ymax,xmax,confidence)):
                continue
            x1,y1 = int(max(0,min(width-1,xmin*width/1000))),int(max(0,min(height-1,ymin*height/1000)))
            x2,y2 = int(max(0,min(width,xmax*width/1000))),int(max(0,min(height,ymax*height/1000)))
            if x2<=x1 or y2<=y1:
                continue
            sign_id = str(raw.get('sign_id','unknown'))
            found.append(dict(sign_id=sign_id if sign_id in known else 'unknown', sign_text=str(raw.get('sign_text',''))[:200], box=[x1,y1,x2,y2], confidence=max(0,min(1,confidence))))
        return found
    except (ValueError,TypeError,KeyError,AttributeError):
        raise ChatGPTError('ChatGPT returned an invalid sign response. Detection stopped; no signs were fabricated.') from None


def read_frame(frame, model: str, crop_mode: bool = False) -> list[dict]:
    if not model:
        raise ChatGPTError('Choose a ChatGPT model first.')
    height,width = frame.shape[:2]
    ok, encoded = cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,85])
    if not ok:
        raise ChatGPTError('Could not encode the video frame.')
    prompt = ('Find visible US road signs in this dashcam frame. Read only legible text; do not guess small or blurred text. '
              'Match a catalog id only if the sign matches; otherwise use unknown. Include visible signs outside the catalog. '
              'Do not explain laws or invent sign meanings. Return only JSON: {"signs":[{"sign_id":"...","sign_text":"...","box_2d":[ymin,xmin,ymax,xmax],"confidence":0.9}]}. '
              'Coordinates must be normalized from 0 to 1000, with a tight box around the sign. Use an empty signs array if none are visible. Catalog:\n'+prompt_list())
    if crop_mode:
        prompt = ('This is a crop of ONE candidate US road sign. Identify its symbol and read only clearly legible text. '
                  'Do not confuse STOP (red octagon) with DO NOT ENTER (red circle with white horizontal bar). '
                  'Do not confuse a two-person school crossing with a bicycle/pedestrian crossing. '
                  'Business advertisements are not road signs; return an empty signs array for those. '
                  'Use unknown for real signs outside the catalog. Do not guess blurred words or numbers. '
                  'Return at most one sign. ' + prompt)
    body = dict(model=model, store=False, stream=True, input=[dict(role='user',content=[dict(type='input_text',text=prompt),dict(type='input_image',image_url='data:image/jpeg;base64,'+base64.b64encode(encoded).decode())])])
    parts = []
    started = time.monotonic()
    try:
        with httpx.stream('POST',API+'/responses',headers={'Authorization':'Bearer '+access_token()},json=body,timeout=20 if crop_mode else 60) as r:
            _http_error(r,'ChatGPT image request')
            for line in r.iter_lines():
                if time.monotonic()-started>(30 if crop_mode else 90):
                    raise ChatGPTError('ChatGPT image request timed out. Detection stopped.')
                if not line.startswith('data:'):
                    continue
                chunk = line[5:].strip()
                if chunk=='[DONE]':
                    break
                event = json.loads(chunk)
                kind = event.get('type')
                if kind=='response.output_text.delta':
                    parts.append(event.get('delta',''))
                elif kind in ('response.failed','response.incomplete','error'):
                    raise ChatGPTError('ChatGPT could not complete the image request. Check model access and ChatGPT Settings → Usage.')
                elif kind=='response.completed':
                    return parse_signs(''.join(parts),width,height)
        raise ChatGPTError('ChatGPT stream ended without a completed response.')
    except httpx.HTTPError:
        raise ChatGPTError('ChatGPT connection failed. Detection stopped; try again when connected.') from None
    except (ValueError,KeyError,TypeError):
        raise ChatGPTError('ChatGPT returned an unreadable response.') from None
