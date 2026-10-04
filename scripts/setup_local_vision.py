"""Run with .venv-local/bin/python after installing requirements-local.txt."""
from pathlib import Path
import os, sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from backend.local_vision import DRIVER_SIGN_CLASSES
folder=root/'.local/vision'
folder.mkdir(parents=True,exist_ok=True)
os.environ['YOLO_CONFIG_DIR']=str(folder/'config')
os.chdir(folder)
from ultralytics import YOLOE
model=YOLOE('yoloe-26s-seg.pt')
model.set_classes(DRIVER_SIGN_CLASSES)
model.save(str(folder/'signs.pt'))
print('Local sign detector ready.')
