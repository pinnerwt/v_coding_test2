const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let activeWS = null;
let activeSid = null;
let tokens = {prompt:0, completion:0};
let stepCount = 0;
const STEP_MAX = 50;
$("step-max").textContent = STEP_MAX;

// ---------- sidebar ----------
async function refreshSessions() {
  try {
    const r = await fetch("/api/sessions");
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

function renderEvent(ev) {
  const t = $("transcript");
  if (ev.type === "session_started") {
    const el = document.createElement("div");
    el.className = "card expanded";
    el.innerHTML = `<div class=summary><b>Goal:</b> ${esc(ev.payload.goal)}</div>`;
    t.appendChild(el);
  } else if (ev.type === "step") {
    removeThinking();
    const p = ev.payload;
    const summary = `▸ ${esc(p.action)}(${esc(shortArgs(p.args))}) → ${esc(shortObs(p.obs))}`;
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML =
      `<div class=summary>${summary}</div>` +
      `<div class=detail>` +
      (p.thought ? `<div class=thought>${esc(p.thought)}</div>` : "") +
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
    el.className = "card expanded";
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
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
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
  const r = await fetch("/api/trace/" + sid);
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

$("collapse").onclick = () => $("sidebar").classList.toggle("collapsed");

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
    const r = await fetch("/api/llm_health");
    const d = await r.json();
    $("led-server").className = "led up";
    $("led-llm").className = "led " + d.llm;
    $("llm-latency").textContent = d.latency_ms != null ? `(${d.latency_ms}ms)` : "";
  } catch {
    $("led-server").className = "led down";
    $("led-llm").className = "led down";
  }
}

setInterval(pollHealth, 5000);
pollHealth();

// ---------- boot ----------
const initialSid = new URL(location.href).searchParams.get("s");
if (initialSid) openSession(initialSid);
refreshSessions();
setInterval(refreshSessions, 5000);
