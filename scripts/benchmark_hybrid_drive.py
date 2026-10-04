"""Run baseline and hybrid on real-time clip without UI, cloud, speech or log writes."""
import argparse,json,time,types,subprocess,statistics
from pathlib import Path
from unittest.mock import patch
from backend.video import VideoSource
from backend.state import state
from backend import local_vision
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--baseline-ref',required=True);parser.add_argument('--mode',choices=['before','after','both'],default='both');args=parser.parse_args()
comparison=root/'research/vision-architecture/hybrid-drive-comparison.json'
outputs=json.loads(comparison.read_text()) if comparison.exists() and args.mode!='both' else {}
for mode in (['before','after'] if args.mode=='both' else [args.mode]):
 module=local_vision
 if mode=='before':
  module=types.ModuleType('hybrid_baseline_vision')
  source=subprocess.check_output(['git','show',args.baseline_ref+':backend/local_vision.py'],text=True)
  symbols=types.ModuleType('hybrid_baseline_symbols')
  exec(subprocess.check_output(['git','show',args.baseline_ref+':backend/local_symbols.py'],text=True),symbols.__dict__)
  exec(source,module.__dict__);module.LocalSymbolClassifier=symbols.LocalSymbolClassifier
 state.drive_id='hybrid-benchmark-'+mode;state.detections=[];state.pipeline_metrics={}
 video=VideoSource(str(root/'data/drive.mp4'));events=[]
 with patch.object(module,'log_detection'),patch.object(module,'_thumb',return_value=''),patch.object(module.bus,'publish',side_effect=lambda row:events.append(dict(row))):
  detector=module.LocalDetector(video,voice=lambda text:'/audio/benchmark-disabled.mp3')
  start=time.monotonic();video.start();detector.start()
  while time.monotonic()-start<90:
   if any(e.get('type')=='detector_status' and e.get('status') in ('completed','error') for e in events):break
   time.sleep(.25)
  elapsed=time.monotonic()-start;video.stop();detector.stop()
 rows=[dict(row) for row in state.detections]
 inference=[e['inference_ms'] for e in events if e.get('type')=='pipeline_metrics' and e.get('inference_ms',0)>0]
 readings=[r['recognition_seconds'] for r in rows if r.get('recognition_seconds') is not None]
 result=dict(elapsed_seconds=round(elapsed,2),metrics=detector.stats,median_inference_ms=statistics.median(inference) if inference else None,median_reading_seconds=statistics.median(readings) if readings else None,rows=rows,removals=[e for e in events if e.get('type')=='detection_remove'],status=[e for e in events if e.get('type')=='detector_status'])
 outputs[mode]=result
 (root/f'research/vision-architecture/hybrid-drive-{mode}.json').write_text(json.dumps(result,indent=2))
 print(mode,json.dumps({k:v for k,v in result.items() if k not in ('rows','removals')}),flush=True)
 del detector
(root/'research/vision-architecture/hybrid-drive-comparison.json').write_text(json.dumps(outputs,indent=2))
