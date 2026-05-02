const $ = id => document.getElementById(id);
$("goal-form").addEventListener("submit", e => {
  e.preventDefault();
  $("trace").innerHTML = ""; $("result").innerHTML = ""; $("qa").style.display = "none";
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.addEventListener("open", () => ws.send(JSON.stringify({type:"goal", goal: $("goal").value})));
  ws.addEventListener("message", ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "step") {
      const p = m.payload;
      const el = document.createElement("div"); el.className = "card";
      el.innerHTML = `<div class=thought>${escapeHtml(p.thought||"")}</div>
                      <div class=action>${escapeHtml(p.action)}(${escapeHtml(JSON.stringify(p.args))})</div>
                      <div class=obs>${escapeHtml(p.obs||"")}</div>`;
      $("trace").appendChild(el);
    } else if (m.type === "question") {
      $("qa-question").innerText = m.payload.question;
      $("qa").style.display = "block";
      $("qa-send").onclick = () => { ws.send(JSON.stringify({type:"answer", text: $("qa-answer").value})); $("qa").style.display="none"; $("qa-answer").value=""; };
    } else if (m.type === "done") {
      $("result").innerHTML = `<h2>${escapeHtml(m.payload.status)}</h2><p>${escapeHtml(m.payload.answer)}</p>`;
    }
  });
});
function escapeHtml(s){return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
