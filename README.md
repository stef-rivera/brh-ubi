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

Open http://127.0.0.1:8000 and press **Start drive**. Replace `data/drive.mp4` with a real dashcam clip when you have one. **Use webcam** is for holding a printed sign up to the camera.

Tiger Data is optional. Detections are saved in `data/detections_log.json` either way.

Local YOLO (live boxes on the video) waits until this demo is solid. It needs PyTorch, which is not installed yet.
