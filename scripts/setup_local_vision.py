"""Run with .venv-local/bin/python after installing requirements-local.txt."""
from pathlib import Path
import os
root=Path(__file__).resolve().parents[1]
folder=root/'.local/vision'
folder.mkdir(parents=True,exist_ok=True)
os.environ['YOLO_CONFIG_DIR']=str(folder/'config')
os.chdir(folder)
from ultralytics import YOLOE
model=YOLOE('yoloe-26s-seg.pt')
model.set_classes(['traffic sign','school crossing sign','speed limit sign','street name sign','stop sign','do not enter sign','road work sign'])
model.save(str(folder/'signs.pt'))
print('Local sign detector ready.')
