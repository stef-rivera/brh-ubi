# Road English Coach

A voice-first coach that quizzes a driver on the signs they just passed.

The default demo plays a video on your Apple Silicon Mac, detects sign candidates locally, and reads their text with Apple Vision. Live recognition does not send images to cloud models. A specialist US-sign model makes stable predictions across frames. Every detected candidate receives a closest local prediction. Uncertain guesses are marked Demo guess and spoken by sign name; unreadable candidates remain in the log. Sign meanings come from `data/sign_catalog.json`. Those meanings are drafts (`"verified": false`) until someone checks them against the federal sign manual.

## Setup

Follow the Apple Silicon setup below. Put `ELEVENLABS_API_KEY` and the voice configuration in `.env` for spoken cues. The real clip is `data/drive.mp4`; recognition needs no Gemini or OpenAI credential.

Tiger Data is optional. Detections and practice answers are saved locally either way.

## Live recognition policy

The demo now accepts only the local detector. ChatGPT/Gemini live readers and crop assistance are disabled at the drive API, including calls from stale browser tabs. Historical OAuth integration files remain in the repository but are not used by the driving pipeline. ElevenLabs continues to supply speech.

Run isolated tests without model requests:

```sh
.venv-local/bin/python -m unittest discover -s tests -v
```

## Local vision prototype

On an Apple Silicon Mac, install Python 3.12, then run:

```sh
python3.12 -m venv .venv-local
.venv-local/bin/pip install -r backend/requirements-local.txt
.venv-local/bin/python scripts/setup_local_vision.py
.venv-local/bin/python scripts/setup_local_symbols.py
.venv-local/bin/python scripts/setup_us_signs.py
sh scripts/run_local.sh
```

Open http://127.0.0.1:8000 and press Start drive. Original video crops go to an independent local recognition queue. Recognition must meet confidence and freshness checks before announcing a catalog sign. Demo guesses are clearly labeled and can improve as a sign approaches. Guesses on business signs or blurry objects can be wrong. Only confirmed catalog readings enter factual practice. ElevenLabs generation and browser playback have separate queues; Park cancels driving cues.

This prototype uses Apple Vision OCR and MPS and currently requires macOS/Apple Silicon. Detector labels alone do not establish a reliable reading. See [the demo guide](docs/demo-guide.md) and measured runs in research/vision-architecture.

Local recognition combines a pretrained LISA 47-class YOLOv8m US-sign specialist, YOLOE candidate proposals, Apple Vision OCR, and a narrow CLIP fallback for the combined bicycle/pedestrian symbol absent from LISA. Specialist votes require at least three agreeing frames. Conservative confidence gates distinguish confirmed readings from demo guesses, but do not suppress a candidate’s nearest label. Scores are model outputs or cosine similarities, not calibrated probabilities. The specialist runs on full native frames, not tight sign crops.

Selected weights: [ali-haidous/yolo_trafficsign](https://github.com/ali-haidous/yolo_trafficsign/tree/ea6e5cc214bbe5b3ab406f141c3d67e307ccafbc), pinned revision and SHA256 in `backend/us_signs.py` and `scripts/setup_us_signs.py`. Safely loaded with PyTorch `weights_only=True` and installed framework classes. Actual checkpoint class names were inspected. School and supported numeric speeds are covered; road work, no-turn-on-red, and the distinct combined crossing are absent. Repository code is MIT; the [LISA dataset](https://cvrr.ucsd.edu/lisa-traffic-signs-dataset) has an academic license, and Ultralytics has its own licensing. This is a local hackathon evaluation, not a verified commercial licensing clearance.

Three specialist checkpoints were benchmarked on six independent manually reviewed real-clip examples: `research/vision-architecture/specialist-checkpoint-benchmark.json`. LISA outperformed the narrower JC21 alternatives on relevant class coverage and frame accuracy; this is a practical selection among evaluated available checkpoints, not a claim of global best accuracy. Live measurements and remaining failures are recorded in `specialist-live-results.json`.
