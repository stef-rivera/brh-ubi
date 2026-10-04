"""Offline held-out crop evaluation, without cloud recognition/TTS calls.

Run: .venv-local/bin/python scripts/benchmark_local_reading.py
Setup: .venv-local/bin/python scripts/setup_local_symbols.py
"""
import json,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
from backend.config import ROOT
from backend.local_symbols import LocalSymbolClassifier,LABELS
from backend.local_vision import read_text,interpret_text
classifier=LocalSymbolClassifier()
manifest=json.loads((ROOT/'research/validation/ground-truth/manifest.json').read_text())
rows=[]
for entry in manifest['entries']:
 crop=cv2.imread(entry['crop_path']);start=time.monotonic()
 items=read_text(crop);found=interpret_text(items,{})
 scores=classifier._scores([crop])[0];order=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)
 rows.append(dict(id=entry['id'],expected=entry['label'],ocr=items,ocr_result=found,top_symbol=LABELS[order[0]][0],top_description=LABELS[order[0]][1],similarity=scores[order[0]],margin=scores[order[0]]-scores[order[1]],seconds=time.monotonic()-start))
for name in ['raw-113.png','raw-218.png','raw-246.png','raw-255.png','raw-259.png','raw-119.png','raw-124.png','raw-145.png']:
 crop=cv2.imread(str(ROOT/'research/validation'/name));start=time.monotonic()
 scores=classifier._scores([crop])[0];order=sorted(range(len(scores)),key=lambda i:scores[i],reverse=True)
 rows.append(dict(id=name,expected='abstain',top_symbol=LABELS[order[0]][0],top_description=LABELS[order[0]][1],similarity=scores[order[0]],margin=scores[order[0]]-scores[order[1]],seconds=time.monotonic()-start))
result={'method':'Held-out native crops; one view only, so symbol scores diagnostically reported, not live acceptance. Live requires two temporal views. Not a full accuracy estimate.','rows':rows}
output=ROOT/'research/vision-architecture/local-only-crop-benchmark.json';output.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
