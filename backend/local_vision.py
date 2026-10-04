"""Local sign proposals with independent OCR, cloud and speech queues."""
from __future__ import annotations
import queue, re, threading, time
from collections import Counter
from datetime import datetime, timezone
import cv2
from backend.config import ROOT
from backend.catalog import get
from backend.detector import _thumb
from backend.events import bus
from backend.state import state
from backend.storage import log_detection
from backend.tts import speak

ANNOUNCEMENTS = {"school_zone": "School sign detected.", "road_work": "Road work sign detected.", "stop": "Stop sign detected.", "do_not_enter": "Do not enter sign detected.", "yield": "Yield sign detected.", "lane_ends": "Lane ends sign detected.", "railroad": "Railroad crossing sign detected."}

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
    for words,sign_id in [('DO NOT ENTER','do_not_enter'),('ROAD WORK','road_work'),('ONE WAY','one_way'),('LANE ENDS','lane_ends')]:
        if words in upper: return dict(sign_id=sign_id,sign_text=text,confidence=.85)
    if upper=='STOP': return dict(sign_id='stop',sign_text='STOP',confidence=.9)
    # Legible street names need no model explanation and stay outside the catalog.
    if re.search(r'\b(RD|ROAD|AVE|AV|ST|STREET|BLVD|DR)\b',upper) and len(text)>5:
        return dict(sign_id='unknown',sign_text=text,confidence=.8)
    return None

class LocalDetector:
    def __init__(self, video, fallback=None, model=None, ocr=read_text, voice=speak):
        self.video=video; self.drive_id=state.drive_id; self.fallback=fallback; self.ocr=ocr; self.voice=voice
        if model is None:
            from ultralytics import YOLOE
            if not MODEL_PATH.exists(): raise RuntimeError('Local sign model is missing. Run scripts/setup_local_vision.py first.')
            model=YOLOE(str(MODEL_PATH))
            import numpy as np
            model.predict(np.zeros((720,1280,3),dtype="uint8"),imgsz=1280,device="mps",verbose=False)
        self.model=model
        self.stop_event=threading.Event(); self.jobs=queue.Queue(8); self.cloud_jobs=queue.Queue(6); self.audio_jobs=queue.Queue(8)
        self.tracks={};self.seen={};self.threads=[];self.cloud_count=0;self.cloud_disabled=False
        self.stats={'frames':0,'pending':0,'resolved':0,'unresolved':0,'cloud_requests':0,'queue_full':0,'inference_ms':0,'audio_ready':0}

    def active(self): return not self.stop_event.is_set() and state.drive_id==self.drive_id
    def metrics(self):
        if not self.active(): return
        with state.lock:
            state.pipeline_metrics=dict(self.stats,local_queue=self.jobs.qsize(),cloud_queue=self.cloud_jobs.qsize())
        bus.publish({'type':'pipeline_metrics',**state.pipeline_metrics})
    def status(self,name,message):
        if not self.active():return
        with state.lock:state.detection_status=name;state.detection_error=message if name=='error' else ''
        bus.publish({'type':'detector_status','status':name,'message':message})
    def start(self):
        if any(t.is_alive() for t in self.threads):return
        if self.stop_event.is_set():return
        for name,target in [('local-ocr',self.local_loop),('crop-reader-1',self.cloud_loop),('crop-reader-2',self.cloud_loop),('sign-audio',self.audio_loop),('local-detection',self.loop)]:
            t=threading.Thread(target=target,name=name,daemon=True);self.threads.append(t);t.start()
    def stop(self):
        self.stop_event.set()
        for t in self.threads:t.join(timeout=.15)
    def publish_pending(self,job):
        if not self.active():return
        row=dict(event_id=job['id'],drive_id=self.drive_id,ts_video=round(job['ts'],2),ts_wall=datetime.now(timezone.utc).isoformat(),sign_id='unknown',sign_text='Reading sign…',recognition_status='pending',box=job['box'],confidence=job['confidence'],source='local',thumb_url=job['thumb'],meaning='',verified=False,safety_critical=False,speak_now=False)
        with state.lock:
            if not self.active():return
            state.detections.append(row)
        self.stats['pending']+=1;bus.publish({'type':'detection',**row})
    def finish(self,job,found=None,source='local-ocr',error=''):
        if not self.active():return
        found=found or {};sign_id=found.get('sign_id','unknown');entry=get(sign_id) or {}
        text=found.get('sign_text') or entry.get('sign_text') or 'Unresolved sign'
        key=(sign_id,text.casefold())
        last=self.seen.get(key)
        duplicate=found and last is not None and abs(job['ts']-last)<10
        if found:self.seen[key]=job['ts']
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
                row.update(first_seen_video=job.get('first_seen_video',job['ts']),sign_id=sign_id,sign_text=text,confidence=found.get('confidence',job['confidence']),recognition_status='resolved' if found else 'unresolved',source=source,meaning=entry.get('meaning',''),meaning_es=entry.get('meaning_es',''),verified=entry.get('verified',False),safety_critical=entry.get('safety_critical',False),cue_text=entry.get('cue_text',''),recognition_error=error,recognition_seconds=round(time.monotonic()-job['created'],3),value=found.get('value'))
                row=dict(row)
        self.stats['pending']-=1
        if duplicate:bus.publish({'type':'detection_remove','event_id':job['id']});return
        self.stats['resolved' if found else 'unresolved']+=1
        log_detection(row);bus.publish({'type':'detection_update',**row});self.metrics()
        # Descriptive announcements report what was observed; they do not instruct a maneuver.
        if entry.get('safety_critical') and time.monotonic()-job['created']<20:
            try:self.audio_jobs.put_nowait((job,ANNOUNCEMENTS[sign_id]))
            except queue.Full:pass

    def local_loop(self):
        while not self.stop_event.is_set():
            try:job=self.jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                texts=self.ocr(job['crop']);job['ocr']=texts
                found=interpret_text(texts,job['labels'])
                if found:self.finish(job,found)
                elif self.fallback and not self.cloud_disabled and self.cloud_count<8:
                    try:
                        self.cloud_jobs.put_nowait(job);self.cloud_count+=1;self.stats['cloud_requests']=self.cloud_count
                    except queue.Full:self.finish(job,error='Recognition queue is full; detection continued.')
                else:self.finish(job,error='No confident local reading. Cloud assistance is off or its per-drive limit was reached.')
            except Exception:self.finish(job,error='Local OCR failed; detection continued.')
            finally:self.jobs.task_done()
    def cloud_loop(self):
        while not self.stop_event.is_set():
            try:job=self.cloud_jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                if time.monotonic()-job["created"]>25:
                    self.finish(job,error="Crop reading expired; detection continued.");continue
                if self.cloud_disabled:self.finish(job,error='Cloud assistance unavailable; detection continued.');continue
                rows=self.fallback(job['crop'])
                candidates=[r for r in rows if r.get('confidence',0)>=.75 and (r.get('sign_id')!='unknown' or r.get('sign_text'))]
                # Multiple conflicting signs within one crop do not constitute a verified reading.
                found=max(candidates,key=lambda r:r['confidence']) if len(candidates)==1 else None
                self.finish(job,found,source='local+chatgpt')
            except Exception:
                self.cloud_disabled=True;self.finish(job,error='Cloud assistance failed. Local detection and OCR continue.')
                self.status('running','Cloud assistance unavailable. Local detection and text reading continue.')
            finally:self.cloud_jobs.task_done()
    def audio_loop(self):
        while not self.stop_event.is_set():
            try:job,text=self.audio_jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                if self.active() and time.monotonic()-job['created']<20:
                    url=self.voice(text)
                    if self.active() and time.monotonic()-job['created']<20:
                        self.stats['audio_ready']+=1;self.metrics();bus.publish({'type':'cue','event_id':job['id'],'text':text,'audio_url':url,'seconds_since_detected':round(time.monotonic()-job['created'],3)})
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
                        result=self.model.track(frame,imgsz=1280,device='mps',conf=.15,agnostic_nms=True,persist=True,tracker=str(TRACKER_PATH),verbose=False)[0]
                        self.stats['frames']+=1;self.stats['inference_ms']=round((time.monotonic()-start)*1000,1)
                        for b in result.boxes:
                            if b.id is None:continue
                            tid=int(b.id.item());box=list(map(int,b.xyxy[0].tolist()));x1,y1,x2,y2=box;confidence=float(b.conf.item())
                            track=self.tracks.setdefault(tid,dict(first=ts,last=ts,hits=0,labels=Counter(),best=None,score=0,submitted=False))
                            track['last']=ts;track['hits']+=1;track['labels'][self.model.names[int(b.cls.item())]]+=1
                            score=(x2-x1)*(y2-y1)*confidence
                            if score>track['score'] and min(x2-x1,y2-y1)>=10:
                                crop=frame[max(0,y1-6):min(frame.shape[0],y2+6),max(0,x1-6):min(frame.shape[1],x2+6)].copy()
                                track.update(score=score,best=(crop,frame.copy(),ts,box,confidence))
                        for tid,track in list(self.tracks.items()):
                            if not track['submitted'] and track['hits']>=3 and track['best'] and (ts-track['first']>=2 or ts-track['last']>.2):
                                crop,original,stamp,box,confidence=track['best'];job=dict(id=f'{self.drive_id}:{tid}',first_seen_video=track['first'],crop=crop,ts=stamp,box=box,confidence=confidence,labels=dict(track['labels']),created=time.monotonic())
                                if self.jobs.full():self.stats['queue_full']+=1;continue
                                job['thumb']=_thumb(original,box,self.drive_id,tid)
                                # Publish before enqueuing so a fast recognizer cannot race the pending card.
                                self.publish_pending(job);self.jobs.put_nowait(job);track['submitted']=True;track['best']=None
                            if ts-track['last']>3:del self.tracks[tid]
                if start-last_metrics>1:self.metrics();last_metrics=start
                if self.video.ended:break
                self.stop_event.wait(max(0,.1-(time.monotonic()-start)))
            if self.active():
                self.status('resolving','Video complete. Finishing pending sign readings…')
                while self.active() and (self.jobs.unfinished_tasks or self.cloud_jobs.unfinished_tasks or self.audio_jobs.unfinished_tasks):
                    self.metrics();self.stop_event.wait(.2)
                self.metrics();self.status('completed','Drive complete. Park to practice, or Start drive to run it again.')
        except Exception as exc:
            self.status('error',f'Local detector failed: {type(exc).__name__}. Check local vision setup.')
