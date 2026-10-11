/* PR Council UI (G5)
 *
 * Every message here is a real `Message` from contracts/schema.py, the same shape the
 * server streams over WebSocket. Every review runs against the configured model.
 *
 *   Message: { id, review_id, round, agent, kind, position, confidence, claims, references, text, ts }
 *   - kind "diff"    (round 0): text is the raw diff
 *   - kind "verdict" (last)   : text is a TradeoffMap serialized as JSON
 */

/* ---------- Config ---------- */
// Served by the FastAPI server, so same-origin. If this page is opened straight from
// disk (file://), fall back to the default local server.
const SAME_ORIGIN = location.protocol === "http:" || location.protocol === "https:";
const HTTP_BASE = SAME_ORIGIN ? "" : "http://localhost:8000";
const WS_BASE = SAME_ORIGIN
  ? (location.protocol === "https:" ? "wss://" : "ws://") + location.host
  : "ws://localhost:8000";
const ROUNDS = [1, 2, 3];

/* Agent keys must match AgentName in contracts/schema.py exactly. */
const AGENTS = {
  security:      { name: "Security",      c: "var(--sec)" },
  performance:   { name: "Performance",   c: "var(--perf)" },
  compatibility: { name: "Compatibility", c: "var(--compat)" },
  quality:       { name: "Quality",       c: "var(--qual)" },
  product:       { name: "Product",       c: "var(--prod)" },
};
/* Position values must match Position in contracts/schema.py. */
const SYM = { approve: "✓", approve_with_changes: "~", block: "✗", escalate: "!" };
const POS_LABEL = {
  approve: "approve",
  approve_with_changes: "approve with changes",
  block: "block",
  escalate: "escalate",
};
const STANCE_LABEL = { agree: "agrees with", challenge: "challenges", compromise: "compromises with" };

const $ = id => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

/* ---------- State ---------- */
let runId = 0;          // bumped on every reset; late callbacks from an old run are ignored
let socket = null;
let lastRound = null;
let lastPosition = {};  // agent -> most recent position (to detect a changed mind)
let messageCount = 0;
let sawVerdict = false;
let debateBusy = false;

/* ---------- Rendering ---------- */
function renderDiff(text) {
  const box = $("diff");
  box.textContent = "";
  text.split("\n").forEach(l => {
    const d = el("span", "ln", l || " ");
    const c = l[0];
    const kind = l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff ") || l.startsWith("index ")
      ? "meta"
      : c === "+" ? "add" : c === "-" ? "del" : c === "@" ? "hunk" : "";
    if (kind) d.classList.add(kind);
    box.appendChild(d);
  });
}

function buildTracker() {
  const t = $("tracker");
  t.textContent = "";
  const head = el("tr");
  head.appendChild(el("th"));
  ROUNDS.forEach(r => head.appendChild(el("th", "", "R" + r)));
  t.appendChild(head);
  Object.entries(AGENTS).forEach(([k, a]) => {
    const tr = el("tr");
    tr.style.setProperty("--a", a.c);
    tr.appendChild(el("td", "name", a.name));
    ROUNDS.forEach(r => {
      const td = el("td", "cell", "·");
      td.id = `c-${k}-${r}`;
      td.style.setProperty("--a", a.c);
      tr.appendChild(td);
    });
    t.appendChild(tr);
  });
}

function addMessage(m) {
  const a = AGENTS[m.agent];
  if (!a) return;
  const ch = $("channel");

  if (m.round !== lastRound) {
    lastRound = m.round;
    ch.appendChild(el("div", "round", "Round " + m.round));
  }

  const card = el("article", "card");
  card.style.setProperty("--a", a.c);

  const top = el("div", "top");
  top.appendChild(el("span", "who", a.name));
  if (m.position) {
    top.appendChild(el("span", "badge", `${SYM[m.position] || ""} ${POS_LABEL[m.position] || m.position}`));
  }
  if (typeof m.confidence === "number") {
    const conf = el("span", "conf");
    const bar = el("span", "bar");
    const fill = document.createElement("i");
    fill.style.width = Math.round(m.confidence * 100) + "%";
    bar.appendChild(fill);
    conf.appendChild(bar);
    conf.appendChild(document.createTextNode(m.confidence.toFixed(2)));
    top.appendChild(conf);
  }
  card.appendChild(top);

  // "↳ challenges Security": the most important visual in the demo.
  // Colored in the *responding* agent's color (the card's --a); the target's name gets its own color.
  (m.references || []).forEach(r => {
    const target = AGENTS[r.agent];
    if (!target) return;
    const p = el("div", `ref ref-${r.stance}`);
    p.appendChild(document.createTextNode(`↳ ${STANCE_LABEL[r.stance] || r.stance} `));
    const who = el("b", "", target.name);
    who.style.color = target.c;
    p.appendChild(who);
    card.appendChild(p);
  });

  card.appendChild(el("div", "text", m.text));

  (m.claims || []).forEach(c => {
    const loc = c.file + (c.line != null ? ":" + c.line : "");
    const row = el("div", "claim");
    row.appendChild(el("span", `sev sev-${c.severity}`, c.severity));
    row.appendChild(el("code", "", loc));
    row.appendChild(document.createTextNode(" " + c.concern));
    card.appendChild(row);
  });

  ch.appendChild(card);
  ch.scrollTop = ch.scrollHeight;

  // Position tracker. A cell is "changed" when this agent's position differs from the last
  // one it stated, even if it skipped a round in between.
  const cell = $(`c-${m.agent}-${m.round}`);
  if (cell && m.position) {
    cell.textContent = SYM[m.position] || "?";
    cell.title = POS_LABEL[m.position] || m.position;
    const prev = lastPosition[m.agent];
    if (prev && prev !== m.position) {
      cell.classList.add("changed");
      cell.title += ` (was ${POS_LABEL[prev] || prev})`;
    }
  }
  if (m.position) lastPosition[m.agent] = m.position;
}

function parseMap(text) {
  try {
    const t = JSON.parse(text);
    return t && typeof t === "object" ? t : null;
  } catch (e) {
    return null;
  }
}

function addVerdict(m) {
  sawVerdict = true;
  const map = $("map");
  const t = parseMap(m.text);
  map.textContent = "";

  if (!t) {   // verdict text wasn't valid TradeoffMap JSON; show it raw rather than hide it
    map.className = "escalate";
    map.appendChild(el("h2", "", "Tradeoff map"));
    map.appendChild(el("div", "row", m.text));
    return;
  }

  const esc = t.outcome === "escalate";
  const positions = Object.values(t.final_positions || {});
  const agreement = !esc && positions.length === Object.keys(AGENTS).length && positions.every(position => position === positions[0]) ? positions[0] : null;
  map.className = esc ? 'escalate' : agreement === 'block' ? 'blocked' : 'consensus';
  map.appendChild(el('h2', '', esc ? 'Human review needed' : agreement === 'block' ? 'Council agrees: block this PR'
    : agreement === 'approve' ? 'Council agrees: approve' : agreement === 'approve_with_changes' ? 'Council agrees: changes required' : 'Review needs attention'));
  if (!esc && !agreement) {
    map.className = 'escalate';
    map.appendChild(el('div', 'row', 'The council did not record a complete agreement. Review the transcript before deciding.'));
  }

  const add = (label, items) => {
    if (!items || !items.length) return;
    const r = el("div", "row");
    r.appendChild(el("b", "", label));
    const ul = el("ul");
    items.forEach(i => ul.appendChild(el("li", "", i)));
    r.appendChild(ul);
    map.appendChild(r);
  };
  add("Resolved", t.resolved);
  add("Unresolved", t.unresolved);
  (t.human_decisions || []).forEach(q => map.appendChild(el("div", "ask", "Human decides: " + q)));

  map.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function handle(m) {
  messageCount++;
  if (m.kind === "diff") renderDiff(m.text);
  else if (m.kind === "verdict") addVerdict(m);
  else addMessage(m);
}

function setBusy(busy) {
  debateBusy = busy;
  $("run").disabled = busy;
  if (typeof updateGitHubControls === "function") updateGitHubControls();
  if (typeof lockConnections === "function") lockConnections();
}

function finish(text) {
  setBusy(false);
  $("status").textContent = text;
}

function reset() {
  runId++;
  if (socket) { socket.onmessage = socket.onclose = socket.onerror = null; socket.close(); socket = null; }
  lastRound = null;
  lastPosition = {};
  messageCount = 0;
  sawVerdict = false;
  $("channel").textContent = "";
  $("map").className = "";
  $("map").innerHTML = '<h2>Tradeoff map</h2><div class="row" style="color:var(--mute)">Waiting for the council\'s verdict.</div>';
  buildTracker();
  return runId;
}

/* ---------- Stream a real review ---------- */
function stream(url, id, { onFail }) {
  const ws = new WebSocket(url);
  socket = ws;
  ws.onmessage = e => {
    if (id !== runId) return;
    let m;
    try { m = JSON.parse(e.data); } catch (err) { return; }
    handle(m);
    $("status").textContent = m.kind === "verdict"
      ? "Verdict reached."
      : `Debate in progress: ${messageCount} message${messageCount === 1 ? "" : "s"}`;
  };
  ws.onerror = () => { /* onclose always follows; handle it there */ };
  ws.onclose = event => {
    if (id !== runId) return;
    socket = null;
    if (messageCount === 0) return event.reason ? finish(event.reason) : onFail();
    finish(sawVerdict ? "Verdict reached." : event.reason || "The review ended before a verdict arrived. Check the model connection.");
  };
}

/* ---------- Live ---------- */
async function runLive(diff) {
  if (typeof modelHasChanges === 'function' && modelHasChanges()) {
    $('connection-settings').open = true;
    $('status').textContent = 'Save your model changes before starting a review.';
    return;
  }
  const id = reset();
  setBusy(true);
  renderDiff(diff);
  $("status").textContent = "Contacting the council…";
  try {
    const { review_id } = await requestJSON('/reviews', { diff });
    if (id !== runId) return;
    $("status").textContent = "Live debate in progress.";
    // The server replays history to late sockets, so connecting after POST loses nothing.
    stream(WS_BASE + "/ws/" + review_id, id, {
      onFail: () => finish("The council produced no messages. Check the server log."),
    });
  } catch (err) {
    if (id !== runId) return;
    finish(err.message);
  }
}

/* ---------- Wiring ---------- */
$("run").onclick = () => {
  const d = $("input").value.trim();
  if (!d) {
    $("status").textContent = "Paste a diff first, or select a GitHub PR above.";
    $("input").focus();
    return;
  }
  runLive(d);
};
buildTracker();
