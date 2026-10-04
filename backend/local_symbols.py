"""Local zero-shot symbol recognition; abstain without two agreeing crop views.

Cosine scores are similarities, not calibrated probabilities. Thresholds are
prototype gates, not proof of field accuracy. No video-specific references.
"""
from backend.config import ROOT
from backend.vision_runtime import mps_lock

LABELS = [
    ('school_zone', 'school crossing sign with two children walking'),
    ('bicycle_pedestrian_crossing', 'bicycle and pedestrian crossing sign'),
    (None, 'pedestrian crossing sign with one person'),
    ('speed_limit', 'speed limit sign'), ('stop', 'stop sign'),
    ('do_not_enter', 'do not enter sign'), ('road_work', 'road work sign'),
    (None, 'street name sign'), (None, 'advertising business sign'),
    (None, 'blank blurry traffic sign'), (None, 'yellow AHEAD plaque'),
    ('no_turn_on_red','white rectangular NO TURN ON RED sign'),
    ('no_left_turn','no left turn sign'), ('yield','yield triangle sign'),
    ('lane_ends','lane ends sign'), ('one_way','one way arrow sign'),
    ('railroad','railroad crossing sign'),
]


def agree(scores):
    """Two supported top classes must agree with a useful similarity margin."""
    votes=[]
    for row in scores:
        order=sorted(range(len(row)),key=lambda i:row[i],reverse=True)
        top,second=order[:2]
        if LABELS[top][0] and row[top]>=.32 and row[top]-row[second]>=.025:
            votes.append((LABELS[top][0],row[top],row[top]-row[second]))
    for sign_id,_,_ in votes:
        matched=[v for v in votes if v[0]==sign_id]
        if len(matched)>=2:
            return dict(sign_id=sign_id,sign_text='',confidence=round(min(v[1] for v in matched),4),
                        symbol_similarity=round(min(v[1] for v in matched),4),
                        symbol_margin=round(min(v[2] for v in matched),4))
    return None


class LocalSymbolClassifier:
    def __init__(self):
        import clip, torch
        from PIL import Image
        import numpy as np
        weights=ROOT/'.local/vision/ViT-B-32.pt'
        if not weights.exists():
            raise RuntimeError('Local symbol weights missing. Run scripts/setup_local_symbols.py.')
        self.torch=torch;self.Image=Image
        self.model,self.preprocess=clip.load(str(weights),device='mps')
        with mps_lock, torch.no_grad():
            tokens=clip.tokenize(['A photo of a US '+text for _,text in LABELS]).to('mps')
            self.text=self.model.encode_text(tokens)
            self.text/=self.text.norm(dim=-1,keepdim=True)
            self._scores([np.zeros((64,64,3),dtype='uint8')])

    def _scores(self,crops):
        import cv2
        with mps_lock, self.torch.no_grad():
            images=self.torch.stack([self.preprocess(self.Image.fromarray(cv2.cvtColor(c,cv2.COLOR_BGR2RGB))) for c in crops]).to('mps')
            features=self.model.encode_image(images)
            features/=features.norm(dim=-1,keepdim=True)
            return (features@self.text.T).float().cpu().tolist()

    def __call__(self,crops):
        usable=[c for c in crops if min(c.shape[:2])>=8][:3]
        if not usable:return None
        scores=self._scores(usable)
        confident=agree(scores)
        if confident:return confident
        # Recorded-video demo deliberately shows a nearest catalog guess for every crop.
        supported=[i for i,(sign_id,_) in enumerate(LABELS) if sign_id]
        best=max(supported,key=lambda i:sum(row[i] for row in scores)/len(scores))
        similarity=sum(row[best] for row in scores)/len(scores)
        return dict(sign_id=LABELS[best][0],sign_text='',confidence=round(similarity,4),
                    symbol_similarity=round(similarity,4),tentative=True,demo_guess=True)
