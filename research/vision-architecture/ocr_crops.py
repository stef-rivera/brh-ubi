import Vision,Foundation,cv2,time,json,numpy as np
from pathlib import Path
root=Path('/tmp/brh-sign-bench');rows=[]
for p in sorted(root.glob('crop-*.jpg')):
 f=cv2.imread(str(p));f=cv2.resize(f,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC);data=cv2.imencode('.png',f)[1].tobytes()
 handler=Vision.VNImageRequestHandler.alloc().initWithData_options_(Foundation.NSData.dataWithBytes_length_(data,len(data)),None)
 request=Vision.VNRecognizeTextRequest.alloc().init();request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate);request.setUsesLanguageCorrection_(False);request.setRecognitionLanguages_(['en-US']);request.setMinimumTextHeight_(.01)
 start=time.perf_counter();success,error=handler.performRequests_error_([request],None);ms=(time.perf_counter()-start)*1000
 texts=[{'text':str(o.topCandidates_(1)[0].string()),'confidence':float(o.topCandidates_(1)[0].confidence())} for o in request.results() or []]
 rows.append(dict(file=p.name,ms=ms,success=bool(success),error=str(error),texts=texts))
 print(rows[-1],flush=True)
(root/'ocr-crops.json').write_text(json.dumps(rows,indent=2))
