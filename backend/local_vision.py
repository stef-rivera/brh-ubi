"""Local sign proposals with independent OCR/classification and speech queues."""
from __future__ import annotations
import queue, re, threading, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import cv2
from backend.config import ROOT
from backend.catalog import get, driving_announcement as announcement, load_catalog
from backend.detector import _thumb
from backend.events import bus
from backend.state import state
from backend.storage import save_detection as log_detection
from backend.local_symbols import LocalSymbolClassifier
from backend.us_signs import USSignSpecialist, overlap, resolve_votes, SHA256 as SPECIALIST_SHA256
from backend.tts import speak
from backend.vision_runtime import mps_lock
from backend.feedback import store_raw_example, register_prediction, lookup_review, apply_review_fields

MODEL_PATH = ROOT / '.local/vision/signs.pt'
TRACKER_PATH = ROOT / 'backend/sign_tracker.yaml'

def printed_text(items):
    """Require local OCR evidence; a predicted catalog label is not printed text."""
    return ' '.join(item.get('text', '').strip() for item in items
                    if item.get('confidence', 0) >= .5
                    and re.search(r'[A-Za-z0-9]{2,}', item.get('text', '')))

def driving_audio_allowed(row, video_source=None):
    # This demo preference applies only to the named nighttime sample.
    if not isinstance(video_source, (str, Path)) or Path(video_source).name != 'night-drive.mp4':
        return True
    labels = ' '.join(str(row.get(key) or '') for key in ('sign_id', 'specialist_label', 'sign_text'))
    normalized = re.sub(r'[^a-z]', '', labels.lower())
    symbol_types = ('laneend',
                    'school', 'pedestrian', 'bicycle', 'noleftturn', 'norightturn',
                    'curve', 'slippery', 'winding')
    return not any(symbol in normalized for symbol in symbol_types)


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

# Keep guide messages even when they contain a destination ending in ROAD/ST.
GUIDE_WORDS = {'AHEAD','EXPECT','DELAY','DELAYS','WORK','LANE','CLOSED','EXIT',
               'SECOND','2ND','RIGHT','NEXT','DETOUR','MERGE','SPEED','LIMIT','STOP','YIELD','ONLY','NORTH',
               'SOUTH','EAST','WEST','JUNCTION','INTERSTATE','MILES','MILE'}
STREET_SUFFIX = r'(RD|ROAD|AVE|AV|AVENUE|ST|STREET|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|CT|COURT|PL|PLACE|PKWY|WAY)'

def is_street_name(text):
    """Stef's raw-text name filter, with guide directions preserved."""
    upper=text.upper().strip()
    words=set(re.findall(r'[A-Z0-9]+',upper))
    if words & GUIDE_WORDS or upper=='ONE WAY':return False
    return len(upper)>5 and bool(re.search(r'\b'+STREET_SUFFIX+r'\.?$',upper))

def street_blade_reason(job,items):
    """Filter names before nearest-class guessing, never blanket-filter green."""
    text=' '.join(item['text'] for item in items if item.get('confidence',0)>=.5)
    if set(re.findall(r'[A-Z0-9]+',text.upper())) & GUIDE_WORDS:return ''
    if is_street_name(text):return 'Street-name blade'
    box=job['box'];width=box[2]-box[0];height=box[3]-box[1]
    # A small long green board is usually a street blade. Large guides remain.
    if height<=0 or width/height<2.0 or height>36:return ''
    crop=job['crop']
    if not crop.size:return ''
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    green=cv2.inRange(hsv,(35,65,35),(95,255,230))
    if cv2.countNonZero(green)/green.size>=.12:return 'Small green street-name blade'
    return ''

def interpret_text(items, labels):
    text=' '.join(x['text'] for x in items if x['confidence']>=.5).strip()
    upper=text.upper()
    if re.search(r'\b(?:SECOND|2ND|2)\s+RIGHT\b',upper):
        return dict(sign_id='second_right',sign_text='SECOND RIGHT',confidence=.85)
    if re.search(r'\bNEXT\s+EXIT\b',upper):
        return dict(sign_id='next_exit',sign_text='NEXT EXIT',confidence=.85)
    number=re.search(r'\b(\d{1,2})\b',upper)
    # A number alone needs independent evidence that this is a speed sign.
    if number and (('SPEED' in upper and 'LIMIT' in upper) or labels.get('speed limit sign',0)>=3):
        value=int(number[1])
        if 5<=value<=85 and value%5==0:
            return dict(sign_id='speed_limit',sign_text=f'SPEED LIMIT {value}',confidence=.9,value=value)
    for words,sign_id in [('FREEWAY ENTRANCE','freeway_entrance'),('LEFT TURN ONLY','left_turn_only'),('KEEP RIGHT','keep_right'),('NO RIGHT TURN','no_right_turn'),('SIGNAL AHEAD','signal_ahead'),('STOP AHEAD','stop_ahead'),('YIELD AHEAD','yield_ahead'),('NO TURN ON RED','no_turn_on_red'),('DO NOT ENTER','do_not_enter'),('ROAD WORK','road_work'),('ONE WAY','one_way'),('LANE ENDS','lane_ends')]:
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
        self.completed=set();self.inflight=set();self.announced=set();self.audio_last_label={}
        self.overlay_lock=threading.Lock();self.overlay_snapshot=(0.0,0.0,())
        self.specialist_tracks={};self.specialist_next=100000;self.tracks={};self.seen={};self.threads=[];self.cloud_count=0;self.cloud_disabled=False
        self.stats={'frames':0,'pending':0,'resolved':0,'tentative':0,'unresolved':0,'cloud_requests':0,'queue_full':0,'inference_ms':0,'audio_ready':0,'audio_failed':0,'cloud_cache_hits':0,'cloud_skipped':0,'filtered_street_names':0,'filtered_business_signs':0}

    def annotate(self, frame, ts):
        """Draw only on the displayed copy; OCR and saved examples stay unmarked."""
        with self.overlay_lock:
            stamp, wall, candidates = self.overlay_snapshot
        if abs(ts-stamp) > .5 or (not getattr(self.video, 'paused', False) and time.monotonic()-wall > .6):
            return frame
        with state.lock:
            rows={row.get('event_id'):dict(row) for row in state.detections}
        for ident, box in candidates:
            row=rows.get(ident)
            if row and row.get('recognition_status')=='excluded':continue
            if not row and ident in self.completed:continue
            x1,y1,x2,y2=box
            label=(row.get('sign_text') if row and row.get('recognition_status')!='pending' else 'Reading sign...') or 'Sign'
            label=label.replace('…','...')[:45]
            thickness=max(2,round(frame.shape[1]/640))
            cv2.rectangle(frame,(x1,y1),(x2,y2),(0,0,255),thickness)
            scale=max(.45,frame.shape[1]/2200)
            (width,height),baseline=cv2.getTextSize(label,cv2.FONT_HERSHEY_SIMPLEX,scale,1)
            left=max(0,min(x1,frame.shape[1]-width-8));top=max(height+6,y1-6)
            cv2.rectangle(frame,(left,top-height-5),(left+width+6,top+baseline),(0,0,180),-1)
            cv2.putText(frame,label,(left+3,top-2),cv2.FONT_HERSHEY_SIMPLEX,scale,(255,255,255),1,cv2.LINE_AA)
        return frame

    def active(self): return not self.stop_event.is_set() and state.drive_id==self.drive_id
    def audio_window(self):
        source=getattr(self.video,'source',None)
        return 8 if isinstance(source,(str,Path)) and Path(source).name=='night-drive.mp4' else 2

    def relevant(self,job):
        window=self.audio_window()
        if time.monotonic()-job['created']>=max(3,window):return False
        latest=self.video.latest() if hasattr(self.video,'latest') else None
        return latest is None or latest[1]-job['ts']<=window

    def metrics(self):
        if not self.active(): return
        with state.lock:
            for field,status in [('pending','pending'),('resolved','resolved'),('tentative','tentative'),('unresolved','unresolved')]:
                self.stats[field]=sum(r.get('recognition_status')==status for r in state.detections)
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
        with state.lock:
            reviewed=next((r for r in state.detections if r.get('event_id')==job['id'] and r.get('human_reviewed')),None)
        if reviewed:self.completed.add(job['id']);return False
        self.inflight.add(job["id"])
        self.publish_pending(job)
        self.jobs.put_nowait(job)
        return True

    def publish_pending(self,job):
        if not self.active():return
        row=dict(event_id=job['id'],drive_id=self.drive_id,ts_video=round(job['ts'],2),first_seen_video=job.get('first_seen_video',job['ts']),ts_wall=datetime.now(timezone.utc).isoformat(),model_version='lisa47-yoloe-clip-local-v1',specialist_sha256=SPECIALIST_SHA256,sign_id='unknown',sign_text='Reading sign…',recognition_status='pending',box=job['box'],confidence=job['confidence'],source='local',thumb_url=job['thumb'],meaning='',verified=False,safety_critical=False,speak_now=False)
        with state.lock:
            if not self.active():return
            old=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if old:
                if old.get('human_reviewed'):return
                if old.get('recognition_status')=='unresolved':self.stats['unresolved']=max(0,self.stats['unresolved']-1)
                elif old.get('recognition_status')=='tentative':self.stats['tentative']=max(0,self.stats['tentative']-1)
                old.update(row)
            else:state.detections.append(row)
        self.stats['pending']+=1;bus.publish({'type':'detection',**row})
    def drop_irrelevant(self,job,reason):
        """Hide non-driving candidates and prevent the same track being retried."""
        if not self.active():return
        with state.lock:
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if row is None or row.get('human_reviewed'):return
            state.detections.remove(row)
        self.completed.add(job['id'])
        self.stats['pending']=max(0,self.stats['pending']-1)
        key='filtered_business_signs' if 'business' in reason.lower() else 'filtered_street_names'
        self.stats[key]+=1
        bus.publish({'type':'detection_remove','drive_id':self.drive_id,'event_id':job['id'],'reason':reason})
        self.metrics()

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
        duplicate=False  # Keep distinct captures in the log; suppress repeated speech separately.
        if found and not found.get('tentative'):self.seen[key]=job['ts']
        with state.lock:
            if not self.active():return
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if row is None:return
            if row.get('human_reviewed'):self.completed.add(job['id']);return
            if duplicate:
                state.detections.remove(row)
            else:
                if sign_id=='speed_limit':
                    number=re.search(r'\b(\d{1,2})\b',text)
                    if number:found['value']=int(number[1]);text='SPEED LIMIT '+number[1]
                row.update(printed_text=printed_text(job.get('ocr', [])),first_seen_video=job.get('first_seen_video',job['ts']),sign_id=sign_id,sign_text=text,confidence=found.get('confidence',job['confidence']),recognition_status=('tentative' if found.get('tentative') else 'resolved') if found else 'unresolved',source=source,meaning=entry.get('meaning',''),meaning_es=entry.get('meaning_es',''),verified=entry.get('verified',False),safety_critical=entry.get('safety_critical',False),cue_text=entry.get('cue_text',''),recognition_error=error,recognition_seconds=round(time.monotonic()-job['created'],3),value=found.get('value'),symbol_similarity=found.get('symbol_similarity'),symbol_margin=found.get('symbol_margin'),tentative=found.get('tentative',False),specialist_label=found.get('specialist_label'),specialist_frames=found.get('specialist_frames'),demo_guess=found.get('demo_guess',False))
                row['driving_audio_eligible']=bool(row.get('printed_text') and entry)
                row=dict(row)
        if found and sign_id!='unknown' and not found.get('tentative'):self.completed.add(job['id'])
        self.stats['pending']-=1
        if duplicate:bus.publish({'type':'detection_remove','event_id':job['id']});return
        self.stats[('tentative' if found.get('tentative') else 'resolved') if found else 'unresolved']+=1
        register_prediction(row)
        with state.lock:
            current=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if current and current.get('human_reviewed'):return
            log_detection(row);bus.publish({'type':'detection_update',**row})
        self.metrics()
        if not driving_audio_allowed(row, getattr(self.video,'source',None)):
            self.audio_status(job,'skipped','',reason='Symbol-only sign: announcements are muted for this nighttime clip.')
            return
        # Descriptive announcements report what was observed; they do not instruct a maneuver.
        text=announcement(sign_id,found.get('value')) if found else ''
        if not text and found:text=row['sign_text'].capitalize()+'.'
        self.queue_announcement(job,text)

    def apply_saved_exclusion(self,job):
        review=lookup_review(job,getattr(self.video,'source',None))
        if review and review.get('action') in ('ignore','not_sign'):
            self.finish_review(job,review)
            return True
        return False

    def queue_announcement(self,job,text):
        if not text or self.apply_saved_exclusion(job):return
        if not self.relevant(job):
            self.audio_status(job,'skipped',text,reason='Sign is no longer current; use Replay on the card.')
            return
        with state.lock:
            row=next((r for r in state.detections if r.get('event_id')==job['id']),{})
            job['audio_priority']=row.get('safety_critical',False)
        key=text.casefold().strip()
        last=self.audio_last_label.get(key)
        if job['id'] in self.announced or (last is not None and abs(job['ts']-last)<8):
            self.audio_status(job,'skipped',text,reason='Repeated cue suppressed; sign remains available for practice.')
            return
        self.audio_status(job,'queued',text)
        try:self.audio_jobs.put_nowait((job,text))
        except queue.Full:
            self.audio_status(job,'skipped',text,reason='Speech queue is full; use Replay on the card.')
            return
        self.announced.add(job['id']);self.audio_last_label[key]=job['ts']

    def finish_review(self,job,review):
        observed = review.get('row', {}).get('printed_text')
        if observed is None: observed = ''
        with state.lock:
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if row is None:return
            duplicate=next((r for r in state.detections if r.get('event_id')!=job['id'] and r.get('feedback_record_key')==review['record_key']),None)
            if duplicate:
                state.detections.remove(row)
                self.completed.add(job['id'])
                bus.publish({'type':'detection_remove','drive_id':self.drive_id,'event_id':job['id']})
                return
            row.update(apply_review_fields(row,review))
            row["printed_text"] = observed
            row["driving_audio_eligible"] = bool(observed and get(row.get("sign_id")) and row.get("recognition_status") != "excluded")
            snapshot=dict(row)
        self.completed.add(job['id'])
        register_prediction(snapshot);log_detection(snapshot)
        bus.publish({'type':'detection_update',**snapshot});self.metrics()
        if snapshot.get('recognition_status')=='resolved' and not driving_audio_allowed(snapshot, getattr(self.video,'source',None)):
            job['human_replay']=True
            self.audio_status(job,'skipped','',reason='Symbol-only sign: announcements are muted for this nighttime clip.')
        if snapshot.get('recognition_status')=='resolved' and driving_audio_allowed(snapshot, getattr(self.video,'source',None)):
            text=announcement(snapshot['sign_id'],snapshot.get('value'))
            job['human_replay']=True
            self.queue_announcement(job,text)

    def can_announce(self,job):
        with state.lock:
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            return row is not None and row.get('recognition_status')!='excluded' and (not row.get('human_reviewed') or job.get('human_replay'))

    def local_loop(self):
        while not self.stop_event.is_set():
            try:job=self.jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                if job.get('frame') is not None:store_raw_example(job,self.drive_id,getattr(self.video,'source',None))
                review=lookup_review(job,getattr(self.video,'source',None))
                if review:self.finish_review(job,review);continue
                if time.monotonic()-job['created']>1.5:
                    self.finish(job,error='Local reading expired; saved for parked review.');continue
                texts=[];found=None
                for crop in job.get('crops',[job['crop']]):
                    items=self.ocr(crop);texts.extend(items)
                    found=interpret_text(items,job['labels'])
                    if found:break
                job['ocr']=texts
                from backend.local_symbols import business_sign_reason
                irrelevant=business_sign_reason(texts) or street_blade_reason(job,texts)
                if irrelevant:
                    self.drop_irrelevant(job,irrelevant);continue
                used_symbol=not found
                if used_symbol:
                    specialist=resolve_votes(job.get('specialist_votes',[]))
                    if specialist and min(job['crop'].shape[:2])<36:specialist['tentative']=True
                    if specialist and specialist.get('tentative'):specialist['demo_guess']=True
                    # Preserve the distinct combined symbol fallback absent from LISA.
                    if specialist:
                        found=specialist
                        if specialist.get('specialist_label')=='pedestrianCrossing':
                            symbol=self.classifier(job.get('crops',[job['crop']]))
                            if symbol and symbol.get('sign_id')=='bicycle_pedestrian_crossing' and not symbol.get('tentative'):
                                found=symbol
                    else:found=self.classifier(job.get('crops',[job['crop']]))
                if found and found.get('irrelevant_reason'):
                    self.drop_irrelevant(job,found['irrelevant_reason']);continue
                self.finish(job,found,source=('local-us-specialist' if found and found.get('specialist_label') else 'local-symbol') if used_symbol else 'local-ocr',error='' if found else 'Couldn’t read this sign confidently with local recognition.')
            except Exception:self.finish(job,error='Local OCR failed; detection continued.')
            finally:self.inflight.discard(job["id"]);self.jobs.task_done()
    def audio_status(self,job,status,text,url='',reason=''):
        if not self.active():return
        with state.lock:
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if row is None or (row.get('human_reviewed') and not job.get('human_replay')) or row.get('recognition_status')=='excluded':return
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
                if not self.active() or self.apply_saved_exclusion(job) or not self.can_announce(job):continue
                if not self.relevant(job):
                    self.audio_status(job,'skipped',text,reason='Announcement expired; use Replay on the card.');continue
                url=self.voice(text)
                if not self.active() or self.apply_saved_exclusion(job) or not self.can_announce(job):continue
                if not self.relevant(job):
                    self.audio_status(job,'skipped',text,reason='Sign is no longer current; use Replay on the card.');continue
                if url:
                    self.stats['audio_ready']+=1
                    self.audio_status(job,'ready',text,url)
                    self.metrics()
                    with state.lock:
                        cue_detection=next((dict(r) for r in state.detections if r.get('event_id')==job['id']),None)
                    bus.publish({'type':'cue','detection':cue_detection,'drive_id':self.drive_id,'event_id':job['id'],'text':text,'audio_url':url,'human_replay':bool(job.get('human_replay')),'priority':int(bool(job.get('audio_priority'))),'audio_window_seconds':self.audio_window(),'seconds_since_detected':round(time.monotonic()-job['created'],3)})
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
                        overlay=[]
                        for b in boxes:
                            if b.id is None:continue
                            tid=int(b.id.item());box=list(map(int,b.xyxy[0].tolist()));x1,y1,x2,y2=box;confidence=float(b.conf.item())
                            track=self.tracks.setdefault(tid,dict(first=ts,last=ts,hits=0,labels=Counter(),best=None,views=[],specialist_votes=[],score=0,submitted=False))
                            overlay.append((f"{self.drive_id}:{tid}:{int(track['first']*1000)}",tuple(box)))
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
                        with self.overlay_lock:self.overlay_snapshot=(ts,time.monotonic(),tuple(overlay))
                        for tid,track in list(self.tracks.items()):
                            ident=f"{self.drive_id}:{tid}:{int(track['first']*1000)}"
                            retry=track['submitted'] and ident not in self.completed and ident not in self.inflight and ts-track.get('attempt_ts',ts)>.5 and track['score']>track.get('attempt_score',0)*1.3
                            if (not track['submitted'] or retry) and track['hits']>=3 and track['best'] and (ts-track['first']>=.65 or ts-track['last']>.2):
                                crop,original,stamp,box,confidence=track['best'];job=dict(id=ident,first_seen_video=track['first'],frame=original,crop=crop,ts=stamp,box=box,confidence=confidence,labels=dict(track['labels']),specialist_votes=list(track['specialist_votes']),created=time.monotonic(),crops=[v[2] for v in track['views']] or [crop])
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
