"""Download pinned specialist weights only; never execute remote repository code."""
import hashlib,urllib.request,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.us_signs import WEIGHTS,SHA256
URL='https://raw.githubusercontent.com/ali-haidous/yolo_trafficsign/ea6e5cc214bbe5b3ab406f141c3d67e307ccafbc/runs/detect/traffic_sign_yolov8m/weights/best.pt'
WEIGHTS.parent.mkdir(parents=True,exist_ok=True)
if not WEIGHTS.exists():
    contents=urllib.request.urlopen(URL).read()
    if hashlib.sha256(contents).hexdigest()!=SHA256:raise RuntimeError('Checkpoint checksum mismatch')
    WEIGHTS.write_bytes(contents)
if hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()!=SHA256:raise RuntimeError('Checkpoint checksum mismatch')
print('Verified LISA 47-class US specialist:',WEIGHTS)
