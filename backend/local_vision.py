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
# Green street blades are names, not driving instructions. The generic
# "traffic sign" phrase was also guessing boxes onto shops and lights.
DRIVER_SIGN_CLASSES = ['speed limit sign', 'stop sign', 'do not enter sign', 'road work sign', 'school crossing sign']

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

SIGN_WORDS = {
    'AHEAD','EXPECT','DELAY','DELAYS','ROAD','WORK','LANE','CLOSED','LEFT','RIGHT','SHOULDER',
    'DETOUR','MERGE','STOP','SPEED','LIMIT','YIELD','EXIT','NORTH','SOUTH','EAST','WEST','ONLY',
    'ONE','WAY','ENDS','ENTER','FLAGGER','SLOW','KEEP','RIGHT','PREPARE','TO','STOP','BUMP',
}

def _edit_distance(left, right):
    if abs(len(left)-len(right))>1:
        return 99
    prev=list(range(len(right)+1))
    for i, a in enumerate(left, 1):
        cur=[i]
        for j, b in enumerate(right, 1):
            cur.append(min(cur[-1]+1, prev[j]+1, prev[j-1]+(a!=b)))
        prev=cur
    return prev[-1]

def clean_sign_text(text):
    """Keep real sign words, and fix a one-letter miss. Unreadable tokens are dropped."""
    kept=[]
    for token in re.findall(r'[A-Z0-9]+', text.upper()):
        if token.isdigit() or token in SIGN_WORDS:
            kept.append(token)
            continue
        if len(token)<4:
            continue
        nearest=min(SIGN_WORDS, key=lambda word: _edit_distance(token, word))
        if _edit_distance(token, nearest)==1:
            kept.append(nearest)
    return ' '.join(kept)

TYPICAL_SIGNS = (
    ('ROAD WORK AHEAD EXPECT DELAY', 'road_work'),
    ('ROAD WORK AHEAD', 'road_work'),
    ('LEFT LANE CLOSED', 'road_work'),
    ('RIGHT LANE CLOSED', 'road_work'),
    ('SHOULDER WORK AHEAD', 'road_work'),
    ('DETOUR AHEAD', 'road_work'),
    ('LANE ENDS', 'lane_ends'),
    ('DO NOT ENTER', 'do_not_enter'),
    ('ONE WAY', 'one_way'),
)

def _unfinished_typical(cleaned):
    tokens=cleaned.split()
    if len(tokens)<2:
        return False
    for phrase, _sign_id in TYPICAL_SIGNS:
        words=phrase.split()
        if all(token in words for token in tokens) and len(tokens)<len(words):
            return True
    return False

def complete_typical_sign(text):
    """If the readable words pick one standard sign, use that whole sentence."""
    tokens=re.findall(r'[A-Z0-9]+', text.upper())
    best=None
    for phrase, sign_id in TYPICAL_SIGNS:
        words=phrase.split()
        score=sum(1 for word in words if any(_edit_distance(token, word)<=1 for token in tokens))
        if score<2 or score<len(words)-2:
            continue
        if best is None or score>best[0] or (score==best[0] and len(phrase)>len(best[1])):
            best=(score, phrase, sign_id)
    if best is None:
        return None
    return dict(sign_id=best[2], sign_text=best[1], confidence=.8)

def interpret_text(items, labels):
    raw=' '.join(x['text'] for x in items if x['confidence']>=.5).strip()
    cleaned=clean_sign_text(raw)
    text=cleaned or raw
    upper=text.upper()
    number=re.search(r'\b(\d{1,2})\b',upper)
    # A number alone needs independent evidence that this is a speed sign.
    if number and (('SPEED' in upper and 'LIMIT' in upper) or labels.get('speed limit sign',0)>=3):
        value=int(number[1])
        if 5<=value<=85 and value%5==0:
            return dict(sign_id='speed_limit',sign_text=f'SPEED LIMIT {value}',confidence=.9,value=value)
    for words,sign_id in [('DO NOT ENTER','do_not_enter'),('ROAD WORK','road_work'),('ONE WAY','one_way'),('LANE ENDS','lane_ends'),('YIELD','yield')]:
        if words in upper: return dict(sign_id=sign_id,sign_text=text,confidence=.85)
    if upper=='STOP' or (labels.get('red sign') and upper.replace(' ','')=='STOP'): return dict(sign_id='stop',sign_text='STOP',confidence=.9)
    # An orange board is a construction sign. A standard sentence can fill words the photo blurred.
    words_found=re.findall(r'[A-Z]{3,}', upper)
    orange_words=re.findall(r'[A-Z]{3,}', cleaned)
    if labels.get('orange sign'):
        completed=complete_typical_sign(raw)
        if completed:
            return completed
        # A tail such as EXPECT DELAY is part of a standard board. Wait for a clearer frame
        # instead of locking that short line in for 20 seconds.
        if _unfinished_typical(cleaned):
            return None
        if len(orange_words)>=2 and not is_street_name(cleaned):
            return dict(sign_id='road_work',sign_text=cleaned,confidence=.8)
    # Green guide signs (exits, directions) are useful. Street blades are dropped by name.
    if labels.get('green sign') and words_found and not is_street_name(text):
        return dict(sign_id='unknown',sign_text=text,confidence=.8)
    return None

def is_street_name(text):
    """A blade such as Pleasant Hill Rd. A sentence that also says ahead, work, or delay is a sign."""
    upper=text.upper()
    if not re.search(r'\b(RD|ROAD|AVE|AV|ST|STREET|BLVD|DR)\b', upper) or len(upper.strip())<=5:
        return False
    message={'AHEAD','EXPECT','DELAY','DELAYS','WORK','LANE','CLOSED','EXIT','DETOUR','MERGE','SPEED','LIMIT','STOP','YIELD','ONLY'}
    return not bool(set(re.findall(r'[A-Z0-9]+', upper)) & message)

def _mask_boxes(mask, frame, name, min_side, max_side, aspect_ok):
    """Keep a colored patch that is sign-shaped and sits above the road, not in the sky."""
    height, width = frame.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        side = min(w, h)
        if side < min_side or max(w, h) > max_side or w == 0 or h == 0:
            continue
        center_y = (y + h / 2) / height
        if not 0.18 <= center_y <= 0.72:
            continue
        if w * h > 0.06 * width * height:
            continue
        extent = cv2.contourArea(contour) / float(w * h)
        aspect = w / float(h)
        if extent < 0.45 or not aspect_ok(aspect):
            continue
        confidence = min(0.95, 0.55 + extent * 0.35)
        boxes.append(dict(name=name, box=[x, y, x + w, y + h], confidence=confidence))
    return boxes

def color_sign_boxes(frame):
    """Propose sign crops from MUTCD colors. Tiny green street blades are left out; larger green guide signs are kept."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, (0, 110, 110), (18, 255, 255))
    red = cv2.bitwise_or(cv2.inRange(hsv, (0, 120, 70), (8, 255, 255)), cv2.inRange(hsv, (170, 120, 70), (180, 255, 255)))
    white = cv2.inRange(hsv, (0, 0, 175), (180, 45, 255))
    yellow = cv2.inRange(hsv, (18, 90, 150), (40, 255, 255))
    green = cv2.inRange(hsv, (35, 80, 40), (95, 255, 220))
    found = []
    found += _mask_boxes(orange, frame, 'orange sign', 24, 240, lambda aspect: 1.15 <= aspect <= 3.4)
    found += _mask_boxes(red, frame, 'red sign', 24, 180, lambda aspect: 0.65 <= aspect <= 1.35)
    found += _mask_boxes(white, frame, 'white sign', 28, 140, lambda aspect: 0.45 <= aspect <= 0.95)
    found += _mask_boxes(yellow, frame, 'yellow sign', 48, 220, lambda aspect: 0.6 <= aspect <= 1.8)
    found += _mask_boxes(green, frame, 'green sign', 36, 420, lambda aspect: 1.2 <= aspect <= 6)
    return found

# A kept reading quiets every finder that would report that same kind of sign.
FINDER_QUIET = {
    'speed limit sign': ('speed_limit',),
    'white sign': ('speed_limit',),
    'orange sign': ('road_work',),
    'road work sign': ('road_work',),
    'red sign': ('stop', 'yield', 'do_not_enter'),
    'stop sign': ('stop',),
    'do not enter sign': ('do_not_enter',),
    'school crossing sign': ('school_zone',),
    'yellow sign': ('school_zone', 'pedestrian_crossing'),
    'green sign': ('guide',),
}

def last_kept_at(recent, name):
    times = [recent[key] for key in FINDER_QUIET.get(name, ()) if key in recent]
    return max(times) if times else None

def box_is_clearer(last_try, ts, side):
    """A failed read does not block the next one. Retry when the board grows, or after a short pause."""
    if last_try is None:
        return True
    prev_ts, prev_side = last_try
    return side >= prev_side * 1.3 or ts - prev_ts >= 0.8

def should_read_sign(ts, confidence, min_side, last_seen, last_read):
    """Read a sign whose box has moved. One sure box is enough. A weaker box is read after it shows up again within a second. The same kind of sign stays quiet for 20 seconds after a real reading."""
    if min_side < 24 or confidence < .55:
        return False
    if last_read is not None and ts - last_read < 20:
        return False
    repeated = last_seen is not None and 0 < ts - last_seen <= 1
    return repeated or confidence >= .7

class LocalDetector:
    def __init__(self, video, fallback=None, model=None, ocr=read_text, voice=speak):
        self.video=video; self.drive_id=state.drive_id; self.fallback=fallback; self.ocr=ocr; self.voice=voice
        if model is None:
            from ultralytics import YOLOE
            if not MODEL_PATH.exists(): raise RuntimeError('Local sign model is missing. Run scripts/setup_local_vision.py first.')
            model=YOLOE(str(MODEL_PATH))
            model.set_classes(DRIVER_SIGN_CLASSES)
            import numpy as np
            model.predict(np.zeros((720,1280,3),dtype="uint8"),imgsz=960,device="mps",verbose=False)
        self.model=model
        self.stop_event=threading.Event(); self.jobs=queue.Queue(8); self.cloud_jobs=queue.Queue(6); self.audio_jobs=queue.Queue(8)
        self.tracks={};self.seen={};self.recent_kept={};self.class_seen={};self.last_try={};self.threads=[];self.cloud_count=0;self.cloud_disabled=False
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
    def drop(self,job):
        if not self.active():return
        with state.lock:
            if not self.active():return
            row=next((r for r in state.detections if r.get('event_id')==job['id']),None)
            if row is None:return
            state.detections.remove(row)
        self.stats['pending']-=1
        bus.publish({'type':'detection_remove','event_id':job['id']})
        self.metrics()
    def finish(self,job,found=None,source='local-ocr',error=''):
        if not self.active():return
        found=found or {};sign_id=found.get('sign_id','unknown');entry=get(sign_id) or {}
        text=found.get('sign_text') or entry.get('sign_text') or 'Unresolved sign'
        key=(sign_id,text.casefold())
        kept_key='guide' if sign_id=='unknown' else sign_id
        last=self.seen.get(key)
        type_last=self.recent_kept.get(kept_key)
        duplicate=bool(found) and ((last is not None and abs(job['ts']-last)<10) or (type_last is not None and abs(job['ts']-type_last)<20))
        if found and not duplicate:
            self.seen[key]=job['ts']
            self.recent_kept[kept_key]=job['ts']
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
                joined=' '.join(item['text'] for item in texts if item['confidence']>=.5)
                if is_street_name(joined):
                    self.drop(job)
                else:
                    found=interpret_text(texts,job['labels'])
                    if found:self.finish(job,found)
                    elif self.fallback and not self.cloud_disabled and self.cloud_count<8:
                        try:
                            self.cloud_jobs.put_nowait(job);self.cloud_count+=1;self.stats['cloud_requests']=self.cloud_count
                        except queue.Full:self.drop(job)
                    else:self.drop(job)
            except Exception:self.drop(job)
            finally:self.jobs.task_done()
    def cloud_loop(self):
        while not self.stop_event.is_set():
            try:job=self.cloud_jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                if time.monotonic()-job["created"]>25 or self.cloud_disabled:
                    self.drop(job);continue
                rows=self.fallback(job['crop'])
                candidates=[r for r in rows if r.get('confidence',0)>=.75 and (r.get('sign_id')!='unknown' or r.get('sign_text')) and not is_street_name(r.get('sign_text') or '')]
                # Multiple conflicting signs within one crop do not constitute a verified reading.
                found=max(candidates,key=lambda r:r['confidence']) if len(candidates)==1 else None
                if found:self.finish(job,found,source='local+chatgpt')
                else:self.drop(job)
            except Exception:
                self.cloud_disabled=True;self.drop(job)
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
    def _offer_read(self, frame, ts, name, box, confidence):
        x1,y1,x2,y2=box
        min_side=min(x2-x1,y2-y1)
        last_read=last_kept_at(self.recent_kept, name)
        if should_read_sign(ts, confidence, min_side, self.class_seen.get(name), last_read) and box_is_clearer(self.last_try.get(name), ts, min_side) and not self.jobs.full():
            self.last_try[name]=(ts, min_side)
            crop=frame[max(0,y1-16):min(frame.shape[0],y2+16),max(0,x1-16):min(frame.shape[1],x2+16)].copy()
            labels={name:1}
            if name in ('speed limit sign','white sign') and confidence>=.7:
                labels['speed limit sign']=3
            job=dict(id=f'{self.drive_id}:{name}:{int(ts*10)}',first_seen_video=ts,crop=crop,ts=ts,box=box,confidence=confidence,labels=labels,created=time.monotonic())
            job['thumb']=_thumb(frame,box,self.drive_id,int(ts*10))
            self.publish_pending(job);self.jobs.put_nowait(job)
        if min_side>=24 and confidence>=.55:
            self.class_seen[name]=ts
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
                        result=self.model.track(frame,imgsz=960,device='mps',conf=.15,agnostic_nms=True,persist=True,tracker=str(TRACKER_PATH),verbose=False)[0]
                        self.stats['frames']+=1;self.stats['inference_ms']=round((time.monotonic()-start)*1000,1)
                        for proposal in color_sign_boxes(frame):
                            self._offer_read(frame, ts, proposal['name'], proposal['box'], proposal['confidence'])
                        for b in result.boxes:
                            name=self.model.names[int(b.cls.item())]
                            box=list(map(int,b.xyxy[0].tolist()));x1,y1,x2,y2=box;confidence=float(b.conf.item())
                            # ByteTrack only confirms a box that still overlaps the previous sample.
                            # An approaching sign moves farther than that, so a repeated or sure box is read anyway.
                            if b.id is None:
                                self._offer_read(frame, ts, name, box, confidence)
                                continue
                            tid=int(b.id.item())
                            track=self.tracks.setdefault(tid,dict(first=ts,last=ts,hits=0,labels=Counter(),best=None,score=0,submitted=False))
                            track['last']=ts;track['hits']+=1;track['labels'][self.model.names[int(b.cls.item())]]+=1
                            score=(x2-x1)*(y2-y1)*confidence
                            if score>track['score'] and min(x2-x1,y2-y1)>=10:
                                crop=frame[max(0,y1-6):min(frame.shape[0],y2+6),max(0,x1-6):min(frame.shape[1],x2+6)].copy()
                                track.update(score=score,best=(crop,frame.copy(),ts,box,confidence))
                        for tid,track in list(self.tracks.items()):
                            dominant=max(track['labels'], key=track['labels'].get) if track['labels'] else ''
                            kept_at=last_kept_at(self.recent_kept, dominant) if dominant else None
                            if kept_at is not None and ts-kept_at<20:
                                track['submitted']=True
                            elif not track['submitted'] and track['hits']>=3 and track['best'] and (ts-track['first']>=2 or ts-track['last']>.2):
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
