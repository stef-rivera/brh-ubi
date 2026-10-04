"""Offline event/crop audit. Never starts playback or invokes cloud/TTS APIs."""
from __future__ import annotations
import argparse, html, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Human review of raw video crops; model predictions are not treated as ground truth.
REVIEW = {
 '1': ('school_crossing', 'Recognized', 'Fluorescent pentagon with two pedestrian figures; valid school crossing category, not evidence of an active school speed restriction.'),
 '30': ('street_name', 'Recognized', 'Pleasant Hill Rd is legible in the original selected crop.'),
 '46': ('regulatory_sign', 'Cloud abstention', 'NO TURN ON RED is visually readable. Lower supplementary lines remain uncertain. Native box38x48; no need to force-read the smaller lines.'),
 '92': ('speed_limit', 'Recognized', 'Speed limit40 is visually legible; selected detector box undercovers rightmost digit but padded crop preserves it.'),
 '113': ('nontraffic_false_positive', 'Cloud abstention', 'Building roof/fascia behind the speed sign; not an actionable road sign.'),
 '119': ('street_name', 'Cloud abstention; tiny crop', 'Real green street blade41x16. Exact street name is not confidently human-readable from selected crop.'),
 '124': ('street_name', 'Cloud abstention; tiny crop', 'Real green street blade42x16. Text partially visible; do not assign exact spelling without a clearer frame.'),
 '145': ('street_name', 'Cloud abstention; tiny crop', 'Real green street blade42x13. Native text is too small/blurred for confident spelling.'),
 '161': ('speed_limit', 'Recognized', 'Second physical speed limit40 sign, not a repeated detection of the first sign.'),
 '171': ('road_work', 'Cloud abstention; incorrect late crop', 'At39.3s a full orange board visibly reads ROAD WORK / AHEAD / EXPECT DELAYS. Selected39.89s detector box undercovers top/right and sign crosses image boundary. Earlier crops are better despite lower area.'),
 '218': ('business_or_property_sign', 'Cloud budget exhausted', 'Left-side business/property advertising board. Exact business text unneeded for road coaching; this is not a road instruction.'),
 '246': ('business_or_property_sign', 'Cloud budget exhausted', 'Another left-side business/property advertising board; distinct design from218. Not counted as a traffic sign.'),
 '255': ('supplementary_plaque_fragment', 'Cloud budget exhausted', 'AHEAD plaque on the same post as final bicycle/pedestrian crossing258. Merge assembly instead of presenting an independent unresolved sign.'),
 '259': ('supplementary_plaque_fragment', 'Cloud budget exhausted', 'Detector box spans a partly visible supplementary board; same final crossing assembly as255/258. Plaque text beyond AHEAD is uncertain.'),
 '258': ('bicycle_pedestrian_crossing', 'Cloud budget exhausted', 'Clearly visible bicycle and pedestrian pictograms on a fluorescent diamond at57.21s. This is not the school pentagon; early broad detector labels must not be authoritative.'),
}

def main():
 p=argparse.ArgumentParser();p.add_argument('--events',type=Path,default=ROOT/'research/vision-architecture/integration-pass-2.json');p.add_argument('--output',type=Path,default=ROOT/'research/validation');a=p.parse_args();events=json.loads(a.events.read_text());latest={}
 for event in events:
  if event.get('type') in ('detection','detection_update'):latest[event['event_id']]=event
 rows=[]
 for event in latest.values():
  tid=event['event_id'].rsplit(':',1)[-1];category,cause,evidence=REVIEW.get(tid,('not_manually_reviewed','','')) if '22-32-20' in event['event_id'] else ('not_manually_reviewed','','')
  row={k:event.get(k) for k in ('event_id','ts_video','box','recognition_status','sign_id','sign_text','source','recognition_error','recognition_seconds','thumb_url')};row.update(native_size=[event['box'][2]-event['box'][0],event['box'][3]-event['box'][1]],manual_category=category,primary_cause=cause,evidence=evidence);rows.append(row)
 metrics=[e for e in events if e.get('type')=='pipeline_metrics'];cues=[e for e in events if e.get('type')=='cue'];unresolved=[r for r in rows if r['recognition_status']=='unresolved']
 report=dict(events_file=str(a.events),method='Offline manual review of selected original frames, annotated thumbnails and neighboring frames; no cloud calls. These are candidate-level classifications, not a complete per-frame accuracy benchmark.',rows=rows,summary=dict(total_candidates=len(rows),resolved=sum(r['recognition_status']=='resolved' for r in rows),unresolved=len(unresolved),unresolved_categories=dict(Counter(r['manual_category'] for r in unresolved)),cloud_abstentions=sum(r['source']=='local+chatgpt' for r in unresolved),budget_denials=sum('limit was reached' in (r['recognition_error'] or '') for r in unresolved),cue_events=len(cues),final_metrics=metrics[-1] if metrics else {}),diagnosis=['Unresolved count combines abstention, budget denial, irrelevant boards, tiny text, clipped crops and fragmented plaque tracks; it does not measure dataset adequacy.','Original one-cue result follows catalog policy: only school sign was recognized as safety_critical; speed limits and unknown street names were intentionally silent.','ChatGPT ran in two background workers but was capped at8 submissions per drive. Late candidates were never sent; successful readings only spoke if catalog policy and20-second freshness gate allowed them.'],recommendations=['Record separate resolution reasons for model abstention versus cloud budget versus nontraffic rejection.','Retain several raw crops; penalize clipping and box undercoverage, and revisit abstentions only after meaningful quality improvement.','Prioritize distinctive regulatory/warning symbol crops over unreadable street names and business boards within cloud budget.','Merge supplementary plaques into their parent sign assembly; never interpret AHEAD alone as a maneuver.','Use explicit descriptive demo speech policy including speed values; preserve quiet driving mode as a separate choice.','Never rely on Apple OCR confidence alone: noisy39.7s board readings ROAD WOF/ASEAD/FAPECT DE each had confidence1.0.'])
 a.output.mkdir(parents=True,exist_ok=True);(a.output/'assessment.json').write_text(json.dumps(report,indent=2))
 cells=[]
 for row in rows:
  thumb=ROOT/'data'/row['thumb_url'].lstrip('/');rel=Path('../..')/thumb.relative_to(ROOT);cells.append('<tr><td>'+html.escape(row['event_id'].rsplit(':',1)[-1])+f"<br>{row['ts_video']}s<br>{row['native_size']}</td><td><img width=220 src='"+html.escape(str(rel))+"'></td><td>"+html.escape(row['recognition_status'])+'<br>'+html.escape(row['source'])+'</td><td>'+html.escape(row['manual_category'])+'<br>'+html.escape(row['primary_cause'])+'</td><td>'+html.escape(row['evidence'])+'</td></tr>')
 doc='<!doctype html><meta charset=utf-8><title>Independent clip audit</title><style>body{font:16px system-ui;margin:24px;max-width:1400px}td,th{padding:12px;border:1px solid #ccc;vertical-align:top}table{border-collapse:collapse}img{image-rendering:auto}</style><h1>Independent clip audit</h1><p>Selected frame crops manually reviewed. Eleven unresolved candidates are not eleven missed safety signs.</p><pre>'+html.escape(json.dumps(report['summary'],indent=2))+'</pre><table><tr><th>Track</th><th>Thumbnail (overlay is display only)</th><th>Pipeline result</th><th>Review</th><th>Evidence</th></tr>'+''.join(cells)+'</table>'
 (a.output/'assessment.html').write_text(doc);print(json.dumps(report['summary'],indent=2))
if __name__=='__main__': main()
