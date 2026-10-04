"""Reproducible held-out real-crop filter check; no cloud or application server."""
import json,time,cv2
from pathlib import Path
from backend.local_symbols import LocalSymbolClassifier,LABELS
from backend.local_vision import read_text,interpret_text,street_blade_reason
root=Path(__file__).resolve().parents[1]
classifier=LocalSymbolClassifier();rows=[]
review=json.loads((root/'research/validation/assessment.json').read_text())['rows']
negative_boxes={'raw-'+r['event_id'].split(':')[-1]:r['box'] for r in review}
entries=json.loads((root/'research/validation/ground-truth/manifest.json').read_text())['entries']
for entry in entries+[
 {'id':name,'crop_path':str(root/f'research/validation/{name}.png'),'label':'nontraffic'}
 for name in ['raw-113','raw-119','raw-124','raw-145','raw-218','raw-246']]:
 crop=cv2.imread(entry['crop_path']);start=time.perf_counter()
 items=read_text(crop);labels={}
 found=interpret_text(items,labels)
 job=dict(crop=crop,box=entry.get('box',negative_boxes.get(entry['id'],[0,0,crop.shape[1],crop.shape[0]])))
 filtered=street_blade_reason(job,items)
 scores=classifier._scores([crop,crop]);supported=[i for i,(id,_) in enumerate(LABELS) if id]
 baseline=max(supported,key=lambda i:sum(row[i] for row in scores))
 if not found and not filtered:
  original=classifier._scores;classifier._scores=lambda crops:scores
  try:found=classifier([crop,crop])
  finally:classifier._scores=original
  filtered=(found or {}).get('irrelevant_reason','')
 rows.append(dict(id=entry['id'],truth=entry['label'],ocr=items,before_nearest_guess=LABELS[baseline][0],filtered_reason=filtered,after=found,ms=round((time.perf_counter()-start)*1000,2)))
result={'method':'Held-out native GT6 crops plus6 previously reviewed upscaled hard-negative crops. Does not measure full-drive recall. No training/correction labels fed to inference.','rows':rows,'traffic_examples_removed':sum(bool(r['filtered_reason']) for r in rows if r['truth']!='nontraffic'),'nontraffic_removed':sum(bool(r['filtered_reason']) for r in rows if r['truth']=='nontraffic')}
path=root/'research/vision-architecture/hybrid-filter-benchmark.json';path.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
