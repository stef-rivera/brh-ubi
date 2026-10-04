import os
os.environ['YOLO_CONFIG_DIR']='/tmp/Ultralytics'
from ultralytics import YOLOE
import cv2,time,json,numpy as np
from pathlib import Path
root=Path('/tmp/brh-sign-bench')
model=YOLOE('yoloe-26s-seg.pt')
model.set_classes(['traffic sign','school crossing sign','speed limit sign','street name sign','stop sign','do not enter sign','road work sign'])
cap=cv2.VideoCapture('/Users/temiadebowale/Documents/brh-ubi/data/drive.mp4');rows=[];tiles=[]
for t in range(60):
 cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,f=cap.read()
 if not ok:continue
 start=time.perf_counter();r=model.predict(f,imgsz=1280,device='mps',conf=.15,verbose=False)[0];ms=(time.perf_counter()-start)*1000
 boxes=[dict(label=model.names[int(b.cls.item())],conf=round(float(b.conf.item()),3),box=[round(x,1) for x in b.xyxy[0].tolist()]) for b in r.boxes]
 rows.append(dict(t=t,ms=ms,boxes=boxes))
 if boxes:
  img=r.plot(masks=False);img=cv2.resize(img,(640,360));cv2.putText(img,str(t)+'s',(12,25),0,1,(0,0,255),2);tiles.append(img)
(root/'open-vocab.json').write_text(json.dumps(rows,indent=2))
for n in range(0,len(tiles),12):
 chunk=tiles[n:n+12];chunk += [np.zeros_like(chunk[0])]*(12-len(chunk));cv2.imwrite(str(root/f'open-vocab-{n//12}.jpg'),np.vstack([np.hstack(chunk[i:i+3]) for i in range(0,12,3)]))
print('median_ms',np.median([r['ms'] for r in rows[3:]]),'p95_ms',np.percentile([r['ms'] for r in rows[3:]],95),'frames',sum(bool(r['boxes']) for r in rows),flush=True)
