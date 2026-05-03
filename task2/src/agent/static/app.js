const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let activeWS = null;
let activeSid = null;
let tokens = {prompt:0, completion:0};
let stepCount = 0;
const STEP_MAX = 50;
$("step-max").textContent = STEP_MAX;

// ---------- auth ----------
const TOKEN_KEY = "agent_auth_token";
let authToken = localStorage.getItem(TOKEN_KEY) || "";

function authHeaders() {
  return authToken ? {"Authorization": "Bearer " + authToken} : {};
}

async function authFetch(url, init = {}) {
  const headers = Object.assign({}, init.headers || {}, authHeaders());
  const r = await fetch(url, Object.assign({}, init, {headers}));
  if (r.status === 401) {
    authToken = "";
    localStorage.removeItem(TOKEN_KEY);
    showGate("Session expired — please re-enter the passcode.");
  }
  return r;
}

function wsUrl(path) {
  const base = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + path;
  return authToken ? base + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(authToken) : base;
}

function showGate(msg = "") {
  $("gate-err").textContent = msg;
  $("gate").classList.add("show");
  setTimeout(() => $("gate-input").focus(), 0);
}
function hideGate() { $("gate").classList.remove("show"); $("gate-input").value = ""; $("gate-err").textContent = ""; }

async function tryToken(t) {
  // Probe an auth-gated endpoint with the candidate token.
  const r = await fetch("/api/sessions", {headers: {"Authorization": "Bearer " + t}});
  return r.status !== 401;
}

$("gate-submit").onclick = async () => {
  const t = $("gate-input").value.trim();
  if (!t) { $("gate-err").textContent = "Please enter the passcode."; return; }
  $("gate-err").textContent = "Checking…";
  const ok = await tryToken(t);
  if (!ok) { $("gate-err").textContent = "Wrong passcode. Try again."; return; }
  authToken = t;
  localStorage.setItem(TOKEN_KEY, t);
  hideGate();
  boot();
};
$("gate-input").addEventListener("keydown", e => { if (e.key === "Enter") $("gate-submit").click(); });

// ---------- sidebar ----------
async function refreshSessions() {
  try {
    const r = await authFetch("/api/sessions");
    if (!r.ok) return;
    const list = await r.json();
    const div = $("sessions");
    div.innerHTML = "";
    for (const s of list) {
      const el = document.createElement("div");
      el.className = "session" + (s.sid === activeSid ? " active" : "");
      el.dataset.sid = s.sid;
      const ago = relTime(s.started_at);
      el.innerHTML =
        `<span class=goal>${esc(s.goal || "(no goal)")}</span>` +
        `<span class=meta>${ago}<span class="pill ${esc(s.status)}">${esc(s.status)}</span></span>`;
      el.onclick = () => openSession(s.sid);
      div.appendChild(el);
    }
  } catch {}
}

function relTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s/60) + "m ago";
  if (s < 86400) return Math.floor(s/3600) + "h ago";
  return Math.floor(s/86400) + "d ago";
}

// ---------- transcript ----------
function clearTranscript() {
  $("transcript").innerHTML = "";
  $("qa").style.display = "none";
  tokens = {prompt:0, completion:0};
  stepCount = 0;
  updateMonitors();
}

function shortArgs(args) {
  if (!args) return "";
  const s = JSON.stringify(args);
  return s.length > 60 ? s.slice(0, 57) + "…" : s;
}
function shortObs(obs) {
  if (obs == null) return "";
  const s = String(obs);
  return s.length > 60 ? s.slice(0, 57) + "…" : s;
}

function removeThinking() {
  document.querySelectorAll(".card.thinking").forEach(el => el.remove());
}

function removeQueued() {
  document.querySelectorAll(".card.queued").forEach(el => el.remove());
}

function renderEvent(ev) {
  const t = $("transcript");
  if (ev.type === "queued") {
    const ahead = ev.payload && ev.payload.ahead;
    const el = document.createElement("div");
    el.className = "card queued";
    el.innerHTML = `<div class=summary>⏳ Queued — ${ahead ?? "?"} ahead. Server is at capacity (5 concurrent sessions); waiting for a slot…</div>`;
    t.appendChild(el);
    $("run-status").textContent = "● queued";
  } else if (ev.type === "session_started") {
    removeQueued();
    const el = document.createElement("div");
    el.className = "card expanded goal";
    el.innerHTML = `<div class=summary><b>Goal:</b> ${esc(ev.payload.goal)}</div>`;
    t.appendChild(el);
    $("run-status").textContent = "● running";
  } else if (ev.type === "step") {
    removeThinking();
    const p = ev.payload;
    const summary = `▸ ${esc(p.action)}(${esc(shortArgs(p.args))}) → ${esc(shortObs(p.obs))}`;
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML =
      `<div class=summary>${summary}</div>` +
      `<div class=detail>` +
      (p.reason ? `<div class=reason>${esc(p.reason)}</div>` : "") +
      `<pre>${esc(JSON.stringify(p.args, null, 2))}</pre>` +
      `<pre>${esc(p.obs ?? "")}</pre>` +
      `</div>`;
    el.onclick = () => el.classList.toggle("expanded");
    t.appendChild(el);
    stepCount++;
    updateMonitors();
  } else if (ev.type === "llm_call_start") {
    if (!document.querySelector(".card.thinking")) {
      const el = document.createElement("div");
      el.className = "card thinking";
      el.innerHTML = `<div class=summary>thinking</div>`;
      t.appendChild(el);
    }
  } else if (ev.type === "usage") {
    tokens.prompt += ev.payload.prompt_tokens || 0;
    tokens.completion += ev.payload.completion_tokens || 0;
    updateMonitors();
  } else if (ev.type === "question") {
    $("qa-question").textContent = ev.payload.question;
    $("qa").style.display = "block";
    $("qa-send").onclick = () => {
      if (activeWS) activeWS.send(JSON.stringify({type:"answer", text: $("qa-answer").value}));
      $("qa").style.display = "none";
      $("qa-answer").value = "";
    };
  } else if (ev.type === "done") {
    removeThinking();
    const el = document.createElement("div");
    el.className = "card expanded done";
    el.innerHTML = `<div class=summary><span class="pill ${esc(ev.payload.status)}">${esc(ev.payload.status)}</span> ${esc(ev.payload.answer)}</div>`;
    t.appendChild(el);
    finishRun(ev.payload.status);
  }
  t.scrollTop = t.scrollHeight;
}

// ---------- run / WS ----------
$("input").addEventListener("submit", e => {
  e.preventDefault();
  const goal = $("goal").value.trim();
  if (!goal) return;
  startRun(goal);
});

function startRun(goal) {
  if (activeWS) try { activeWS.close(); } catch {}
  clearTranscript();
  $("run").disabled = true;
  $("run-status").textContent = "● running";
  const ws = new WebSocket(wsUrl("/ws"));
  activeWS = ws;
  ws.addEventListener("open", () => ws.send(JSON.stringify({type:"goal", goal})));
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "session_started") {
      activeSid = m.payload.sid;
      history.pushState({sid: activeSid}, "", "/?s=" + activeSid);
      refreshSessions();
    }
    renderEvent(m);
  });
  ws.addEventListener("close", () => { if (activeWS === ws) finishRun(null); });
}

function finishRun(status) {
  $("run").disabled = false;
  $("run-status").textContent = status ? "● " + status : "";
  refreshSessions();
}

// ---------- session swap ----------
async function openSession(sid) {
  if (sid === activeSid && !activeWS) return;
  if (activeWS) try { activeWS.close(); } catch {}
  activeWS = null;
  activeSid = sid;
  history.pushState({sid}, "", "/?s=" + sid);
  clearTranscript();
  const r = await authFetch("/api/trace/" + sid);
  if (!r.ok) {
    $("transcript").innerHTML = `<div class=card>Session ${esc(sid)} not found.</div>`;
    return;
  }
  const evs = await r.json();
  for (const ev of evs) renderEvent(ev);
  refreshSessions();
}

$("newchat").onclick = () => {
  if (activeWS) try { activeWS.close(); } catch {}
  activeWS = null;
  activeSid = null;
  history.pushState({}, "", "/");
  clearTranscript();
  $("goal").value = "";
  $("goal").focus();
  $("run-status").textContent = "";
  refreshSessions();
};

$("collapse").onclick = () => $("layout").classList.toggle("collapsed");

window.addEventListener("popstate", () => {
  const u = new URL(location.href);
  const sid = u.searchParams.get("s");
  if (sid) openSession(sid);
  else $("newchat").click();
});

// ---------- monitors ----------
function updateMonitors() {
  $("tok-prompt").textContent = tokens.prompt;
  $("tok-completion").textContent = tokens.completion;
  $("step-count").textContent = stepCount;
}

async function pollHealth() {
  try {
    const r = await authFetch("/api/llm_health");
    const d = await r.json();
    $("led-server").className = "led up";
    $("led-llm").className = "led " + d.llm;
    $("llm-latency").textContent = d.latency_ms != null ? `(${d.latency_ms}ms)` : "";
  } catch {
    $("led-server").className = "led down";
    $("led-llm").className = "led down";
  }
}

// ---------- boot ----------
let healthTimer = null, sessionsTimer = null;
function boot() {
  if (healthTimer) clearInterval(healthTimer);
  if (sessionsTimer) clearInterval(sessionsTimer);
  pollHealth();
  refreshSessions();
  healthTimer = setInterval(pollHealth, 5000);
  sessionsTimer = setInterval(refreshSessions, 5000);
  const initialSid = new URL(location.href).searchParams.get("s");
  if (initialSid) openSession(initialSid);
}

(async () => {
  let required = false;
  try {
    const r = await fetch("/api/auth_required");
    if (r.ok) required = (await r.json()).required === true;
  } catch {}
  if (!required) { boot(); return; }
  if (authToken && await tryToken(authToken)) { boot(); return; }
  authToken = "";
  localStorage.removeItem(TOKEN_KEY);
  showGate();
})();
