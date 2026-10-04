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

function setMode(name) {
  mode.textContent = name.toUpperCase();
}

function escapeHTML(value) {
  const el = document.createElement("span");
  el.textContent = String(value || "");
  return el.innerHTML.replaceAll('"', "&quot;");
}

function addCard(row) {
  const title = row.sign_id !== "unknown" && (!row.sign_text || row.sign_text === "Unresolved sign")
    ? row.sign_id.replaceAll("_", " ").toUpperCase() : row.sign_text;
  const card = document.createElement("article");
  card.className = "card";
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
  card.innerHTML = `
    <img src="${escapeHTML(row.thumb_url || "")}" alt="" />
    <div>
      <strong>${escapeHTML(title || row.sign_id)}</strong>
      ${chip}
      ${meaning}
    </div>`;
  if (row.event_id) {
    card.dataset.eventId = row.event_id;
    const previous = [...log.children].find(c => c.dataset.eventId === row.event_id);
    if (previous) previous.remove();
  }
  if (row.recognition_status === "unresolved") log.append(card);
  else log.prepend(card);
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
    body: JSON.stringify({ source, detector: detectorSelect.value, model: modelSelect.value, cloud_assist: document.querySelector("#cloud-assist").checked }),
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
  log.hidden = false;
  practice.hidden = true;
  panelTitle.textContent = "Sign log";
  park.disabled = false;
  setMode("driving");
  hint.textContent = "Driving. Recognized safety signs receive short descriptive announcements.";
}

document.querySelector("#start-file").addEventListener("click", () => start("file"));

park.addEventListener("click", async () => {
  clearCues();
  const response = await fetch("/api/park", { method: "POST" });
  const body = await response.json();
  setMode("parked");
  hint.textContent = "Parked. Answer in English.";
  panelTitle.textContent = "Parked practice";
  log.hidden = true;
  if (!body.next || body.next.type === "practice_done") {
    practice.hidden = false;
    question.textContent = "No catalog signs on this drive yet.";
    progress.textContent = "";
    spanishText = "";
    return;
  }
  showQuestion(body.next);
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
    if (message.type === "detector_status") hint.textContent = message.message || message.status;
    if (message.type === "detection" || message.type === "detection_update") addCard(message);
    if (message.type === "detection_remove") [...log.children].find(c => c.dataset.eventId === message.event_id)?.remove();
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
  const needsChatGPT = detectorSelect.value === "chatgpt" || (local && document.querySelector("#cloud-assist").checked);
  document.querySelector("#local-controls").hidden = !local;
  document.querySelector("#chatgpt-controls").hidden = !needsChatGPT;
  startButton.disabled = needsChatGPT && (!testedModel || testedModel !== modelSelect.value);
}
detectorSelect.addEventListener("change", updateStart);
document.querySelector("#cloud-assist").addEventListener("change", updateStart);
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
    await loadChatGPT();
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
loadChatGPT();

function showMetrics(m) {
  document.querySelector("#pipeline-status").textContent = m.frames == null ? "" :
    `${m.frames} frames analyzed · ${m.pending} reading · ${m.resolved} read · ${m.unresolved} unresolved · ${m.cloud_requests} cloud crops · ${m.inference_ms} ms/frame`;
}
const cueQueue = [];
let cueBusy = false;
let cueGeneration = 0;
function clearCues() { cueGeneration++; cueQueue.length = 0; cueBusy = false; player.pause(); player.onended = null; player.onerror = null; speechSynthesis.cancel(); }
function enqueueCue(cue) { if (!voiceOn) return; cueQueue.push({...cue, received: Date.now()}); drainCues(); }
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
