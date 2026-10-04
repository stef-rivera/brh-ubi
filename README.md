# Road English Coach

A voice-first coach that quizzes a driver on the signs they just passed.

This demo plays a video on your laptop and sends one frame to Gemini about every two seconds. Sign meanings come from `data/sign_catalog.json`. Those meanings are drafts (`"verified": false`) until someone checks them against the federal sign manual.

## Setup

```bash
conda activate brh
pip install -r backend/requirements.txt
```

Put `GEMINI_API_KEY` and `ELEVENLABS_API_KEY` in `.env`. Then:

```bash
python scripts/smoke_test.py
python scripts/make_demo_clip.py
uvicorn backend.main:app --app-dir . --reload
```

Open http://127.0.0.1:8000 and press **Start drive**. Replace `data/drive.mp4` with a real dashcam clip when you have one. The demo is that file, not a live camera.

Tiger Data is optional. Detections are saved in `data/detections_log.json` either way.

Local YOLO (live boxes on the video) waits until this demo is solid. It needs PyTorch, which is not installed yet.


## Use your ChatGPT plan locally

Install `backend/requirements.txt`, then start on the loopback address. Disable
access logs so OAuth callback codes are not written to the terminal:

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log --timeout-graceful-shutdown 3
```

Open http://127.0.0.1:8000 (use this exact host for the sign-in cookie).
Choose **ChatGPT plan**, click **Continue with ChatGPT**, and authorize the
app to use your plan. Complete sign-in in the new tab. The callback returns to
the app; refresh the original tab if you use it instead. Choose an available
model and click **Test one frame**. Only a completed image response unlocks
**Start drive**. Account model lists do not guarantee image access.

This uses OpenAI's documented ChatGPT-plan OAuth flow, not an OpenAI API key.
Usage counts against the account's plan/app limits. Manage permissions and
limits at https://chatgpt.com/settings/usage. No automatic Gemini fallback runs.
ChatGPT is sampled at most once every five seconds, with one request at a time;
model latency can reduce that rate further. Results may be late or inaccurate.
A file plays once and detection stops at its end. Click **Park** to practice.
The existing ElevenLabs integration continues supplying cached voice cues.

Credentials are stored under `.local/chatgpt/` (Git-ignored, owner-only files),
with PKCE, state and signed ID-token validation. Use **Disconnect** to clear the
active account's tokens and attempt remote session revocation. Account mappings
and the stable host ID are retained for later sign-ins. Run only one app server
per checkout to avoid concurrent rotating-token refreshes. This is a local demo,
not a remotely hosted multi-user authentication service.

Run isolated tests without making model requests:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Local vision prototype

On an Apple Silicon Mac, install Python 3.12, then run:

```sh
python3.12 -m venv .venv-local
.venv-local/bin/pip install -r backend/requirements-local.txt
.venv-local/bin/python scripts/setup_local_vision.py
sh scripts/run_local.sh
```

Open http://127.0.0.1:8000 and select Local vision. Detection runs at about 10 Hz using the GPU. Original sign crops go to a bounded local OCR queue. Optional ChatGPT crop assistance uses two independent workers, at most eight requests per drive, and the existing tested ChatGPT account/model. It is off by default. Speech has its own worker and browser playback queue; late cues are suppressed. Pending cards update independently, and live counters show detection progress while recognition runs.

This prototype uses Apple Vision OCR and MPS and therefore currently requires macOS/Apple Silicon. Unresolved signs remain visible. Detector labels alone never trigger a spoken cue. Research benchmarks are in research/vision-architecture.
