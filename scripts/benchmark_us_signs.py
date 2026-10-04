import torch,importlib,pathlib,cv2,time,json,numpy as np
from ultralytics import YOLO
root=pathlib.Path(__file__).resolve().parents[1];entries=json.loads((root/'research/validation/ground-truth/manifest.json').read_text())['entries'];out={}
for name in ['lisa47','jc11','jc26']:
 p=root/'.local/vision'/f'{name}.pt';safe=[]
 for g in torch.serialization.get_unsafe_globals_in_checkpoint(p):
  mod,attr=g.rsplit('.',1);safe.append(getattr(importlib.import_module(mod),attr))
 with torch.serialization.safe_globals(safe):ck=torch.load(p,map_location='cpu',weights_only=True)
 # Ultralytics only gets already safely loaded in-memory object.
 model=YOLO('yolov8m.yaml' if name=='lisa47' else ('yolo11s.yaml' if name=='jc11' else 'yolo26s.yaml'))
 model.model=ck['model'].float();model.task='detect';model.overrides={'task':'detect','model':str(p)}
 model.predict(np.zeros((720,1280,3),np.uint8),device='mps',imgsz=1280,verbose=False)
 results=[]
 for e in entries:
  item={'id':e['id'],'label':e.get('label'),'results':{}}
  for mode,key,size in [('frame','frame_path',1280),('crop','crop_path',320)]:
   im=cv2.imread(e[key]);start=time.perf_counter();r=model.predict(im,device='mps',imgsz=size,conf=.05,verbose=False)[0];ms=(time.perf_counter()-start)*1000
   item['results'][mode]={'ms':round(ms,1),'predictions':[{'label':model.names[int(b.cls)],'confidence':round(float(b.conf),3),'box':b.xyxy[0].tolist()} for b in r.boxes]}
  results.append(item)
 out[name]={'classes':model.names,'evaluations':results};print(name,json.dumps(results),flush=True)
(root/'research/vision-architecture/specialist-checkpoint-benchmark.json').write_text(json.dumps(out,indent=2))
