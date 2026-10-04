"""Pretrained LISA 47-class US specialist; no runtime training or cloud calls."""
import importlib
import re
from collections import Counter
from backend.config import ROOT
from backend.vision_runtime import mps_lock

WEIGHTS = ROOT / '.local/vision/lisa47.pt'
SHA256 = 'cfaba5cad447b2a251b722015bd66fc613845b0d151d2d576d6a2a9198aa1b7b'
MAP = {'school':'school_zone','stop':'stop','yield':'yield','doNotEnter':'do_not_enter',
       'noLeftTurn':'no_left_turn','laneEnds':'lane_ends'}


def overlap(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    intersection=x*y
    return intersection/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)


def resolve_votes(votes):
    """Require multiple frames; model confidence is not calibrated probability."""
    if len(votes)<3:return None
    name,count=Counter(v['label'] for v in votes).most_common(1)[0]
    matched=[v for v in votes if v['label']==name]
    confidence=sum(v['confidence'] for v in matched)/len(matched)
    if count<3 or count/len(votes)<.6 or confidence<.25:return None
    value=None
    speed=re.fullmatch(r'speedLimit(\d+)',name)
    sign_id=MAP.get(name,'unknown')
    text=re.sub(r'(?<!^)(?=[A-Z])',' ',name).upper()
    if speed:sign_id='speed_limit';value=int(speed[1]);text=f'SPEED LIMIT {value}'
    if name=='school':text='SCHOOL'
    if name=='speedLimitUrdbl':text='SPEED LIMIT, NUMBER UNREADABLE'
    # Combined bicycle/pedestrian is outside LISA; generic pedestrian stays tentative.
    tentative=confidence<.65 or count/len(votes)<.8 or sign_id=='unknown'
    return dict(sign_id=sign_id,sign_text=text,confidence=round(confidence,3),value=value,
                tentative=tentative,specialist_label=name,specialist_frames=count)


class USSignSpecialist:
    def __init__(self):
        import hashlib, torch
        from ultralytics import YOLO
        if not WEIGHTS.exists():raise RuntimeError('US sign specialist missing. Run scripts/setup_us_signs.py.')
        if hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()!=SHA256:raise RuntimeError('US specialist checkpoint checksum mismatch.')
        allowed=[]
        for name in torch.serialization.get_unsafe_globals_in_checkpoint(WEIGHTS):
            if not name.startswith(('torch.nn.modules.','ultralytics.nn.')):
                raise RuntimeError('Unexpected checkpoint class: '+name)
            module,attribute=name.rsplit('.',1)
            allowed.append(getattr(importlib.import_module(module),attribute))
        with torch.serialization.safe_globals(allowed):
            checkpoint=torch.load(WEIGHTS,map_location='cpu',weights_only=True)
        self.model=YOLO('yolov8m.yaml')
        self.model.model=checkpoint['model'].float();self.model.task='detect'
        self.model.overrides={'task':'detect','model':str(WEIGHTS)}
        import numpy as np
        self.predict(np.zeros((720,1280,3),dtype='uint8'))

    def predict(self,frame):
        with mps_lock:
            result=self.model.predict(frame,imgsz=1280,device='mps',conf=.25,agnostic_nms=True,verbose=False)[0]
        return [dict(label=self.model.names[int(b.cls.item())],confidence=float(b.conf.item()),box=b.xyxy[0].tolist()) for b in result.boxes]
