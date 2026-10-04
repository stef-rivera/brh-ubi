import cv2,json,numpy as np
from pathlib import Path
root=Path('/tmp/brh-sign-bench');cap=cv2.VideoCapture('/Users/temiadebowale/Documents/brh-ubi/data/drive.mp4');tiles=[]
for r in json.load(open(root/'open-vocab.json')):
 if r['t'] not in [1,9,10,16,17,20,26,36,39,40,53,57]:continue
 cap.set(cv2.CAP_PROP_POS_MSEC,r['t']*1000);ok,f=cap.read()
 for i,b in enumerate(r['boxes']):
  if b['conf']<.3:continue
  x1,y1,x2,y2=map(int,b['box']);crop=f[max(0,y1-5):min(720,y2+5),max(0,x1-5):min(1280,x2+5)]
  cv2.imwrite(str(root/f'crop-{r["t"]}-{i}.jpg'),crop)
  canvas=np.full((200,240,3),240,np.uint8);h,w=crop.shape[:2];scale=min(230/w,155/h);res=cv2.resize(crop,(int(w*scale),int(h*scale)));canvas[:res.shape[0],:res.shape[1]]=res;cv2.putText(canvas,f'{r["t"]}s {b["label"]}',(3,175),0,.42,(0,0,0),1);tiles.append(canvas)
while len(tiles)%5:tiles.append(np.zeros_like(tiles[0]))
cv2.imwrite(str(root/'crops.jpg'),np.vstack([np.hstack(tiles[i:i+5]) for i in range(0,len(tiles),5)]))
