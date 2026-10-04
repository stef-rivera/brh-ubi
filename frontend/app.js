const video = document.querySelector("#video");
const mode = document.querySelector("#mode");
const log = document.querySelector("#log");
const hint = document.querySelector("#hint");
const park = document.querySelector("#park");
const pauseDrive = document.querySelector("#pause-drive");
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
let lastQuestionKey = "";
const rows = new Map();
const audioStates = new Map();
const playbackStates = new Map();
const playedEvents = new Set();
const spokenSnapshots = new Map();
const announcedEvents = new Set();
const feedbackEditors = new Map();
let feedbackCatalog = [];
let catalogError = "";
let currentCueEvent = "";
let currentCueCancel = null;
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
  clearCues();
  const generation = cueGeneration;
  if (!audioUrl) {
    audioHint.textContent = "Speaking with browser voice: ElevenLabs audio unavailable.";
    sayBrowser(text, lang);
    return;
  }
  player.pause();
  player.src = audioUrl;
  player.volume = 1;
  audioHint.textContent = "Speaking with ElevenLabs.";
  player.play().catch((error) => {
    if (generation !== cueGeneration || error?.name === "AbortError") return;
    player.pause();
    audioHint.textContent = "Speaking with browser voice: ElevenLabs playback failed.";
    sayBrowser(text, lang);
  });
}

function setMode(name) {
  currentMode = name;
  if (name !== "parked") stopCoach();
  document.body.classList.remove("idle", "driving", "paused", "parked");
  document.body.classList.add(name);
  if (name !== "parked") document.querySelector("#practice-panel").hidden = true;
  pauseDrive.disabled = !["driving", "paused"].includes(name);
  pauseDrive.textContent = name === "paused" ? "Resume drive" : "Pause drive";
  if (name === "paused") clearCues();
  mode.textContent = name.toUpperCase();
}

function escapeHTML(value) {
  const el = document.createElement("span");
  el.textContent = String(value || "");
  return el.innerHTML.replaceAll('"', "&quot;");
}

let activeLogTab = "audio";
function belongsInAudio(row, audio, playback) {
  if (row.recognition_status === "excluded" || row.exclude_from_practice || ["ignore", "not_sign"].includes(row.feedback_action)) return false;
  if (playedEvents.has(row.event_id) || spokenSnapshots.has(row.event_id)) return true;
  if (["playing", "played"].includes(playback)) return true;
  if (["skipped", "failed", "canceled"].includes(playback)) return false;
  if (audio?.status === "failed" || (audio?.status === "skipped" && audio.reason)) return false;
  if (audio?.audio_url || ["queued", "ready"].includes(audio?.status)) return true;
  return row.driving_audio_eligible ?? Boolean(row.printed_text && row.sign_id && row.sign_id !== "unknown" && row.recognition_status !== "pending");
}
function refreshLogTabs() {
  let audioCount = 0, otherCount = 0;
  for (const card of log.children) {
    const row = rows.get(card.dataset.eventId);
    const inAudio = row && belongsInAudio(row, audioStates.get(card.dataset.eventId), playbackStates.get(card.dataset.eventId));
    if (inAudio) audioCount++; else otherCount++;
    card.hidden = (inAudio ? "audio" : "other") !== activeLogTab;
  }
  for (const [id, label, count, tab] of [["log-audio-tab", "Audio cues", audioCount, "audio"], ["log-other-tab", "Everything else", otherCount, "other"]]) {
    const button = document.getElementById(id);
    button.textContent = `${label} (${count})`;
    button.setAttribute("aria-selected", String(activeLogTab === tab));
    button.tabIndex = activeLogTab === tab ? 0 : -1;
  }
  log.setAttribute("aria-labelledby", activeLogTab === "audio" ? "log-audio-tab" : "log-other-tab");
  const empty = document.getElementById("log-empty");
  empty.hidden = Boolean(activeLogTab === "audio" ? audioCount : otherCount);
  empty.textContent = activeLogTab === "audio" ? "No signs assigned an audio cue yet. Other detections are in Everything else." : "No other detections yet.";
}
for (const [id, tab] of [["log-audio-tab", "audio"], ["log-other-tab", "other"]]) {
  const button = document.getElementById(id);
  button.addEventListener("click", () => {activeLogTab = tab; refreshLogTabs();});
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    activeLogTab = event.key === "Home" ? "audio" : event.key === "End" ? "other" : activeLogTab === "audio" ? "other" : "audio";
    refreshLogTabs(); document.getElementById(activeLogTab === "audio" ? "log-audio-tab" : "log-other-tab").focus();
  });
}

function addCard(row) {
  if (row.drive_id && activeDriveId && row.drive_id !== activeDriveId) return;
  const key = row.event_id || `${row.sign_id}:${row.ts_ms || rows.size}`;
  const existing = rows.get(key);
  // A late worker result must not overwrite a human review in this tab.
  if (existing?.human_reviewed && !row.human_reviewed && row.feedback_action !== "undo") return;
  const spoken = spokenSnapshots.get(key);
  if (spoken) {
    if (row.recognition_status === "pending") row = {...(existing?.recognition_status !== "pending" ? existing : spoken.row), drive_id: row.drive_id, event_id: key};
    row = {...row, spoken_cue_text: spoken.text};
  }
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
  const excluded = row.exclude_practice || ["not_sign", "ignore"].includes(row.feedback_action);
  const status = row.human_reviewed ? ({confirm: "You confirmed", correct: "You corrected", not_sign: "Not a road sign", ignore: "Ignored"}[row.feedback_action] || "Reviewed") : row.recognition_status === "pending" ? "Reading in background" :
    row.recognition_status === "unresolved" ? "Unresolved" :
    row.recognition_status === "tentative" ? "Demo guess" :
    row.sign_id === "unknown" ? "Outside catalog" : "Recognized";
  const reason = row.recognition_error || (row.recognition_status === "pending" ?
    "Detection continues while this crop is read." : "");
  const audio = audioStates.get(key);
  const playback = playbackStates.get(key);
  const audioText = playback === "canceled" ? "Playback stopped. Replay is available." :
    playback === "played" ? "ElevenLabs audio played in this tab" :
    playback === "skipped" ? "Cue skipped to avoid late or repeated speech. Replay is available." :
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
      ${row.meaning && !excluded ? `<p class="meaning">${row.recognition_status === "tentative" ? "If this prediction is correct: " : ""}${escapeHTML(row.meaning)}</p>` : ""}
      ${row.meaning && !excluded && !row.verified ? '<div class="draft">Draft catalog meaning.</div>' : ""}
      ${reason ? `<p class="recognition-reason">${escapeHTML(reason)}</p>` : ""}
      ${row.spoken_cue_text ? `<p class="audio-detail">Spoken cue: ${escapeHTML(row.spoken_cue_text)}${excluded ? " · excluded by later review" : ""}</p>` : ""}
      ${audioText ? `<p class="audio-detail">${escapeHTML(audioText)}</p>` : ""}
      ${audio?.audio_url && !excluded ? '<button type="button" class="secondary replay">Replay ElevenLabs cue</button>' : ""}
    </div>`;
  card.querySelector(".replay")?.addEventListener("click", () => {
    unlockVoice();
    enqueueCue({event_id: key, ...audio, replay: true});
  });
  attachFeedback(card, row, key);
  const previous = [...log.children].find(c => c.dataset.eventId === key);
  const focused = previous?.contains(document.activeElement) ? document.activeElement : null;
  const focusField = focused?.dataset.feedbackField;
  const selection = focused?.type !== "number" && focused?.tagName === "INPUT" ? [focused.selectionStart, focused.selectionEnd] : null;
  if (previous) previous.replaceWith(card);
  else if (row.recognition_status === "unresolved") log.append(card);
  else log.prepend(card);
  refreshLogTabs();
  if (focusField && !card.hidden) {
    const nextInput = card.querySelector(`[data-feedback-field="${focusField}"]`);
    nextInput?.focus({preventScroll: true});
    if (selection && nextInput?.setSelectionRange) nextInput.setSelectionRange(...selection);
  }
}

function setPlayback(eventId, status) {
  if (!eventId) return;
  if (status === "playing") {
    const row = rows.get(eventId);
    if (row) spokenSnapshots.set(eventId, {row: {...row}, text: audioStates.get(eventId)?.text || row.audio_text || row.sign_text});
  }
  playbackStates.set(eventId, status);
  if (status === "played") playedEvents.add(eventId);
  const row = rows.get(eventId);
  if (row) addCard(row);
  if (status === "playing" && row) {
    activeLogTab = "audio";
    const card = [...log.children].find(item => item.dataset.eventId === eventId);
    if (card) {log.prepend(card); card.classList.add("cue-highlight");}
    refreshLogTabs();
    log.scrollTop = 0;
  }
  document.querySelector("#playback-status").textContent =
    `${playedEvents.size} sign cue(s) played in this tab`;
}

function updateAudio(message) {
  if (message.drive_id && activeDriveId && message.drive_id !== activeDriveId) return;
  if (rows.get(message.event_id)?.human_reviewed) return;
  audioStates.set(message.event_id, {...audioStates.get(message.event_id), ...message});
  const row = rows.get(message.event_id);
  if (row) addCard({...row, audio_status: message.status, audio_url: message.audio_url || row.audio_url,
    audio_text: message.text || row.audio_text, audio_error: message.reason || ""});
}

async function start(source) {
  coachGeneration++; stopCoach(); parkVoicePending = false;
  result.textContent = "";
  lastQuestionKey = "";
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
  rows.clear(); feedbackEditors.clear(); audioStates.clear(); playbackStates.clear(); playedEvents.clear(); spokenSnapshots.clear(); announcedEvents.clear();
  document.querySelector("#playback-status").textContent = "";
  showMetrics({});
  audioHint.textContent = "Voice enabled. Confirmed catalog signs will be announced.";
  video.src = "/stream.mjpg?t=" + Date.now();
  spanishText = "";
  clearCues();
  log.replaceChildren();
  refreshLogTabs();
  log.hidden = false;
  practice.hidden = true;
  panelTitle.textContent = "Sign log";
  park.disabled = false;
  setMode("driving");
  hint.textContent = "Driving. Confirmed catalog signs receive short announcements; reading continues in the background.";
}

document.querySelector("#start-file").addEventListener("click", () => start(document.querySelector("#clip").value));

// Selecting a different sample immediately starts that clip.
document.querySelector("#clip").addEventListener("change", async (event) => {
  const selector = event.currentTarget;
  const startControl = document.querySelector("#start-file");
  selector.disabled = true;
  startControl.disabled = true;
  clearCues();
  try {
    await start(selector.value);
  } catch (error) {
    hint.textContent = error.message || "Could not switch videos.";
  } finally {
    selector.disabled = false;
    startControl.disabled = !socket || socket.readyState !== WebSocket.OPEN;
  }
});

pauseDrive.addEventListener("click", async () => {
  const endpoint = currentMode === "paused" ? "resume" : "pause";
  pauseDrive.disabled = true;
  try {
    const response = await fetch(`/api/drive/${endpoint}`, {method: "POST"});
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Could not change playback.");
    setMode(body.mode);
    hint.textContent = body.mode === "paused" ? "Drive paused. Resume to continue from here." : "Driving resumed.";
  } catch (error) {
    setMode(currentMode);
    hint.textContent = error.message;
  }
});

park.addEventListener("click", async () => {
  result.textContent = "";
  unlockVoice(); lastQuestionKey = ""; clearCues();
  const generation = ++coachGeneration;
  stopCoach(); parkVoicePending = true;
  try {
    const response = await fetch("/api/park", {method: "POST"});
    const body = await response.json();
    if (generation !== coachGeneration) return;
    if (!response.ok) throw new Error(body.detail || "Could not park.");
    setMode("parked");
    document.querySelector("#practice-panel").hidden = false;
    typedPracticeNext = body.next;
    if (await beginVoicePractice(generation)) return;
    if (generation !== coachGeneration) return;
    parkVoicePending = false;
    showTypedPractice();
  } catch (error) {
    parkVoicePending = false; hint.textContent = error.message;
  }
});

function showTypedPractice() {
  hint.textContent = "Parked. Answer in English.";
  practice.hidden = false;
  if (typedPracticeNext && typedPracticeNext.type !== "practice_done") showQuestion(typedPracticeNext);
  else {question.textContent = "No catalog signs on this drive yet."; progress.textContent = ""; answer.disabled = true;}
}

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
  typedPracticeNext = item;
  if (parkVoicePending || coachSocket) return;
  const key = JSON.stringify([activeDriveId, item.event_id, item.index, item.total, item.question]);
  const duplicate = key === lastQuestionKey;
  lastQuestionKey = key;
  practice.hidden = false;
  document.querySelector("#practice-panel").hidden = false;
  log.hidden = false;
  answer.disabled = false;
  progress.textContent = `${item.index} / ${item.total}`;
  question.textContent = item.question;
  // Keep the last answer grade visible while the next question arrives.
  qThumb.hidden = !item.thumb_url;
  if (item.thumb_url) {
    qThumb.hidden = false;
    qThumb.src = item.thumb_url;
  }
  spanishText = item.meaning_es || "";
  if (!duplicate) say(item.question, item.audio_url);
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
        clearCues(); rows.clear(); feedbackEditors.clear(); audioStates.clear(); playbackStates.clear(); playedEvents.clear(); spokenSnapshots.clear(); announcedEvents.clear();
  document.querySelector("#playback-status").textContent = "";
      }
      activeDriveId = message.drive_id || "";
      setMode(message.mode);
      if (message.detection_error) hint.textContent = message.detection_error;
      if (Array.isArray(message.detections)) {
        log.replaceChildren();
        message.detections.forEach(addCard);
        for (const [eventId, spoken] of spokenSnapshots) {
          if (!message.detections.some(row => row.event_id === eventId)) addCard(rows.get(eventId) || spoken.row);
        }
        if (message.drive_id) {
          video.src = "/stream.mjpg";
          park.disabled = false;
        }
      }
    }
    if (message.drive_id && activeDriveId && message.drive_id !== activeDriveId) return;
    if (message.type === "feedback_saved") cancelSignCue(message.event_id);
    if (message.type === "practice_question") showQuestion(message);
    if (message.type === "practice_done") {
      practice.hidden = false; question.textContent = "That's the set from this drive.";
      progress.textContent = ""; answer.disabled = true; spanishText = "";
    }
    if (message.type === "audio_status") updateAudio(message);
    if (message.type === "detector_status") hint.textContent = message.message || message.status;
    if (message.type === "detection" || message.type === "detection_update") addCard(message);
    if (message.type === "detection_remove") {
      if (spokenSnapshots.has(message.event_id) || playedEvents.has(message.event_id)) return;
      rows.delete(message.event_id); audioStates.delete(message.event_id);
      [...log.children].find(c => c.dataset.eventId === message.event_id)?.remove();
      refreshLogTabs();
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
const lastSpokenLabels = new Map();
let cueBusy = false;
let cueGeneration = 0;
function clearCues() {
  for (const [eventId, status] of playbackStates) {
    if (status === "playing") setPlayback(eventId, "canceled");
  }
  currentCueEvent = ""; currentCueCancel = null;
  cueGeneration++; cueQueue.length = 0; cueBusy = false;
  lastSpokenLabels.clear();
  player.pause(); player.onended = null; player.onerror = null;
  speechSynthesis.cancel();
}
function enqueueCue(cue) {
  if (cue.drive_id && activeDriveId && cue.drive_id !== activeDriveId) return;
  if (cue.detection && !rows.get(cue.event_id)?.human_reviewed) addCard(cue.detection);
  const reviewedRow = rows.get(cue.event_id);
  if (reviewedRow?.recognition_status === "excluded") return;
  if (reviewedRow?.human_reviewed && !cue.replay && !cue.human_replay) return;
  updateAudio({...cue, status: cue.audio_url ? "ready" : "failed", reason: cue.audio_url ? "" : "No ElevenLabs audio returned."});
  if (!cue.replay && currentMode !== "driving") return;
  const announcementKey = `${cue.event_id || ""}:${cue.text || ""}`;
  if (!cue.replay && cue.event_id && announcedEvents.has(announcementKey)) return;
  if (!voiceOn) { audioHint.textContent = "Audio ready. Use Replay ElevenLabs cue to enable playback."; return; }
  if (cue.event_id && !cue.replay) announcedEvents.add(announcementKey);
  cue.receivedAt = performance.now();
  cue.deadline = cue.receivedAt + Math.max(0, (cue.audio_window_seconds || 2) - (cue.seconds_since_detected || 0)) * 1000;
  cue.labelKey = (cue.text || "").toLowerCase().trim();
  if (!cue.replay && cueQueue.some(item => !item.replay && item.labelKey === cue.labelKey)) return;
  cueQueue.push(cue);
  cueQueue.sort((a,b) => Number(b.replay)-Number(a.replay) || (b.priority || 0)-(a.priority || 0) || b.receivedAt-a.receivedAt);
  drainCues();
}
function drainCues() {
  if (cueBusy || !cueQueue.length) return;
  const cue = cueQueue.shift();
  const row = rows.get(cue.event_id);
  if (!row || row.recognition_status === "excluded" || row.exclude_from_practice || ["ignore", "not_sign"].includes(row.feedback_action)) {
    drainCues(); return;
  }
  const last = lastSpokenLabels.get(cue.labelKey);
  if (!cue.replay && (performance.now() > cue.deadline || (last != null && performance.now()-last < 8000))) {
    setPlayback(cue.event_id, "skipped");
    drainCues(); return;
  }
  currentCueEvent = cue.event_id;
  cueBusy = true;
  const generation = cueGeneration;
  let settled = false;
  const done = () => {
    if (settled || generation !== cueGeneration) return;
    settled = true; cueBusy = false; currentCueEvent = ""; currentCueCancel = null;
    setPlayback(cue.event_id, "played");
    audioHint.textContent = cueQueue.length ? `${cueQueue.length} announcement(s) waiting.` : "Voice ready.";
    drainCues();
  };
  const failed = (error) => {
    if (settled || generation !== cueGeneration) return;
    setPlayback(cue.event_id, error?.name === "NotAllowedError" ? "blocked" : "failed");
    audioHint.textContent = "ElevenLabs playback failed or was blocked. Use the sign’s Replay button to try again.";
    settled = true; cueBusy = false; currentCueEvent = ""; currentCueCancel = null;
    if (cueQueue.length) drainCues();
  };
  currentCueCancel = () => {
    settled = true; cueBusy = false; currentCueEvent = ""; currentCueCancel = null;
    player.pause(); player.onended = null; player.onerror = null;
    drainCues();
  };
  if (!cue.audio_url) { failed(); return; }
  audioHint.textContent = `Speaking: ${cue.text || "Sign announcement"}`;
  audioStates.set(cue.event_id, {...audioStates.get(cue.event_id), text: cue.text, audio_url: cue.audio_url, status: "ready"});
  player.src = cue.audio_url; player.volume = 1;
  player.onended = done; player.onerror = failed;
  player.play().then(() => {
    if (generation === cueGeneration && !settled) {
      if (!cue.replay) lastSpokenLabels.set(cue.labelKey, performance.now());
      setPlayback(cue.event_id, "playing");
    }
  }).catch(failed);
}


function cancelSignCue(eventId) {
  for (let i = cueQueue.length - 1; i >= 0; i--) {
    if (cueQueue[i].event_id === eventId) cueQueue.splice(i, 1);
  }
  if (currentCueEvent === eventId) currentCueCancel?.();
  audioStates.delete(eventId); playbackStates.delete(eventId);
}

async function loadFeedbackCatalog() {
  try {
    const data = await apiJSON("/api/catalog");
    feedbackCatalog = Array.isArray(data) ? data : (data.signs || data.catalog || data.items || []);
    if (!Array.isArray(feedbackCatalog)) throw new Error("Could not load sign labels.");
    catalogError = "";
  } catch (error) { catalogError = error.message; }
  for (const row of rows.values()) addCard(row);
}

function attachFeedback(card, row, key) {
  if (!row.event_id || !row.drive_id) return;
  let editor = feedbackEditors.get(key);
  if (!editor) {
    editor = {open: false, sign_id: row.sign_id === "unknown" ? "" : (row.sign_id || ""),
      sign_text: row.sign_text || "", value: row.value ?? "", saving: false, error: ""};
    feedbackEditors.set(key, editor);
  }
  const box = document.createElement("section");
  box.className = "sign-feedback";
  box.setAttribute("aria-label", `Review ${row.sign_text || "sign"}`);
  const actions = document.createElement("div");
  actions.className = "feedback-actions";
  const button = (label, action) => {
    const el = document.createElement("button"); el.type = "button";
    el.className = "secondary"; el.textContent = label; el.disabled = editor.saving;
    el.addEventListener("click", action); actions.append(el);
  };
  if (row.human_reviewed) button("Undo review", () => saveSignFeedback(key, "undo"));
  else {
    button("Yes, correct", () => saveSignFeedback(key, "confirm"));
    button("No, fix it", () => {editor.open = !editor.open; addCard(rows.get(key));});
    button("Not a road sign", () => saveSignFeedback(key, "not_sign"));
    button("Ignore", () => saveSignFeedback(key, "ignore"));
  }
  box.append(actions);
  if (editor.open && !row.human_reviewed) {
    const form = document.createElement("form"); form.className = "feedback-editor";
    const preview = document.createElement("img"); preview.className = "feedback-crop";
    preview.alt = "Original captured sign crop for review";
    preview.src = `/api/feedback/image?event_id=${encodeURIComponent(key)}&drive_id=${encodeURIComponent(row.drive_id)}`;
    preview.addEventListener("error", () => { if (!preview.dataset.fallback) {preview.dataset.fallback = "1"; preview.src = row.thumb_url || "";} });
    form.append(preview);
    const label = document.createElement("label"); label.textContent = "Correct sign";
    const select = document.createElement("select"); select.dataset.feedbackField = "label";
    const searchLabel = document.createElement("label"); searchLabel.textContent = "Search sign options";
    const search = document.createElement("input"); search.type = "search";
    search.dataset.feedbackField = "search"; search.placeholder = "Type a sign name, e.g. no u turn";
    search.value = editor.search || ""; search.disabled = editor.saving;
    const populate = () => {
      const query = search.value.toLowerCase().replace(/[-_]/g, " ").trim();
      select.replaceChildren();
      if (!query) select.add(new Option("Choose a sign…", ""));
      const matches = feedbackCatalog.filter(sign =>
        `${sign.sign_text} ${sign.id}`.toLowerCase().replace(/[-_]/g, " ").includes(query));
      for (const sign of matches) select.add(new Option(sign.sign_text || sign.id.replaceAll("_", " "), sign.id));
      if (!matches.length) select.add(new Option("No matching signs", ""));
      select.size = query ? Math.min(6, Math.max(2, matches.length)) : 1;
      if (matches.some(sign => sign.id === editor.sign_id)) select.value = editor.sign_id;
    };
    populate();
    search.addEventListener("input", () => {editor.search = search.value; populate();});
    search.addEventListener("keydown", event => {
      if (event.key === "ArrowDown") {event.preventDefault(); select.focus();}
      if (event.key === "Enter") {
        event.preventDefault();
        if (select.value) select.dispatchEvent(new Event("change"));
      }
    });
    searchLabel.append(search); form.append(searchLabel);
    if (editor.sign_id && !feedbackCatalog.some(sign => sign.id === editor.sign_id)) select.add(new Option(editor.sign_id.replaceAll("_", " "), editor.sign_id));
    select.value = editor.sign_id; select.disabled = editor.saving;
    select.addEventListener("change", () => {
      editor.sign_id = select.value;
      const sign = feedbackCatalog.find(item => item.id === select.value);
      if (sign) editor.sign_text = sign.sign_text || select.selectedOptions[0].textContent;
      addCard(rows.get(key));
    });
    label.append(select); form.append(label);
    const textLabel = document.createElement("label"); textLabel.textContent = "Exact sign text (optional)";
    const textInput = document.createElement("input"); textInput.dataset.feedbackField = "text"; textInput.value = editor.sign_text;
    textInput.disabled = editor.saving;
    textInput.addEventListener("input", () => {editor.sign_text = textInput.value;});
    textLabel.append(textInput); form.append(textLabel);
    if (feedbackCatalog.find(item => item.id === editor.sign_id)?.requires_value) {
      const speedLabel = document.createElement("label"); speedLabel.textContent = "Speed shown (mph)";
      const speed = document.createElement("input"); speed.dataset.feedbackField = "speed"; speed.type = "number"; speed.min = "5"; speed.max = "85"; speed.step = "5";
      speed.value = editor.value; speed.required = true; speed.disabled = editor.saving;
      speed.addEventListener("input", () => {editor.value = speed.value;});
      speedLabel.append(speed); form.append(speedLabel);
    }
    const save = document.createElement("button"); save.type = "submit";
    save.textContent = editor.saving ? "Saving…" : "Save correction";
    save.disabled = editor.saving || !feedbackCatalog.length; form.append(save);
    const note = document.createElement("p"); note.className = "feedback-note";
    note.textContent = "Fixes this captured sign and saves an example for later training. Ignore keeps real signs out of practice; Not a road sign marks a false detection.";
    form.append(note);
    if (catalogError) {
      const retry = document.createElement("button"); retry.type = "button"; retry.className = "secondary";
      retry.textContent = "Reload sign labels"; retry.addEventListener("click", loadFeedbackCatalog); form.append(retry);
    }
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!editor.sign_id) {editor.error = "Choose the correct sign label."; addCard(rows.get(key)); return;}
      saveSignFeedback(key, "correct");
    });
    box.append(form);
  }
  if (editor.error || (editor.open && catalogError)) {
    const error = document.createElement("p"); error.className = "feedback-error"; error.setAttribute("role", "alert");
    error.textContent = editor.error || catalogError; box.append(error);
  }
  card.querySelector(".card-body").append(box);
}

async function saveSignFeedback(key, action) {
  const row = rows.get(key); const editor = feedbackEditors.get(key);
  if (!row || !editor || editor.saving) return;
  const driveId = activeDriveId;
  editor.saving = true; editor.error = ""; addCard(row);
  try {
    const body = {drive_id: row.drive_id, event_id: key, action};
    if (action === "correct") {
      body.sign_id = editor.sign_id; body.sign_text = editor.sign_text.trim();
      if (feedbackCatalog.find(item => item.id === editor.sign_id)?.requires_value) body.value = Number(editor.value);
    }
    const response = await apiJSON("/api/feedback", body);
    if (activeDriveId !== driveId) return;
    cancelSignCue(key); editor.open = false;
    if (response.row) { if (action === "undo") rows.delete(key); addCard(response.row); }
    if (response.practice_reset) hint.textContent = "Review saved. Parked practice now uses your corrected signs.";
  } catch (error) {
    if (activeDriveId === driveId) {editor.error = error.message; addCard(rows.get(key) || row);}
  } finally {
    editor.saving = false;
    if (activeDriveId === driveId && rows.has(key)) addCard(rows.get(key));
  }
}
loadFeedbackCatalog();

function setLogOpen(open) {
  document.querySelector('#panel').hidden = !open;
  document.querySelector('#log-toggle').setAttribute('aria-expanded', String(open));
  document.body.classList.toggle('log-open', open);
}
document.querySelector('#log-toggle').addEventListener('click', () => setLogOpen(document.querySelector('#panel').hidden));
document.querySelector('#log-close').addEventListener('click', () => {setLogOpen(false); document.querySelector('#log-toggle').focus();});
document.querySelector('#practice-close').addEventListener('click', () => {coachGeneration++; stopCoach(); parkVoicePending = false; document.querySelector('#practice-panel').hidden = true; clearCues();});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {coachGeneration++; stopCoach(); parkVoicePending = false; setLogOpen(false); document.querySelector('#practice-panel').hidden = true;}
});

const coachStatus = document.querySelector("#coach-status");
const VOICE_RATE = 24000;
let coachSocket = null, micStream = null, micContext = null, micProcessor = null;
let playContext = null, playTime = 0, coachSources = [], coachStopped = true, coachGeneration = 0;
let parkVoicePending = false, typedPracticeNext = null;

async function beginVoicePractice(generation) {
  coachStatus.textContent = "Connecting to the voice coach…";
  try {
    const data = await apiJSON("/api/practice/voice", {});
    if (generation !== coachGeneration || currentMode !== "parked") return false;
    const stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
    if (generation !== coachGeneration || currentMode !== "parked") {stream.getTracks().forEach(track=>track.stop()); return false;}
    clearCues(); parkVoicePending = false; practice.hidden = true;
    document.querySelector("#coach-end").hidden = false; document.querySelector("#coach-start").hidden = true;
    hint.textContent = "Parked. Answer the coach out loud.";
    beginCoach(data.voice, stream);
    return true;
  } catch (error) {
    if (generation === coachGeneration) coachStatus.textContent = error.message || "Voice unavailable. You can type your answers.";
    return false;
  }
}
document.querySelector("#coach-start").addEventListener("click", async () => {
  const generation = ++coachGeneration; stopCoach(); clearCues(); parkVoicePending = true;
  if (!await beginVoicePractice(generation) && generation === coachGeneration) {parkVoicePending = false; showTypedPractice();}
});
document.querySelector("#coach-end").addEventListener("click", () => {
  coachGeneration++; stopCoach(); parkVoicePending = false; coachStatus.textContent = "Voice stopped. Type your answers below."; showTypedPractice();
});
window.addEventListener("pagehide", () => {coachGeneration++; stopCoach();});
function stopCoach() {
  coachStopped = true;
  document.querySelector("#coach-end").hidden = true;
  document.querySelector("#coach-start").hidden = false;
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
    if (message.type === "input_audio_buffer.speech_started") {
      for (const source of coachSources) {try {source.stop();} catch {}}
      coachSources = []; playTime = 0;
      coachStatus.textContent = "Listening to your answer…";
    }
    if (message.type === "response.created") coachStatus.textContent = "Coach is speaking.";
    if ((message.type === "response.output_audio.delta" || message.type === "response.audio.delta") && message.delta) {
      playCoachPcm(message.delta);
    }
    if (message.type === "response.done") coachStatus.textContent = "Listening. Answer out loud.";
    if (message.type === "error") hint.textContent = (message.error && message.error.message) || "The coach lost the connection.";
  };
  socket.onerror = () => {
    if (coachSocket !== socket) return;
    stopCoach(); parkVoicePending = false;
    coachStatus.textContent = "The voice coach could not connect. Type your answers below.";
    showTypedPractice();
  };
  socket.onclose = () => {
    if (coachSocket === socket) {
      stopCoach(); parkVoicePending = false;
      coachStatus.textContent = "Voice connection ended. You can continue by typing.";
      showTypedPractice();
    }
  };
}
