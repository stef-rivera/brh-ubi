import Vision, Foundation, cv2, time,json,numpy as np
from pathlib import Path
root=Path('/tmp/brh-sign-bench');cap=cv2.VideoCapture('/Users/temiadebowale/Documents/brh-ubi/data/drive.mp4')
rows=[]
for t in range(60):
 cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,f=cap.read()
 if not ok:continue
 cv2.imwrite('/tmp/brh-sign-bench/frame.jpg',f)
 handler=Vision.VNImageRequestHandler.alloc().initWithData_options_(Foundation.NSData.dataWithContentsOfFile_('/tmp/brh-sign-bench/frame.jpg'),None)
 request=Vision.VNRecognizeTextRequest.alloc().init();request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate);request.setUsesLanguageCorrection_(False)
 start=time.perf_counter();success,error=handler.performRequests_error_([request],None);ms=(time.perf_counter()-start)*1000
 texts=[{'text':str(o.topCandidates_(1)[0].string()),'confidence':float(o.topCandidates_(1)[0].confidence())} for o in request.results() or []]
 rows.append(dict(t=t,ms=ms,texts=texts))
print(json.dumps(rows),flush=True)
(root/'ocr.json').write_text(json.dumps(rows,indent=2))
