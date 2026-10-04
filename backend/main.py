"""FastAPI app: video stream, sign log, and a short parked quiz."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import ROOT, settings
from backend.detector import GeminiDetector
from backend.chatgpt_routes import router as chatgpt_router, require_tested
from backend import chatgpt_client
from backend.events import bus
from backend.practice import build_session, current_question, submit
from backend.state import state
from backend.tts import speak
from backend.video import VideoSource

FRONTEND = ROOT / "frontend"
THUMBS = ROOT / "data" / "thumbs"
AUDIO = ROOT / "data" / "audio"
THUMBS.mkdir(parents=True, exist_ok=True)
AUDIO.mkdir(parents=True, exist_ok=True)

video: VideoSource | None = None
detector: GeminiDetector | None = None


def _stop_pipeline() -> None:
    global video, detector
    if detector is not None:
        detector.stop()
        detector = None
    if video is not None:
        video.stop()
        video = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind(asyncio.get_running_loop())
    yield
    _stop_pipeline()


app = FastAPI(lifespan=lifespan)
app.include_router(chatgpt_router)
app.mount("/thumbs", StaticFiles(directory=str(THUMBS)), name="thumbs")
app.mount("/audio", StaticFiles(directory=str(AUDIO)), name="audio")
app.mount("/assets", StaticFiles(directory=str(FRONTEND)), name="assets")


class StartBody(BaseModel):
    source: str = "file"
    detector: str = "local"
    model: str = ""
    cloud_assist: bool = False


class AnswerBody(BaseModel):
    text: str


class SpeakBody(BaseModel):
    text: str


def _resolve_source(choice: str):
    if choice == "webcam":
        return 0
    path = ROOT / settings.video_path
    if not path.exists():
        raise FileNotFoundError(
            f"No video at {path}. Add a dashcam clip there, or start with the webcam."
        )
    return str(path)


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/state")
def api_state():
    return state.snapshot()


@app.post("/api/drive/start")
def start_drive(body: StartBody):
    global video, detector
    if body.detector not in {"gemini", "chatgpt", "local"}:
        raise HTTPException(400, "Unknown detector.")
    if body.detector == "chatgpt" or (body.detector == "local" and body.cloud_assist):
        require_tested(body.model)
    _stop_pipeline()
    try:
        source = _resolve_source(body.source)
        video = VideoSource(source)
    except (FileNotFoundError, RuntimeError) as exc:
        video = None
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    drive_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    with state.lock:
        state.mode = "driving"
        state.drive_id = drive_id
        state.detector = body.detector
        state.detection_status = "running"
        state.detection_error = ""
        state.source = body.source
        state.pipeline_metrics = {}
        state.detections = []
        state.practice_items = []
        state.practice_index = 0
    reader = (lambda frame: chatgpt_client.read_frame(frame, body.model)) if body.detector == "chatgpt" else None
    try:
        if body.detector == "local":
            from backend.local_vision import LocalDetector
            fallback = (lambda crop: chatgpt_client.read_frame(crop, body.model, crop_mode=True)) if body.cloud_assist else None
            detector = LocalDetector(video, fallback=fallback)
        else:
            detector = GeminiDetector(video, reader=reader, provider=body.detector)
        video.start()
        detector.start()
    except Exception as exc:
        _stop_pipeline()
        with state.lock:
            state.mode = "idle"
            state.detection_status = "error"
        raise HTTPException(400, "Could not start local vision. Run the local setup script and check the server log.") from exc
    event = {"type": "state", "mode": "driving", "detector": state.detector, "drive_id": drive_id}
    bus.publish(event)
    return event


@app.post("/api/park")
def park():
    if detector is not None:
        detector.stop()
    with state.lock:
        items = build_session(state.detections)
        state.practice_items = items
        state.practice_index = 0
        state.mode = "parked"
        drive_id = state.drive_id
    event = {"type": "state", "mode": "parked", "detector": state.detector, "drive_id": drive_id}
    bus.publish(event)
    question = current_question()
    if question is None:
        done = {"type": "practice_done", "summary": {"asked": 0}}
        bus.publish(done)
        return {"state": event, "next": done}
    bus.publish(question)
    return {"state": event, "next": question}


@app.post("/api/practice/answer")
def practice_answer(body: AnswerBody):
    result = submit(body.text)
    bus.publish(result)
    if result.get("done") or result.get("type") == "practice_done":
        return result
    nxt = current_question()
    if nxt:
        bus.publish(nxt)
        result["next"] = nxt
    return result


@app.post("/api/speak")
def api_speak(body: SpeakBody):
    return {"audio_url": speak(body.text)}


@app.post("/api/resume")
def resume():
    if detector is not None:
        detector.start()
    with state.lock:
        state.mode = "driving"
        drive_id = state.drive_id
    event = {"type": "state", "mode": "driving", "detector": state.detector, "drive_id": drive_id}
    bus.publish(event)
    return event


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    queue = bus.subscribe()
    try:
        await socket.send_json({"type": "state", **state.snapshot()})
        while True:
            event = await queue.get()
            await socket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(queue)


@app.get("/stream.mjpg")
def stream():
    return StreamingResponse(_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


async def _frames():
    blank = None
    while True:
        item = video.latest() if video is not None else None
        if item is None:
            if blank is None:
                blank = _placeholder()
            payload = blank
        else:
            frame, _ts = item
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            payload = buf.tobytes() if ok else b""
        yield (
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
        )
        await asyncio.sleep(0.04)


def _placeholder() -> bytes:
    import numpy as np

    image = np.zeros((360, 640, 3), dtype="uint8")
    ok, buf = cv2.imencode(".jpg", image)
    return buf.tobytes() if ok else b""
