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
let activeDriveId = "";
let currentMode = "idle";
const rows = new Map();
const audioStates = new Map();
const playbackStates = new Map();
const playedEvents = new Set();
const announcedEvents = new Set();
const audioHint = document.querySelector("#audio-status");

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
    audioHint.textContent = "Speaking with browser voice: ElevenLabs audio unavailable.";
    sayBrowser(text, lang);
    return;
  }
  player.pause();
  player.src = audioUrl;
  player.volume = 1;
  audioHint.textContent = "Speaking with ElevenLabs.";
  player.play().catch(() => {
    audioHint.textContent = "Speaking with browser voice: ElevenLabs playback failed.";
    sayBrowser(text, lang);
  });
}

function setMode(name) {
  currentMode = name;
  mode.textContent = name.toUpperCase();
}

function escapeHTML(value) {
  const el = document.createElement("span");
  el.textContent = String(value || "");
  return el.innerHTML.replaceAll('"', "&quot;");
}

function addCard(row) {
  if (row.drive_id && activeDriveId && row.drive_id !== activeDriveId) return;
  const key = row.event_id || `${row.sign_id}:${row.ts_ms || rows.size}`;
  rows.set(key, row);
  if (row.audio_status || row.audio_url) audioStates.set(key, {
    ...audioStates.get(key), status: row.audio_status || "ready", audio_url: row.audio_url || "",
    text: row.audio_text || row.cue_text || row.meaning, reason: row.audio_error || ""
  });
  const card = document.createElement("article");
  card.className = `card ${row.recognition_status || "resolved"}`;
  card.dataset.eventId = key;
  const title = row.recognition_status === "pending" ? "Reading sign…" :
    row.recognition_status === "unresolved" ? "Couldn’t read this sign" : (row.sign_text || (row.sign_id || "Unresolved sign").replaceAll("_", " "));
  const status = row.recognition_status === "pending" ? "Reading in background" :
    row.recognition_status === "unresolved" ? "Unresolved" :
    row.recognition_status === "tentative" ? "Demo guess" :
    row.sign_id === "unknown" ? "Outside catalog" : "Recognized";
  const reason = row.recognition_error || (row.recognition_status === "pending" ?
    "Detection continues while this crop is read." : "");
  const audio = audioStates.get(key);
  const playback = playbackStates.get(key);
  const audioText = playback === "canceled" ? "Playback stopped. Replay is available." :
    playback === "played" ? "ElevenLabs audio played in this tab" :
    playback === "blocked" ? "Browser blocked audio. Click Replay to allow playback." :
    playback === "failed" ? "ElevenLabs playback failed. Click Replay to retry." :
    playback === "playing" ? "ElevenLabs audio playing…" :
    audio?.status === "failed" ? `Voice failed: ${audio.reason || "Audio could not be generated."}` :
    audio?.status === "skipped" ? `No announcement: ${audio.reason || "This sign has no driving cue."}` :
    audio?.status === "queued" ? "Preparing ElevenLabs audio…" :
    audio?.status === "ready" ? "ElevenLabs audio ready" : "";
  card.innerHTML = `
    <img src="${escapeHTML(row.thumb_url || "")}" alt="Captured sign" />
    <div class="card-body">
      <strong>${escapeHTML(title)}</strong>
      <span class="chip ${row.recognition_status === "resolved" && row.safety_critical ? "" : "quiet"}">${status}</span>
      ${row.meaning ? `<p class="meaning">${row.recognition_status === "tentative" ? "If this prediction is correct: " : ""}${escapeHTML(row.meaning)}</p>` : ""}
      ${row.meaning && !row.verified ? '<div class="draft">Draft catalog meaning.</div>' : ""}
      ${reason ? `<p class="recognition-reason">${escapeHTML(reason)}</p>` : ""}
      ${audioText ? `<p class="audio-detail">${escapeHTML(audioText)}</p>` : ""}
      ${audio?.audio_url ? '<button type="button" class="secondary replay">Replay ElevenLabs cue</button>' : ""}
    </div>`;
  card.querySelector(".replay")?.addEventListener("click", () => {
    unlockVoice();
    enqueueCue({event_id: key, ...audio, replay: true});
  });
  const previous = [...log.children].find(c => c.dataset.eventId === key);
  if (previous) previous.replaceWith(card);
  else if (row.recognition_status === "unresolved") log.append(card);
  else log.prepend(card);
}

function setPlayback(eventId, status) {
  if (!eventId) return;
  playbackStates.set(eventId, status);
  if (status === "played") playedEvents.add(eventId);
  const row = rows.get(eventId);
  if (row) addCard(row);
  document.querySelector("#playback-status").textContent =
    `${playedEvents.size} sign cue(s) played in this tab`;
}

function updateAudio(message) {
  if (message.drive_id && activeDriveId && message.drive_id !== activeDriveId) return;
  audioStates.set(message.event_id, {...audioStates.get(message.event_id), ...message});
  const row = rows.get(message.event_id);
  if (row) addCard({...row, audio_status: message.status, audio_url: message.audio_url || row.audio_url,
    audio_text: message.text || row.audio_text, audio_error: message.reason || ""});
}

async function start(source) {
  unlockVoice();
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    hint.textContent = "Connecting to the server; try Start drive again in a moment.";
    return;
  }
  hint.textContent = "Looking at the road…";
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
  const started = await response.json().catch(() => ({}));
  activeDriveId = started.drive_id || activeDriveId;
  rows.clear(); audioStates.clear(); playbackStates.clear(); playedEvents.clear(); announcedEvents.clear();
  document.querySelector("#playback-status").textContent = "";
  showMetrics({});
  audioHint.textContent = "Voice enabled. Confirmed catalog signs will be announced.";
  video.src = "/stream.mjpg?t=" + Date.now();
  spanishText = "";
  clearCues();
  log.replaceChildren();
  log.hidden = false;
  practice.hidden = true;
  panelTitle.textContent = "Sign log";
  park.disabled = false;
  setMode("driving");
  hint.textContent = "Driving. Confirmed catalog signs receive short announcements; reading continues in the background.";
}

document.querySelector("#start-file").addEventListener("click", () => start("file"));

park.addEventListener("click", async () => {
  clearCues();
  const response = await fetch("/api/park", { method: "POST" });
  const body = await response.json();
  if (!response.ok) { hint.textContent = body.detail || "Could not park."; return; }
  setMode("parked");
  hint.textContent = "Parked. Answer in English.";
  panelTitle.textContent = "Parked practice";
  log.hidden = false;
  if (!body.next || body.next.type === "practice_done") {
    practice.hidden = false;
    question.textContent = "No catalog signs on this drive yet.";
    progress.textContent = "";
    spanishText = "";
    answer.disabled = true;
    qThumb.hidden = true;
    return;
  }
  showQuestion(body.next);
});

document.querySelector("#practice").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = answer.value.trim();
  if (!text) return;
  unlockVoice();
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
    answer.disabled = true;
    spanishText = "";
  }
});

function showQuestion(item) {
  practice.hidden = false;
  log.hidden = false;
  answer.disabled = false;
  progress.textContent = `${item.index} / ${item.total}`;
  question.textContent = item.question;
  result.textContent = "";
  qThumb.hidden = !item.thumb_url;
  if (item.thumb_url) {
    qThumb.hidden = false;
    qThumb.src = item.thumb_url;
  }
  spanishText = item.meaning_es || "";
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
      body: JSON.stringify({ text: spanishText }),
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
      if (message.drive_id && message.drive_id !== activeDriveId) {
        clearCues(); rows.clear(); audioStates.clear(); playbackStates.clear(); playedEvents.clear(); announcedEvents.clear();
  document.querySelector("#playback-status").textContent = "";
      }
      activeDriveId = message.drive_id || "";
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
    if (message.drive_id && activeDriveId && message.drive_id !== activeDriveId) return;
    if (message.type === "audio_status") updateAudio(message);
    if (message.type === "detector_status") hint.textContent = message.message || message.status;
    if (message.type === "detection" || message.type === "detection_update") addCard(message);
    if (message.type === "detection_remove") {
      rows.delete(message.event_id); audioStates.delete(message.event_id);
      [...log.children].find(c => c.dataset.eventId === message.event_id)?.remove();
    }
    if (message.type === "pipeline_metrics") showMetrics(message);
    if (message.type === "state" && message.pipeline_metrics) showMetrics(message.pipeline_metrics);
    if (message.type === "cue") enqueueCue(message);
  };
  socket.onclose = () => setTimeout(connect, 1000);
}

connect();


const detectorSelect = document.querySelector("#detector");
const modelSelect = document.querySelector("#chatgpt-model");
const accountSelect = document.querySelector("#chatgpt-account");
const authStatus = document.querySelector("#chatgpt-status");
const testButton = document.querySelector("#chatgpt-test");
const startButton = document.querySelector("#start-file");
let testedModel = "";
let testedModels = [];

async function apiJSON(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || "Request failed.");
  return value;
}
function updateStart() {
  const local = detectorSelect.value === "local";
  const needsChatGPT = false;
  document.querySelector("#local-controls").hidden = !local;
  document.querySelector("#chatgpt-controls").hidden = !needsChatGPT;
  startButton.disabled = needsChatGPT && (!testedModel || testedModel !== modelSelect.value);
}
detectorSelect.addEventListener("change", updateStart);
modelSelect.addEventListener("change", () => { testedModel = testedModels.includes(modelSelect.value) ? modelSelect.value : ""; updateStart(); });
accountSelect.addEventListener("change", () => {
  testedModel = "";
  modelSelect.disabled = true;
  testButton.disabled = true;
  updateStart();
  authStatus.textContent = "Click Continue with ChatGPT to authorize this account.";
});
document.querySelector("#chatgpt-login").addEventListener("click", async () => {
  // Open during the click so browsers do not treat the sign-in as an unsolicited popup.
  const popup = window.open("about:blank", "chatgpt-login");
  try {
    const data = await apiJSON("/api/chatgpt/login", { account_id: accountSelect.value || null });
    if (popup) popup.location.href = data.url;
    else window.location.href = data.url;
    authStatus.textContent = "Complete sign-in in the new tab, then return here and refresh.";
  } catch (error) {
    if (popup) popup.close();
    authStatus.textContent = error.message;
  }
});
document.querySelector("#chatgpt-disconnect").addEventListener("click", async () => {
  try {
    const data = await apiJSON("/api/chatgpt/disconnect", {});
    testedModel = "";
    modelSelect.replaceChildren(new Option("Connect to load models", ""));
    modelSelect.disabled = true;
    testButton.disabled = true;
    await updateStart();
    authStatus.textContent = data.revoked ? "Disconnected." : "Disconnected locally. Remote revocation was not confirmed; disconnect the app in ChatGPT Settings.";
  } catch (error) { authStatus.textContent = error.message; }
});
testButton.addEventListener("click", async () => {
  testedModel = "";
  updateStart();
  testButton.disabled = true;
  authStatus.textContent = "Testing one frame with ChatGPT…";
  try {
    const selected = modelSelect.value;
    const data = await apiJSON("/api/chatgpt/test", { model: selected });
    testedModel = selected; testedModels.push(selected);
    authStatus.textContent = `Test passed in ${data.seconds}s. ${data.signs.length} sign(s): ${data.signs.map(s => s.sign_text || s.sign_id).join(", ") || "none"}. Ready for Start drive.`;
  } catch (error) { authStatus.textContent = error.message; }
  finally { testButton.disabled = false; updateStart(); }
});
async function loadChatGPT() {
  try {
    const status = await apiJSON("/api/chatgpt/status");
    testedModels = status.tested_models || [];
    accountSelect.replaceChildren(new Option("Add an account", ""));
    for (const a of status.accounts) accountSelect.add(new Option(a.label, a.id));
    accountSelect.value = status.active || "";
    document.querySelector("#chatgpt-disconnect").hidden = !status.connected;
    if (status.connected) {
      authStatus.textContent = "Connected. Loading your available models…";
      const data = await apiJSON("/api/chatgpt/models");
      modelSelect.replaceChildren(...data.models.map(m => new Option(m.name, m.id)));
      if (!data.models.length) throw new Error("No models were available for this account.");
      const efficient = data.models.find(m => /luna/i.test(m.id));
      const previous = data.models.find(m => m.id === (status.tested_models || [])[0]);
      if (previous || efficient) modelSelect.value = (previous || efficient).id;
      if (previous) testedModel = previous.id;
      modelSelect.disabled = false;
      testButton.disabled = false;
      authStatus.textContent = testedModel ? "Connected. This model passed the image test; ready for Start drive." : "Connected. Choose a model and click Test one frame. This verifies image access before driving.";
    }
    const params = new URLSearchParams(location.search);
    if (params.get("chatgpt") === "failed") authStatus.textContent = params.get("message") || "Sign-in failed or was declined. Try Continue with ChatGPT again.";
    if (params.has("chatgpt")) history.replaceState(null, "", "/");
  } catch (error) { authStatus.textContent = error.message; }
  updateStart();
}
updateStart();

function showMetrics(m) {
  document.querySelector("#pipeline-status").textContent = m.frames == null ? "" :
    `${m.frames} frames analyzed · ${m.pending || 0} reading · ${m.resolved || 0} recognized · ${m.tentative || 0} tentative · ${m.unresolved || 0} unresolved · ${m.cloud_requests || 0} cloud image calls` +
    (m.audio_ready == null ? "" : ` · ${m.audio_ready} voice clips ready`) +
    (m.audio_failed ? ` · ${m.audio_failed} voice failures` : "") +
    (m.cloud_cache_hits ? ` · ${m.cloud_cache_hits} cached readings` : "");
}
const cueQueue = [];
let cueBusy = false;
let cueGeneration = 0;
function clearCues() {
  for (const [eventId, status] of playbackStates) {
    if (status === "playing") setPlayback(eventId, "canceled");
  }
  cueGeneration++; cueQueue.length = 0; cueBusy = false;
  player.pause(); player.onended = null; player.onerror = null;
  speechSynthesis.cancel();
}
function enqueueCue(cue) {
  if (cue.drive_id && activeDriveId && cue.drive_id !== activeDriveId) return;
  updateAudio({...cue, status: cue.audio_url ? "ready" : "failed", reason: cue.audio_url ? "" : "No ElevenLabs audio returned."});
  if (!cue.replay && currentMode !== "driving") return;
  const announcementKey = `${cue.event_id || ""}:${cue.text || ""}`;
  if (!cue.replay && cue.event_id && announcedEvents.has(announcementKey)) return;
  if (!voiceOn) { audioHint.textContent = "Audio ready. Use Replay ElevenLabs cue to enable playback."; return; }
  if (cue.event_id && !cue.replay) announcedEvents.add(announcementKey);
  cueQueue.push(cue); drainCues();
}
function drainCues() {
  if (cueBusy || !cueQueue.length) return;
  const cue = cueQueue.shift();
  cueBusy = true;
  const generation = cueGeneration;
  let settled = false;
  const done = () => {
    if (settled || generation !== cueGeneration) return;
    settled = true; cueBusy = false;
    setPlayback(cue.event_id, "played");
    audioHint.textContent = cueQueue.length ? `${cueQueue.length} announcement(s) waiting.` : "Voice ready.";
    drainCues();
  };
  const failed = (error) => {
    if (settled || generation !== cueGeneration) return;
    setPlayback(cue.event_id, error?.name === "NotAllowedError" ? "blocked" : "failed");
    audioHint.textContent = "ElevenLabs playback failed or was blocked. Use the sign’s Replay button to try again.";
    settled = true; cueBusy = false;
    if (cueQueue.length) drainCues();
  };
  if (!cue.audio_url) { failed(); return; }
  audioHint.textContent = `Speaking: ${cue.text || "Sign announcement"}`;
  player.src = cue.audio_url; player.volume = 1;
  player.onended = done; player.onerror = failed;
  player.play().then(() => {
    if (generation === cueGeneration && !settled) setPlayback(cue.event_id, "playing");
  }).catch(failed);
}
