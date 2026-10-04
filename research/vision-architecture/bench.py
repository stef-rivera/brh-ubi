import os
os.environ['YOLO_CONFIG_DIR']='/tmp/brh-sign-bench/config'
import cv2,time,json,torch,numpy as np
from ultralytics import YOLO
from pathlib import Path
root=Path('/tmp/brh-sign-bench')
cap=cv2.VideoCapture('/Users/temiadebowale/Documents/brh-ubi/data/drive.mp4')
frames=[]
for t in range(60):
 cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,f=cap.read()
 if ok: frames.append((t,f))
print('torch',torch.__version__,'mps',torch.backends.mps.is_available(),flush=True)
for ver in ['v8','v11']:
 model=YOLO(str(root/f'{ver}.pt'))
 print(ver,model.names,flush=True)
 for size in [640,1280]:
  device='mps' if torch.backends.mps.is_available() else 'cpu'
  for _ in range(3):model.predict(frames[0][1],imgsz=size,device=device,verbose=False)
  rows=[];times=[];tiles=[]
  for t,f in frames:
   start=time.perf_counter();r=model.predict(f,imgsz=size,device=device,conf=.25,verbose=False)[0]
   times.append((time.perf_counter()-start)*1000)
   boxes=[dict(label=model.names[int(b.cls.item())],conf=round(float(b.conf.item()),3),box=[round(x,1) for x in b.xyxy[0].tolist()]) for b in r.boxes]
   rows.append(dict(t=t,boxes=boxes))
   if boxes:
    img=r.plot();img=cv2.resize(img,(640,360));cv2.putText(img,str(t)+'s',(12,25),0,1,(0,0,255),2);tiles.append(img)
  stats=dict(model=ver,size=size,device=device,median_ms=float(np.median(times)),p95_ms=float(np.percentile(times,95)),frames_with_boxes=sum(bool(r['boxes']) for r in rows),detections=sum(len(r['boxes']) for r in rows))
  (root/f'{ver}-{size}.json').write_text(json.dumps(dict(stats=stats,rows=rows),indent=2))
  for n in range(0,len(tiles),12):
   chunk=tiles[n:n+12];chunk += [np.zeros_like(chunk[0])]*(12-len(chunk));cv2.imwrite(str(root/f'{ver}-{size}-{n//12}.jpg'),np.vstack([np.hstack(chunk[i:i+3]) for i in range(0,12,3)]))
  print(stats,flush=True)
