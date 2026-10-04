const video = document.querySelector("#video");
const mode = document.querySelector("#mode");
const log = document.querySelector("#log");
const hint = document.querySelector("#hint");
const park = document.querySelector("#park");
const practice = document.querySelector("#practice");
const panelTitle = document.querySelector("#panel-title");
const question = document.querySelector("#question");
const progress = document.querySelector("#progress");
const result = document.querySelector("#result");
const qThumb = document.querySelector("#q-thumb");
const answer = document.querySelector("#answer");

let voiceOn = false;
let socket;
const player = new Audio();
const unlocker = new Audio();
let spanishText = "";

function unlockVoice() {
  if (voiceOn) return;
  voiceOn = true;
  unlocker.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
  unlocker.volume = 0;
  const started = unlocker.play();
  if (started) started.catch(() => {});
}

function sayBrowser(text, lang) {
  speechSynthesis.cancel();
  const clip = new SpeechSynthesisUtterance(text);
  clip.lang = lang || "en-US";
  speechSynthesis.speak(clip);
}

function say(text, audioUrl, lang) {
  if (!voiceOn || !text) return;
  speechSynthesis.cancel();
  if (!audioUrl) {
    sayBrowser(text, lang);
    return;
  }
  player.pause();
  player.src = audioUrl;
  player.volume = 1;
  player.play().catch(() => sayBrowser(text, lang));
}

const panel = document.querySelector("#panel");
const coachStatus = document.querySelector("#coach-status");
const VOICE_RATE = 24000;
let coachSocket = null;
let micStream = null;
let micContext = null;
let micProcessor = null;
let playContext = null;
let playTime = 0;
let coachSources = [];
let coachStopped = false;
let coachGeneration = 0;

function setMode(name) {
  const modeName = name === "driving" || name === "parked" ? name : "idle";
  mode.textContent = modeName.toUpperCase();
  document.body.classList.remove("idle", "driving", "parked");
  document.body.classList.add(modeName);
  panel.hidden = true;
  document.querySelector("#start-file").hidden = modeName !== "idle";
  park.hidden = modeName !== "driving";
  document.querySelector("#coach-end").hidden = modeName !== "parked";
}

function escapeHTML(value) {
  const el = document.createElement("span");
  el.textContent = String(value || "");
  return el.innerHTML.replaceAll('"', "&quot;");
}

function signKey(row) {
  if (row.sign_id && row.sign_id !== "unknown") return row.sign_id;
  return `text:${String(row.sign_text || "").toLowerCase()}`;
}

function cardHTML(row) {
  const title = row.sign_id !== "unknown" && (!row.sign_text || row.sign_text === "Unresolved sign")
    ? row.sign_id.replaceAll("_", " ").toUpperCase() : row.sign_text;
  const meaning = row.meaning
    ? `<span>${escapeHTML(row.meaning)}</span>${row.verified ? "" : '<div class="draft">Draft meaning, not checked against the sign manual yet.</div>'}`
    : `<span>${escapeHTML(row.sign_text || "Unrecognized sign")}</span>`;
  const chip = row.recognition_status === "pending"
    ? '<span class="chip quiet">reading…</span>'
    : row.recognition_status === "unresolved"
    ? '<span class="chip quiet">unresolved</span>'
    : row.sign_id === "unknown"
    ? '<span class="chip quiet">not in catalog</span>'
    : row.safety_critical
      ? '<span class="chip">safety</span>'
      : "";
  return `
    <img src="${escapeHTML(row.thumb_url || "")}" alt="" />
    <div>
      <strong>${escapeHTML(title || row.sign_id)}</strong>
      ${chip}
      ${meaning}
    </div>`;
}

function addCard(row) {
  if (row.recognition_status === "pending") {
    let reading = log.querySelector(".card.reading");
    if (!reading) {
      reading = document.createElement("article");
      reading.className = "card reading";
      log.prepend(reading);
    }
    reading.innerHTML = cardHTML(row);
    return;
  }
  const reading = log.querySelector(".card.reading");
  if (reading) reading.remove();
  const key = signKey(row);
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.signKey = key;
  if (row.event_id) card.dataset.eventId = row.event_id;
  card.innerHTML = cardHTML(row);
  const previous = log.querySelector(`[data-sign-key="${CSS.escape(key)}"]`);
  if (previous) previous.remove();
  if (row.recognition_status === "unresolved") log.append(card);
  else log.prepend(card);
}

async function start(source) {
  unlockVoice();
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    hint.textContent = "Connecting to the server; try Start drive again in a moment.";
    return;
  }
  hint.textContent = "";
  const response = await fetch("/api/drive/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, detector: "local" }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    hint.textContent = body.detail || "Could not start the drive.";
    return;
  }
  video.src = "/stream.mjpg?t=" + Date.now();
  spanishText = "";
  clearCues();
  log.replaceChildren();
  log.hidden = true;
  practice.hidden = true;
  coachGeneration += 1;
  stopCoach();
  park.disabled = false;
  setMode("driving");
  hint.textContent = "";
}

document.querySelector("#start-file").addEventListener("click", () => start("file"));

park.addEventListener("click", async () => {
  unlockVoice();
  clearCues();
  const generation = ++coachGeneration;
  stopCoach();
  log.hidden = true;
  practice.hidden = true;
  hint.textContent = "";
  setMode("parked");
  let stream = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch {
    stream = null;
  }
  if (generation !== coachGeneration) {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    return;
  }
  const response = await fetch("/api/park", { method: "POST" });
  const body = await response.json();
  if (generation !== coachGeneration) {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    return;
  }
  setMode("parked");
  if (!body.voice) {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    hint.textContent = body.voice_error || "Voice practice is unavailable right now.";
    return;
  }
  if (!stream) hint.textContent = "The microphone is off.";
  beginCoach(body.voice, stream);
});

document.querySelector("#coach-end").addEventListener("click", () => {
  coachGeneration += 1;
  stopCoach();
  setMode("idle");
  hint.textContent = "";
});

document.querySelector("#practice").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = answer.value.trim();
  if (!text) return;
  const response = await fetch("/api/practice/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const body = await response.json();
  result.className = body.grade || "";
  result.textContent = body.grade
    ? `${body.grade}. ${body.meaning}`
    : "Done.";
  answer.value = "";
  if (body.next) showQuestion(body.next);
  else if (body.done || body.type === "practice_done") {
    question.textContent = "That's the set from this drive.";
    progress.textContent = "";
  }
});

function showQuestion(item) {
  practice.hidden = false;
  log.hidden = true;
  progress.textContent = `${item.index} / ${item.total}`;
  question.textContent = item.question;
  result.textContent = "";
  if (item.thumb_url) {
    qThumb.hidden = false;
    qThumb.src = item.thumb_url;
  }
  spanishText = item.spanish || item.meaning_es || "";
  say(item.question, item.audio_url);
  answer.focus();
}

document.querySelector("#spanish").addEventListener("click", async () => {
  if (!spanishText) {
    hint.textContent = "Park after a sign is logged, then this speaks that sign in Spanish.";
    return;
  }
  unlockVoice();
  let audioUrl = "";
  try {
    const response = await fetch("/api/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: spanishText, language: "es" }),
    });
    const body = await response.json();
    audioUrl = body.audio_url || "";
  } catch {
    audioUrl = "";
  }
  say(spanishText, audioUrl, "es-US");
});

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state" && message.mode) {
      setMode(message.mode);
      if (message.detection_error) hint.textContent = message.detection_error;
      if (Array.isArray(message.detections)) {
        log.replaceChildren();
        message.detections.forEach(addCard);
        if (message.drive_id) {
          video.src = "/stream.mjpg";
          park.disabled = false;
        }
      }
    }
    if (message.type === "detector_status" && message.status === "error") hint.textContent = message.message || message.status;
    if (message.type === "detection" || message.type === "detection_update") addCard(message);
    if (message.type === "detection_remove") [...log.children].find(c => c.dataset.eventId === message.event_id)?.remove();
    if (message.type === "pipeline_metrics") showMetrics(message);
    if (message.type === "state" && message.pipeline_metrics) showMetrics(message.pipeline_metrics);
    if (message.type === "cue") enqueueCue(message);
  };
  socket.onclose = () => setTimeout(connect, 1000);
}

connect();


document.querySelector("#start-file").disabled = false;

function showMetrics(m) {
  if (m.pending === 0) log.querySelector(".card.reading")?.remove();
  document.querySelector("#pipeline-status").textContent = m.frames == null ? "" :
    `${m.frames} frames analyzed · ${m.pending} reading · ${m.resolved} read · ${m.unresolved} unresolved · ${m.cloud_requests} cloud crops · ${m.inference_ms} ms/frame`;
}
const cueQueue = [];
let cueBusy = false;
let cueGeneration = 0;
function clearCues() { cueGeneration++; cueQueue.length = 0; cueBusy = false; player.pause(); player.onended = null; player.onerror = null; speechSynthesis.cancel(); }
function enqueueCue(cue) { if (!voiceOn) return; cueQueue.push({...cue, received: Date.now()}); drainCues(); }
function stopCoach() {
  coachStopped = true;
  for (const source of coachSources) {
    try { source.stop(); } catch { /* already finished */ }
  }
  coachSources = [];
  playTime = 0;
  if (playContext) {
    playContext.close().catch(() => {});
    playContext = null;
  }
  if (coachSocket) {
    coachSocket.onmessage = null;
    coachSocket.onopen = null;
    coachSocket.onclose = null;
    coachSocket.close();
    coachSocket = null;
  }
  if (micProcessor) {
    micProcessor.disconnect();
    micProcessor.onaudioprocess = null;
    micProcessor = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }
  if (micContext) {
    micContext.close().catch(() => {});
    micContext = null;
  }
}

function pcm16Base64(samples) {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function base64ToFloat32(encoded) {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const pcm = new Int16Array(bytes.buffer);
  const samples = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 0x8000;
  return samples;
}

function resample(input, fromRate, toRate) {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const length = Math.floor(input.length / ratio);
  const output = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(left + 1, input.length - 1);
    const mix = position - left;
    output[i] = input[left] * (1 - mix) + input[right] * mix;
  }
  return output;
}

function playCoachPcm(encoded) {
  if (coachStopped || !playContext) return;
  const samples = base64ToFloat32(encoded);
  if (!samples.length) return;
  const buffer = playContext.createBuffer(1, samples.length, VOICE_RATE);
  buffer.getChannelData(0).set(samples);
  const source = playContext.createBufferSource();
  source.buffer = buffer;
  source.connect(playContext.destination);
  source.onended = () => {
    coachSources = coachSources.filter((item) => item !== source);
  };
  coachSources.push(source);
  const now = playContext.currentTime;
  if (playTime < now) playTime = now + 0.05;
  source.start(playTime);
  playTime += buffer.duration;
}

function startMic(stream, socket) {
  micStream = stream;
  micContext = new AudioContext();
  const source = micContext.createMediaStreamSource(stream);
  const mute = micContext.createGain();
  mute.gain.value = 0;
  micProcessor = micContext.createScriptProcessor(4096, 1, 1);
  micProcessor.onaudioprocess = (event) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const input = event.inputBuffer.getChannelData(0);
    const audio = resample(input, micContext.sampleRate, VOICE_RATE);
    socket.send(JSON.stringify({ type: "input_audio_buffer.append", audio: pcm16Base64(audio) }));
  };
  source.connect(micProcessor);
  micProcessor.connect(mute);
  mute.connect(micContext.destination);
}

function beginCoach(voice, stream) {
  coachStopped = false;
  playContext = new AudioContext({ sampleRate: VOICE_RATE });
  playContext.resume();
  playTime = 0;
  const socket = new WebSocket(
    "wss://api.x.ai/v1/realtime?model=grok-voice-latest",
    [`xai-client-secret.${voice.token}`]
  );
  coachSocket = socket;
  coachStatus.textContent = "Connecting to the coach…";
  socket.onopen = () => {
    if (coachStopped || coachSocket !== socket) return;
    socket.send(JSON.stringify({
      type: "session.update",
      session: {
        voice: "eve",
        instructions: voice.instructions,
        turn_detection: { type: "server_vad" },
        audio: {
          input: { format: { type: "audio/pcm", rate: VOICE_RATE } },
          output: { format: { type: "audio/pcm", rate: VOICE_RATE } },
        },
      },
    }));
    socket.send(JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text: "I just parked. Start the practice." }],
      },
    }));
    socket.send(JSON.stringify({ type: "response.create" }));
    if (stream) startMic(stream, socket);
    coachStatus.textContent = "The coach is speaking. Then just answer out loud.";
  };
  socket.onmessage = (event) => {
    if (coachStopped || coachSocket !== socket) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === "response.created") coachStatus.textContent = "Coach is speaking.";
    if ((message.type === "response.output_audio.delta" || message.type === "response.audio.delta") && message.delta) {
      playCoachPcm(message.delta);
    }
    if (message.type === "response.done") coachStatus.textContent = "Listening. Answer out loud.";
    if (message.type === "error") hint.textContent = (message.error && message.error.message) || "The coach lost the connection.";
  };
  socket.onerror = () => { hint.textContent = "The coach could not connect."; };
  socket.onclose = () => {
    if (coachSocket === socket) coachStatus.textContent = "Practice ended.";
  };
}

function drainCues() {
  if (cueBusy) return;
  let cue;
  while (cueQueue.length) { const item = cueQueue.shift(); if (Date.now() - item.received < 8000) { cue = item; break; } }
  if (!cue) return;
  cueBusy = true;
  const generation = cueGeneration;
  const done = () => { if (generation !== cueGeneration) return; cueBusy = false; drainCues(); };
  const fallback = () => {
    if (generation !== cueGeneration) return;
    player.onended = null; player.onerror = null;
    const speech = new SpeechSynthesisUtterance(cue.text); speech.lang = "en-US"; speech.onend = done; speech.onerror = done; speechSynthesis.speak(speech);
  };
  if (!cue.audio_url) { fallback(); return; }
  player.src = cue.audio_url; player.volume = 1; player.onended = done; player.onerror = fallback; player.play().catch(fallback);
}
