import os
os.environ['YOLO_CONFIG_DIR']='/tmp/Ultralytics'
from ultralytics import YOLOE
import cv2,time,json,numpy as np,Vision,Foundation
from pathlib import Path
root=Path('/tmp/brh-sign-bench');model=YOLOE(str(root/'yoloe-26s-seg.pt'));model.set_classes(['traffic sign','school crossing sign','speed limit sign','street name sign','stop sign','do not enter sign','road work sign'])
cap=cv2.VideoCapture('/Users/temiadebowale/Documents/brh-ubi/data/drive.mp4');fps=cap.get(cv2.CAP_PROP_FPS);tracks={};times=[];index=0
while True:
 ok,f=cap.read()
 if not ok:break
 index+=1
 if (index-1)%round(fps/10):continue
 t=(index-1)/fps;start=time.perf_counter();r=model.track(f,imgsz=1280,device='mps',conf=.15,agnostic_nms=True,persist=True,tracker='bytetrack.yaml',verbose=False)[0];times.append((time.perf_counter()-start)*1000)
 for b in r.boxes:
  if b.id is None:continue
  tid=int(b.id.item());conf=float(b.conf.item());x1,y1,x2,y2=map(int,b.xyxy[0].tolist());crop=f[max(0,y1-5):min(720,y2+5),max(0,x1-5):min(1280,x2+5)].copy()
  row=tracks.setdefault(tid,dict(id=tid,first=t,last=t,hits=0,labels={},candidates=[]));row['last']=t;row['hits']+=1;label=model.names[int(b.cls.item())];row['labels'][label]=row['labels'].get(label,0)+1
  score=(x2-x1)*(y2-y1)*conf;row['candidates'].append((score,t,crop));row['candidates']=sorted(row['candidates'],key=lambda x:x[0],reverse=True)[:3]
rows=[];tiles=[]
for tid,row in tracks.items():
 if row['hits']<3:continue
 candidates=row.pop('candidates');row['ocr']=[]
 for i,(_,t,crop) in enumerate(candidates):
  p=root/f'track-{tid}-{i}.jpg';cv2.imwrite(str(p),crop);f=cv2.resize(crop,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC);data=cv2.imencode('.png',f)[1].tobytes();handler=Vision.VNImageRequestHandler.alloc().initWithData_options_(Foundation.NSData.dataWithBytes_length_(data,len(data)),None);req=Vision.VNRecognizeTextRequest.alloc().init();req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate);req.setUsesLanguageCorrection_(False);req.setMinimumTextHeight_(.01);handler.performRequests_error_([req],None);row['ocr'].append(dict(t=t,texts=[str(o.topCandidates_(1)[0].string()) for o in req.results() or []]))
 crop=candidates[0][2];canvas=np.full((200,240,3),240,np.uint8);h,w=crop.shape[:2];s=min(230/w,150/h);res=cv2.resize(crop,(int(w*s),int(h*s)));canvas[:res.shape[0],:res.shape[1]]=res;cv2.putText(canvas,f'#{tid} {row["first"]:.1f}-{row["last"]:.1f}s ({row["hits"]})',(3,175),0,.4,(0,0,0),1);tiles.append(canvas);rows.append(row)
while len(tiles)%5:tiles.append(np.zeros_like(tiles[0]))
cv2.imwrite(str(root/'tracks.jpg'),np.vstack([np.hstack(tiles[i:i+5]) for i in range(0,len(tiles),5)]))
summary=dict(frames=len(times),median_ms=float(np.median(times[5:])),p95_ms=float(np.percentile(times[5:],95)),tracks=rows)
(root/'tracks.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
