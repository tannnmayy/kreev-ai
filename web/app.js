// Ala's screen. Plain modules, no build step: the same files run from Docker, Vercel or a laptop.
// The screen holds no shopping logic. It renders what the agent publishes, so what you see is what Ala believes.
import { Room, RoomEvent, Track } from "./vendor/livekit-client.esm.mjs";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const money = (n) => "₹" + Number(n).toLocaleString("en-IN");

let room = null;
let lang = "en";
let interimBubble = null;
let lastAla = null;
let view = null;
let cards = [];
let micOn = true;
let textOnly = false;
let wakeTimer = null;
let addedTimer = null;

// ---- start ---------------------------------------------------------------------------------------------------
function sessionKey() {
  let key = localStorage.getItem("ala.session");
  if (!key) {
    key = crypto.randomUUID().replaceAll("-", "").slice(0, 20);
    localStorage.setItem("ala.session", key);
  }
  return key;
}

function showError(message) {
  const el = $("start-error");
  el.textContent = message;
  el.hidden = false;
}

async function wakeEarly() {
  // the agent may be asleep on the free plan: start waking it while the customer reads this page
  try { await fetch("api/warm", { method: "POST" }); } catch { /* the call will wake it anyway */ }
}

async function checkInvite() {
  try {
    const health = await (await fetch("api/health")).json();
    if (health.invite_required && !params.get("invite")) $("invite-field").hidden = false;
  } catch { /* the token call will report any real problem */ }
}

async function start(withMic) {
  $("start-error").hidden = true;
  $("talk").disabled = true;
  textOnly = !withMic;
  const name = $("name").value.trim();
  if (name) localStorage.setItem("ala.name", name);
  const query = new URLSearchParams({ session: sessionKey(), name, invite: params.get("invite") || $("invite").value.trim() });
  let details;
  try {
    const response = await fetch("api/token?" + query);
    if (!response.ok) throw new Error(await response.text());
    details = await response.json();
  } catch (error) {
    $("talk").disabled = false;
    $("invite-field").hidden = !String(error.message).includes("invite");
    showError(String(error.message).slice(0, 200) || "Ala can't be reached right now. Please try again.");
    return;
  }
  room = new Room({ adaptiveStream: true, dynacast: true,
    audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  room.on(RoomEvent.TrackSubscribed, (track) => { if (track.kind === Track.Kind.Audio) track.attach($("voice")); });
  room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
    if (topic !== "ala") return;
    try { receive(JSON.parse(new TextDecoder().decode(payload))); } catch (error) { console.warn("bad message", error); }
  });
  room.on(RoomEvent.ParticipantConnected, (p) => { if (p.isAgent) agentArrived(); });
  room.on(RoomEvent.Disconnected, () => setOrb("idle", "call ended"));
  room.on(RoomEvent.Reconnecting, () => setOrb("idle", "reconnecting"));
  room.on(RoomEvent.Reconnected, () => setOrb("listening", "listening"));
  try {
    await room.connect(details.url, details.token);
    await room.startAudio();
    if (withMic) await room.localParticipant.setMicrophoneEnabled(true);
  } catch (error) {
    $("talk").disabled = false;
    showError("Couldn't start the call: " + (error.message || error).toString().slice(0, 160));
    return;
  }
  $("start").hidden = true;
  $("shop").hidden = false;
  setOrb("idle", "connecting");
  if ([...room.remoteParticipants.values()].some((p) => p.isAgent)) agentArrived();
  else wakeTimer = setTimeout(() => { $("wake").hidden = false; }, 1500);
}

function agentArrived() {
  clearTimeout(wakeTimer);
  $("wake").hidden = true;
}

// ---- what the agent publishes --------------------------------------------------------------------------------
function receive(msg) {
  switch (msg.kind) {
    case "ready": agentArrived(); $("hint").textContent = msg.text_only ? "Listening isn't set up here, so type to Ala." : "Talk over Ala any time to interrupt."; break;
    case "caption": caption(msg); break;
    case "heard": markHeard(msg.text); break;
    case "state": renderState(msg); break;
    case "agent_state": agentState(msg.state); break;
    case "hud": hud(msg); break;
    case "decision": note("decisions", `<b>${escape(msg.decision)}</b> ${escape(msg.reason)}: “${escape(msg.text)}”`); break;
    case "tone": $("s-tone").textContent = msg.label || "calm"; note("decisions", `<b>tone</b> ${escape(msg.label)}: ${escape(msg.cues)}`); break;
    case "turn":
      $("s-end").textContent = msg.p_end == null ? "–" : Math.round(msg.p_end * 100) + "%";
      $("s-hold").textContent = msg.hold_s.toFixed(1) + " s";
      if (msg.reasons && msg.reasons.length) note("decisions", `<b>wait ${msg.hold_s.toFixed(1)} s</b> ${escape(msg.reasons.join("; "))}`);
      break;
    case "brain":
      if (msg.event === "route") note("routes", `<b>${escape(msg.where)}</b> ${escape(msg.why || "")}${msg.seconds ? " · " + msg.seconds + " s" : ""}`);
      else if (msg.event === "tool") note("routes", `${msg.ok ? "✓" : "✗"} ${escape(msg.name)}${msg.ok ? "" : " (" + escape(msg.code) + ")"}`);
      else if (msg.event === "minds_busy") note("routes", "<b>quota</b> falling back to code");
      break;
    case "flagged": $("flag").textContent = "Saved"; setTimeout(() => { $("flag").textContent = "Flag this moment"; }, 1500); break;
  }
}

function escape(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function note(list, html) {
  const li = document.createElement("li");
  li.innerHTML = html;
  const el = $(list);
  el.prepend(li);
  while (el.children.length > 30) el.lastChild.remove();
}

function bubble(who, text, interim = false) {
  const div = document.createElement("div");
  div.className = `bubble ${who}` + (interim ? " interim" : "");
  div.textContent = text;
  $("captions").append(div);
  $("captions").scrollTop = $("captions").scrollHeight;
  return div;
}

function caption(msg) {
  if (msg.who === "customer") {
    if (msg.final) {
      if (interimBubble) { interimBubble.textContent = msg.text; interimBubble.classList.remove("interim"); interimBubble = null; }
      else bubble("customer", msg.text);
    } else if (interimBubble) {
      interimBubble.textContent = msg.text;
    } else {
      interimBubble = bubble("customer", msg.text, true);
    }
    return;
  }
  if (msg.lang) setLanguage(msg.lang);
  lastAla = bubble("ala", msg.text);
  lastAla.dataset.full = msg.text;
}

function markHeard(heard) {
  // when you cut Ala off, show how much of the sentence you actually heard
  if (!lastAla || !heard) return;
  const full = lastAla.dataset.full || "";
  if (!full.startsWith(heard)) return;
  lastAla.textContent = heard;
  const rest = document.createElement("span");
  rest.className = "unheard";
  rest.textContent = full.slice(heard.length);
  lastAla.append(rest);
}

function setLanguage(next) {
  lang = next;
  $("lang").textContent = next === "te" ? "తెలుగు" : "English";
  if (view) renderState(view);
}

function name(item) { return lang === "te" ? item.te : item.en; }

function renderState(state) {
  const previous = view;
  view = state;
  const lines = $("cart-lines");
  lines.replaceChildren(...state.cart.map((x) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escape(name(x))}</span><strong>${money(x.cost)}</strong><span class="sub">${escape(x.qty_text)}${x.cut ? " · " + escape(x.cut) : ""}</span>`;
    return li;
  }));
  $("cart-empty").hidden = state.cart.length > 0;
  $("total").textContent = state.total_text;
  $("review").disabled = state.cart.length === 0;

  const budget = $("budget");
  budget.hidden = state.budget == null;
  if (state.budget != null) {
    const used = state.total / state.budget;
    $("budget-fill").style.width = Math.min(100, used * 100) + "%";
    budget.classList.toggle("over", used > 1);
    budget.classList.toggle("near", used > 0.9 && used <= 1);
    const left = state.budget - state.total;
    $("budget-text").textContent = left >= 0 ? `${money(left)} left of ${money(state.budget)}` : `${money(-left)} over ${money(state.budget)}`;
  }

  const inCart = new Set(state.cart.map((x) => x.sku));
  const seller = $("seller").checked;
  $("cards").replaceChildren(...state.shown.map((p) => {
    const div = document.createElement("div");
    div.className = "card" + (inCart.has(p.sku) ? " in-cart" : "") + (p.sold_out ? " sold-out" : "");
    const unit = { kg: "a kilo", litre: "a litre", dozen: "a dozen", pack: "a pack", bunch: "a bunch", piece: "each" }[p.unit] || "";
    div.innerHTML = `<span class="n">${p.n}</span><b>${escape(name(p))}</b><span>${escape(p.price_text)} ${unit}</span>` +
      `<span class="note">${escape(lang === "te" ? p.note_te : p.note_en)}</span>` +
      (p.sold_out ? `<span class="badge">Sold out</span>` : "") +
      (seller && !p.sold_out ? `<button class="sell" data-sku="${escape(p.sku)}">Mark sold out</button>` : "");
    return div;
  }));

  $("orders").replaceChildren(...state.orders.map((o) => {
    const li = document.createElement("li");
    li.className = o.status;
    li.textContent = `Order ${o.id} · ${money(o.total)}` + (o.status === "cancelled" ? " · cancelled" : " · placed");
    return li;
  }));

  if (previous) {
    const had = new Set(previous.cart.map((x) => x.sku));
    const fresh = state.cart.find((x) => !had.has(x.sku));
    if (fresh) flash("Added: " + name(fresh));
  }
  const sheet = $("sheet");
  if (state.review) {
    $("sheet-text").textContent = lang === "te" ? state.review.te : state.review.en;
    if (!sheet.open) sheet.showModal();
  } else if (sheet.open) {
    sheet.close();
  }
}

function flash(text) {
  const el = $("added");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(addedTimer);
  addedTimer = setTimeout(() => { el.hidden = true; }, 2200);
}

function setOrb(state, label) {
  $("orb").className = "orb " + state + (micOn || textOnly ? "" : " muted");
  $("orb-label").textContent = label;
}

function agentState(state) {
  agentArrived();
  const labels = { listening: "listening", thinking: "thinking", speaking: "speaking", idle: "listening", initializing: "starting" };
  setOrb(state === "idle" ? "listening" : state, micOn || textOnly ? labels[state] || state : "muted");
}

function hud(msg) {
  const pct = Math.max(0, Math.min(100, (msg.level + 60) * 100 / 50));
  $("meter-fill").style.width = pct + "%";
  $("s-level").textContent = msg.level + " dB";
  $("s-pitch").textContent = msg.pitch ? msg.pitch + " Hz" : "–";
  $("orb-ring").style.transform = `scale(${1 + pct / 900})`;
}

// ---- what the customer does ----------------------------------------------------------------------------------
function send(msg) {
  if (!room) return;
  room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(msg)), { reliable: true, topic: "ala.client" })
    .catch((error) => console.warn("could not send", error));
}

function sayText(text) { send({ kind: "text", text }); }

$("talk").addEventListener("click", () => start(true));
$("type-only").addEventListener("click", () => start(false));
$("typed").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("typed-text");
  if (input.value.trim()) sayText(input.value.trim());
  input.value = "";
});
$("orb").addEventListener("click", async () => {
  if (!room || textOnly) return;
  micOn = !micOn;
  await room.localParticipant.setMicrophoneEnabled(micOn);
  $("orb").setAttribute("aria-label", micOn ? "Microphone on. Tap to mute." : "Microphone off. Tap to unmute.");
  $("orb").classList.toggle("muted", !micOn);
  if (!micOn) $("orb-label").textContent = "muted";
});
$("review").addEventListener("click", () => sayText(lang === "te" ? "ఆర్డర్ పెట్టండి" : "place the order"));
$("sheet-yes").addEventListener("click", () => { $("sheet").close(); sayText(lang === "te" ? "అవును" : "yes"); });
$("sheet-no").addEventListener("click", () => { $("sheet").close(); sayText(lang === "te" ? "వద్దు" : "no"); });
$("panel-toggle").addEventListener("click", () => {
  const panel = $("panel");
  panel.hidden = !panel.hidden;
  $("panel-toggle").setAttribute("aria-pressed", String(!panel.hidden));
});
$("end").addEventListener("click", async () => { if (room) await room.disconnect(); location.reload(); });
$("seller").addEventListener("change", () => { if (view) renderState(view); });
$("cards").addEventListener("click", (event) => {
  const sku = event.target.dataset && event.target.dataset.sku;
  if (sku) send({ kind: "sold_out", sku });
});

function drawCard() {
  if (!cards.length) return;
  const card = cards[Math.floor(Math.random() * cards.length)];
  $("card-family").textContent = card.family;
  $("card-title").textContent = card.title;
  $("card-text").textContent = card.do;
  $("card-expect").textContent = "Ala should: " + card.expect;
}
$("test-card").addEventListener("click", async () => {
  if (!cards.length) { try { cards = await (await fetch("cards.json")).json(); } catch { cards = []; } }
  drawCard();
  $("card-dialog").showModal();
});
$("card-next").addEventListener("click", drawCard);
$("card-close").addEventListener("click", () => $("card-dialog").close());
$("flag").addEventListener("click", () => { $("flag-note").value = ""; $("flag-dialog").showModal(); });
$("flag-cancel").addEventListener("click", () => $("flag-dialog").close());
$("flag-send").addEventListener("click", () => { send({ kind: "flag", note: $("flag-note").value }); $("flag-dialog").close(); });

window.ala = { get room() { return room; }, send };     // for tests and debugging
$("name").value = localStorage.getItem("ala.name") || "";
wakeEarly();
checkInvite();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
