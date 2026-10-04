"""Reuse successful readings of the same sign in the same unchanged video."""
import hashlib,json,threading
from pathlib import Path
from backend.config import ROOT

class RecognitionCache:
    def __init__(self, source, path=None):
        self.path=Path(path or ROOT/'.local/vision/readings.json')
        self.lock=threading.Lock()
        self.clip_key=''
        if isinstance(source,str) and Path(source).is_file():
            self.clip_key=hashlib.sha256(Path(source).read_bytes()).hexdigest()
        try:self.rows=json.loads(self.path.read_text())
        except (FileNotFoundError,ValueError):self.rows=[]
    @staticmethod
    def iou(a,b):
        x1,y1=max(a[0],b[0]),max(a[1],b[1]);x2,y2=min(a[2],b[2]),min(a[3],b[3])
        area=max(0,x2-x1)*max(0,y2-y1)
        union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area
        return area/union if union>0 else 0
    def lookup(self,job):
        if not self.clip_key:return None
        with self.lock:
            for r in reversed(self.rows):
                if r['clip']==self.clip_key and abs(r['ts']-job['ts'])<=.5 and self.iou(r['box'],job['box'])>=.55:
                    return dict(r['found'])
        return None
    def save(self,job,found):
        if not self.clip_key or found.get('confidence',0)<.85:return
        row=dict(clip=self.clip_key,ts=job['ts'],box=job['box'],found={k:found[k] for k in ('sign_id','sign_text','confidence','value') if k in found})
        with self.lock:
            self.rows.append(row);self.rows=self.rows[-200:]
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.rows,indent=2));tmp.replace(self.path)
