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
let player = new Audio();
let spanishText = "";

function unlockVoice() {
  voiceOn = true;
  player.src = "/audio/ad155beaf084f658.mp3";
  player.volume = 0;
  const started = player.play();
  if (started) {
    started.then(() => {
      player.pause();
      player.currentTime = 0;
      player.volume = 1;
    }).catch(() => {});
  }
}

function sayBrowser(text) {
  speechSynthesis.cancel();
  const clip = new SpeechSynthesisUtterance(text);
  clip.lang = "en-US";
  speechSynthesis.speak(clip);
}

function say(text, audioUrl) {
  if (!voiceOn || !text) return;
  speechSynthesis.cancel();
  if (!audioUrl) {
    sayBrowser(text);
    return;
  }
  player.pause();
  player.src = audioUrl;
  player.volume = 1;
  player.play().catch(() => sayBrowser(text));
}

function setMode(name) {
  mode.textContent = name.toUpperCase();
}

function addCard(row) {
  const card = document.createElement("article");
  card.className = "card";
  const meaning = row.meaning
    ? `<span>${row.meaning}</span>${row.verified ? "" : '<div class="draft">Draft meaning, not checked against the sign manual yet.</div>'}`
    : `<span>${row.sign_text || "Unrecognized sign"}</span>`;
  const chip = row.sign_id === "unknown"
    ? '<span class="chip quiet">not in catalog</span>'
    : row.safety_critical
      ? '<span class="chip">safety</span>'
      : "";
  card.innerHTML = `
    <img src="${row.thumb_url || ""}" alt="" />
    <div>
      <strong>${row.sign_text || row.sign_id}</strong>
      ${chip}
      ${meaning}
    </div>`;
  log.prepend(card);
}

async function start(source) {
  unlockVoice();
  hint.textContent = "Looking at the road…";
  const response = await fetch("/api/drive/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    hint.textContent = body.detail || "Could not start the drive.";
    return;
  }
  video.src = "/stream.mjpg?t=" + Date.now();
  log.replaceChildren();
  log.hidden = false;
  practice.hidden = true;
  panelTitle.textContent = "Sign log";
  park.disabled = false;
  setMode("driving");
  hint.textContent = "Driving. The coach stays quiet unless a sign is safety-critical.";
}

document.querySelector("#start-file").addEventListener("click", () => start("file"));
document.querySelector("#start-cam").addEventListener("click", () => start("webcam"));

park.addEventListener("click", async () => {
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
  if (!spanishText) return;
  unlockVoice();
  const response = await fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: spanishText }),
  });
  const body = await response.json();
  say(spanishText, body.audio_url);
});

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state" && message.mode) setMode(message.mode);
    if (message.type === "detection") addCard(message);
    if (message.type === "cue") say(message.text, message.audio_url);
  };
  socket.onclose = () => setTimeout(connect, 1000);
}

connect();
