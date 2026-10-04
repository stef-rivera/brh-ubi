"""Local sign proposals with independent OCR/classification and speech queues."""
from __future__ import annotations
import queue, re, threading, time
from collections import Counter
from datetime import datetime, timezone
import cv2
from backend.config import ROOT
from backend.catalog import get, announcement, load_catalog
from backend.detector import _thumb
from backend.events import bus
from backend.state import state
from backend.storage import save_detection as log_detection
from backend.local_symbols import LocalSymbolClassifier
from backend.us_signs import USSignSpecialist, overlap, resolve_votes
from backend.tts import speak
from backend.vision_runtime import mps_lock

MODEL_PATH = ROOT / '.local/vision/signs.pt'
TRACKER_PATH = ROOT / 'backend/sign_tracker.yaml'

def read_text(crop):
    import Foundation, Vision
    scale=max(1, min(4, 320/max(crop.shape[:2])))
    image=cv2.resize(crop,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    data=cv2.imencode('.png',image)[1].tobytes()
    handler=Vision.VNImageRequestHandler.alloc().initWithData_options_(Foundation.NSData.dataWithBytes_length_(data,len(data)),None)
    req=Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    req.setRecognitionLanguages_(['en-US'])
    req.setMinimumTextHeight_(.01)
    ok,error=handler.performRequests_error_([req],None)
    if not ok: raise RuntimeError('Local text recognition failed')
    return [{'text':str(o.topCandidates_(1)[0].string()),'confidence':float(o.topCandidates_(1)[0].confidence())} for o in req.results() or []]

def interpret_text(items, labels):
    text=' '.join(x['text'] for x in items if x['confidence']>=.5).strip()
    upper=text.upper()
    number=re.search(r'\b(\d{1,2})\b',upper)
    # A number alone needs independent evidence that this is a speed sign.
    if number and (('SPEED' in upper and 'LIMIT' in upper) or labels.get('speed limit sign',0)>=3):
        value=int(number[1])
        if 5<=value<=85 and value%5==0:
            return dict(sign_id='speed_limit',sign_text=f'SPEED LIMIT {value}',confidence=.9,value=value)
    for words,sign_id in [('NO TURN ON RED','no_turn_on_red'),('DO NOT ENTER','do_not_enter'),('ROAD WORK','road_work'),('ONE WAY','one_way'),('LANE ENDS','lane_ends')]:
        if words in upper: return dict(sign_id=sign_id,sign_text=text,confidence=.85)
    if upper=='STOP': return dict(sign_id='stop',sign_text='STOP',confidence=.9)
    if upper=='AHEAD' and any(x['confidence']>=.9 for x in items):
        return dict(sign_id='unknown',sign_text='AHEAD',confidence=.9)
    # Legible street names need no model explanation and stay outside the catalog.
    if re.search(r'\b(RD|ROAD|AVE|AV|ST|STREET|BLVD|DR)(?:\s+\d+)?$',upper) and len(text)>5:
        return dict(sign_id='unknown',sign_text=text,confidence=.8)
    return None

class LocalDetector:
    def __init__(self, video, fallback=None, model=None, ocr=read_text, voice=speak, classifier=None):
        self.video=video; self.drive_id=state.drive_id; self.fallback=None; self.ocr=ocr; self.voice=voice
        default_model=model is None
        if model is None:
            from ultralytics import YOLOE
            if not MODEL_PATH.exists(): raise RuntimeError('Local sign model is missing. Run scripts/setup_local_vision.py first.')
            model=YOLOE(str(MODEL_PATH))
            import numpy as np
            with mps_lock:
                model.predict(np.zeros((720,1280,3),dtype="uint8"),imgsz=1280,device="mps",verbose=False)
        self.model=model
        self.specialist=USSignSpecialist() if default_model else None
        self.classifier=classifier if classifier is not None else (LocalSymbolClassifier() if default_model else lambda crops:None)
        self.stop_event=threading.Event(); self.jobs=queue.Queue(8); self.audio_jobs=queue.Queue(8)
        self.completed=set();self.inflight=set();self.announced=set()
        self.specialist_tracks={};self.specialist_next=100000;self.tracks={};self.seen={};self.threads=[];self.cloud_count=0;self.cloud_disabled=False
        self.stats={'frames':0,'pending':0,'resolved':0,'tentative':0,'unresolved':0,'cloud_requests':0,'queue_full':0,'inference_ms':0,'audio_ready':0,'audio_failed':0,'cloud_cache_hits':0,'cloud_skipped':0}

    def active(self): return not self.stop_event.is_set() and state.drive_id==self.drive_id
    def relevant(self,job):
        if time.monotonic()-job['created']>=3:return False
        latest=self.video.latest() if hasattr(self.video,'latest') else None
        return latest is None or latest[1]-job['ts']<=2

    def metrics(self):
        if not self.active(): return
        with state.lock:
            state.pipeline_metrics=dict(self.stats,local_queue=self.jobs.qsize(),cloud_queue=0)
        bus.publish({'type':'pipeline_metrics',**state.pipeline_metrics})
    def status(self,name,message):
        if not self.active():return
        with state.lock:state.detection_status=name;state.detection_error=message if name=='error' else ''
        bus.publish({'type':'detector_status','status':name,'message':message})
    def start(self):
        if any(t.is_alive() for t in self.threads):return
        if self.stop_event.is_set():return
        for name,target in [('local-ocr',self.local_loop),('sign-audio',self.audio_loop),('local-detection',self.loop)]:
            t=threading.Thread(target=target,name=name,daemon=True);self.threads.append(t);t.start()
    def stop(self):
        self.stop_event.set()
        if state.drive_id==self.drive_id:
            with state.lock:
                changed=[]
                for row in state.detections:
                    if row.get('recognition_status')=='pending':
                        row.update(recognition_status='unresolved',recognition_error='Drive stopped before the reading completed.')
                        self.stats['pending']=max(0,self.stats['pending']-1);self.stats['unresolved']+=1;changed.append(dict(row))
                    if row.get('audio_status')=='queued':
                        row.update(audio_status='skipped',audio_error='Drive stopped before the announcement played.')
                        changed.append(dict(row))
                state.pipeline_metrics=dict(self.stats,local_queue=0,cloud_queue=0)
            for row in changed:log_detection(row);bus.publish({'type':'detection_update',**row})
        for t in self.threads:t.join(timeout=.15)
    def enqueue(self,job):
        """Only one reading per tracked sign may be queued or running."""
        if not self.active() or job["id"] in self.inflight or job["id"] in self.completed or self.jobs.full():return False
        self.inflight.add(job["id"])
        self.publish_pending(job)
        self.jobs.put_nowait(job)
        return True

    def publish_pending(self,job):
        if not self.active():return
        row=dict(event_id=job['id'],drive_id=self.drive_id,ts_video=round(job['ts'],2),ts_wall=datetime.now(timezone.utc).isoformat(),sign_id='unknown',sign_text='Reading sign…',recognition_status='pending',box=job['box'],confidence=job['confidence'],source='local',thumb_url=job['thumb'],meaning='',verified=False,safety_critical=False,speak_now=False)
        with state.lock:
            if not self.active():return
            old=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if old:
                if old.get('recognition_status')=='unresolved':self.stats['unresolved']=max(0,self.stats['unresolved']-1)
                elif old.get('recognition_status')=='tentative':self.stats['tentative']=max(0,self.stats['tentative']-1)
                old.update(row)
            else:state.detections.append(row)
        self.stats['pending']+=1;bus.publish({'type':'detection',**row})
    def finish(self,job,found=None,source='local-ocr',error=''):
        if not self.active():return
        found=found or {};sign_id=found.get('sign_id','unknown')
        if found and sign_id=='unknown':
            text_key=re.sub(r'\s+',' ',found.get('sign_text','').strip()).casefold()
            match=next((e for e in load_catalog() if e['sign_text'].casefold()==text_key),None)
            if match:sign_id=match['id'];found=dict(found,sign_id=sign_id)
        entry=get(sign_id) or {}
        text=found.get('sign_text') or entry.get('sign_text') or 'Unresolved sign'
        if sign_id=='speed_limit':
            number=re.search(r'\b(\d{1,2})\b',text)
            if number:found['value']=int(number[1]);text='SPEED LIMIT '+number[1]
        key=(sign_id,text.casefold())
        last=self.seen.get(key)
        duplicate=found and last is not None and abs(job['ts']-last)<10
        if found and not found.get('tentative'):self.seen[key]=job['ts']
        with state.lock:
            if not self.active():return
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if row is None:return
            if duplicate:
                state.detections.remove(row)
            else:
                if sign_id=='speed_limit':
                    number=re.search(r'\b(\d{1,2})\b',text)
                    if number:found['value']=int(number[1]);text='SPEED LIMIT '+number[1]
                row.update(first_seen_video=job.get('first_seen_video',job['ts']),sign_id=sign_id,sign_text=text,confidence=found.get('confidence',job['confidence']),recognition_status=('tentative' if found.get('tentative') else 'resolved') if found else 'unresolved',source=source,meaning=entry.get('meaning',''),meaning_es=entry.get('meaning_es',''),verified=entry.get('verified',False),safety_critical=entry.get('safety_critical',False),cue_text=entry.get('cue_text',''),recognition_error=error,recognition_seconds=round(time.monotonic()-job['created'],3),value=found.get('value'),symbol_similarity=found.get('symbol_similarity'),symbol_margin=found.get('symbol_margin'),tentative=found.get('tentative',False),specialist_label=found.get('specialist_label'),specialist_frames=found.get('specialist_frames'),demo_guess=found.get('demo_guess',False))
                row=dict(row)
        if found and sign_id!='unknown' and not found.get('tentative'):self.completed.add(job['id'])
        self.stats['pending']-=1
        if duplicate:bus.publish({'type':'detection_remove','event_id':job['id']});return
        self.stats[('tentative' if found.get('tentative') else 'resolved') if found else 'unresolved']+=1
        log_detection(row);bus.publish({'type':'detection_update',**row});self.metrics()
        # Descriptive announcements report what was observed; they do not instruct a maneuver.
        text=announcement(sign_id,found.get('value')) if found else ''
        if found.get('tentative'):
            text=row['sign_text'].lower()+' sign detected.'
        if text and (job['id'],text) in self.announced:return
        if text:self.announced.add((job['id'],text))
        if text and not self.relevant(job):
            self.audio_status(job,'skipped',text,reason='Sign is no longer current; use Replay on the card.')
        elif text:
            self.audio_status(job,'queued',text)
            try:self.audio_jobs.put_nowait((job,text))
            except queue.Full:self.audio_status(job,'skipped',text,reason='Speech queue is full; replay the announcement from its card.')

    def local_loop(self):
        while not self.stop_event.is_set():
            try:job=self.jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                if time.monotonic()-job['created']>1.5:
                    self.finish(job,error='Local reading expired; saved for parked review.');continue
                texts=[];found=None
                for crop in job.get('crops',[job['crop']]):
                    items=self.ocr(crop);texts.extend(items)
                    found=interpret_text(items,job['labels'])
                    if found:break
                job['ocr']=texts
                used_symbol=not found
                if used_symbol:
                    specialist=resolve_votes(job.get('specialist_votes',[]))
                    if specialist and min(job['crop'].shape[:2])<36:specialist['tentative']=True
                    if specialist and specialist.get('tentative'):specialist['demo_guess']=True
                    # Preserve the distinct combined symbol fallback absent from LISA.
                    if specialist and specialist.get('specialist_label')!='pedestrianCrossing':found=specialist
                    else:found=self.classifier(job.get('crops',[job['crop']])) or specialist
                self.finish(job,found,source=('local-us-specialist' if found and found.get('specialist_label') else 'local-symbol') if used_symbol else 'local-ocr',error='' if found else 'Couldn’t read this sign confidently with local recognition.')
            except Exception:self.finish(job,error='Local OCR failed; detection continued.')
            finally:self.inflight.discard(job["id"]);self.jobs.task_done()
    def audio_status(self,job,status,text,url='',reason=''):
        if not self.active():return
        with state.lock:
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if row is None:return
            row.update(audio_status=status,audio_text=text,audio_url=url,audio_error=reason,speak_now=status in ('queued','ready'))
            snapshot=dict(row)
        log_detection(snapshot)
        bus.publish({'type':'detection_update',**snapshot})
        bus.publish({'type':'audio_status','drive_id':self.drive_id,'event_id':job['id'],'status':status,'text':text,'audio_url':url,'reason':reason})
    def audio_loop(self):
        while not self.stop_event.is_set():
            try:job,text=self.audio_jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                if not self.active():continue
                if not self.relevant(job):
                    self.audio_status(job,'skipped',text,reason='Announcement expired; use Replay on the card.');continue
                url=self.voice(text)
                if not self.active():continue
                if not self.relevant(job):
                    self.audio_status(job,'skipped',text,reason='Sign is no longer current; use Replay on the card.');continue
                if url:
                    self.stats['audio_ready']+=1
                    self.audio_status(job,'ready',text,url)
                    self.metrics()
                    bus.publish({'type':'cue','drive_id':self.drive_id,'event_id':job['id'],'text':text,'audio_url':url,'seconds_since_detected':round(time.monotonic()-job['created'],3)})
                else:
                    self.stats['audio_failed']+=1
                    self.audio_status(job,'failed',text,reason='ElevenLabs did not return audio. Check voice configuration or use browser voice replay.')
                    self.metrics()
            except Exception:
                self.stats['audio_failed']+=1;self.audio_status(job,'failed',text,reason='Speech generation failed; recognition continues.');self.metrics()
            finally:self.audio_jobs.task_done()
    def loop(self):
        self.status('running','Local detection running. Signs resolve independently while the video continues.')
        last_ts=-1;last_metrics=0
        try:
            while self.active():
                start=time.monotonic();item=self.video.latest()
                if item is not None:
                    frame,ts=item
                    if ts!=last_ts:
                        last_ts=ts
                        with mps_lock:
                            result=self.model.track(frame,imgsz=1280,device='mps',conf=.15,agnostic_nms=True,persist=True,tracker=str(TRACKER_PATH),verbose=False)[0]
                        specialist_predictions=self.specialist.predict(frame) if self.specialist else []
                        self.stats['frames']+=1;self.stats['inference_ms']=round((time.monotonic()-start)*1000,1)
                        boxes=list(result.boxes)
                        # Specialists may find a sign that the generic proposal model misses.
                        from types import SimpleNamespace
                        import torch
                        for prediction in specialist_predictions:
                            if any(overlap(prediction['box'],b.xyxy[0].tolist())>=.25 for b in boxes):continue
                            choices=[(overlap(prediction['box'],v['box']),k) for k,v in self.specialist_tracks.items() if ts-v['ts']<.6]
                            best=max(choices,default=(0,None))
                            if best[0]>=.15:synthetic_id=best[1]
                            else:self.specialist_next+=1;synthetic_id=self.specialist_next
                            self.specialist_tracks[synthetic_id]=dict(box=prediction['box'],ts=ts)
                            boxes.append(SimpleNamespace(id=torch.tensor(synthetic_id),xyxy=torch.tensor([prediction['box']]),conf=torch.tensor(prediction['confidence']),specialist_label=prediction['label']))
                        self.specialist_tracks={k:v for k,v in self.specialist_tracks.items() if ts-v['ts']<1}
                        for b in boxes:
                            if b.id is None:continue
                            tid=int(b.id.item());box=list(map(int,b.xyxy[0].tolist()));x1,y1,x2,y2=box;confidence=float(b.conf.item())
                            track=self.tracks.setdefault(tid,dict(first=ts,last=ts,hits=0,labels=Counter(),best=None,views=[],specialist_votes=[],score=0,submitted=False))
                            track['last']=ts;track['hits']+=1;track['labels'][b.specialist_label if hasattr(b,'specialist_label') else self.model.names[int(b.cls.item())]]+=1
                            matching=[p for p in specialist_predictions if overlap(box,p['box'])>=.35]
                            if matching:
                                track['specialist_votes'].append(max(matching,key=lambda p:p['confidence']))
                                track['specialist_votes']=track['specialist_votes'][-20:]
                            score=(x2-x1)*(y2-y1)*confidence
                            if score>track['score'] and min(x2-x1,y2-y1)>=10:
                                crop=frame[max(0,y1-6):min(frame.shape[0],y2+6),max(0,x1-6):min(frame.shape[1],x2+6)].copy()
                                track.update(score=score,best=(crop,frame.copy(),ts,box,confidence))
                            if min(x2-x1,y2-y1)>=10:
                                pad=max(6,int(max(x2-x1,y2-y1)*.12))
                                view=frame[max(0,y1-pad):min(frame.shape[0],y2+pad),max(0,x1-pad):min(frame.shape[1],x2+pad)].copy()
                                if ts-track.get('last_view_ts',-1)>.2:
                                    track['last_view_ts']=ts;track['views'].append((score,ts,view));track['views']=sorted(track['views'],key=lambda x:x[0],reverse=True)[:3]
                        for tid,track in list(self.tracks.items()):
                            ident=f"{self.drive_id}:{tid}:{int(track['first']*1000)}"
                            retry=track['submitted'] and ident not in self.completed and ident not in self.inflight and ts-track.get('attempt_ts',ts)>.5 and track['score']>track.get('attempt_score',0)*1.3
                            if (not track['submitted'] or retry) and track['hits']>=3 and track['best'] and (ts-track['first']>=.65 or ts-track['last']>.2):
                                crop,original,stamp,box,confidence=track['best'];job=dict(id=ident,first_seen_video=track['first'],crop=crop,ts=stamp,box=box,confidence=confidence,labels=dict(track['labels']),specialist_votes=list(track['specialist_votes']),created=time.monotonic(),crops=[v[2] for v in track['views']] or [crop])
                                if self.jobs.full():self.stats['queue_full']+=1;continue
                                job['thumb']=_thumb(original,box,self.drive_id,f"{tid}-{int(track['first']*1000)}")
                                # Publish before enqueuing so a fast recognizer cannot race the pending card.
                                if self.enqueue(job):track['submitted']=True;track['attempt_ts']=ts;track['attempt_score']=track['score']
                            if ts-track['last']>3:del self.tracks[tid]
                if start-last_metrics>1:self.metrics();last_metrics=start
                if self.video.ended:break
                self.stop_event.wait(max(0,.1-(time.monotonic()-start)))
            if self.active():
                self.status('resolving','Video complete. Finishing pending sign readings…')
                while self.active() and (self.jobs.unfinished_tasks or self.audio_jobs.unfinished_tasks):
                    self.metrics();self.stop_event.wait(.2)
                self.metrics();self.status('completed','Drive complete. Park to practice, or Start drive to run it again.')
        except Exception as exc:
            self.status('error',f'Local detector failed: {type(exc).__name__}. Check local vision setup.')
