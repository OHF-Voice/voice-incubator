"use strict";

// Voice Call web UI. No build step: plain DOM, relative URLs (ingress).

const $main = document.getElementById("main");
const state = {
  status: null,
  store: null,
  satellites: [],
  catalog: {},
  pollers: [],
  leaving: [],
};

// ------------------------------------------------------------------ helpers

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else if (key === "checked") el.checked = !!value;
    else if (key === "html") el.innerHTML = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// Icons: a command's own (icon: mdi:…), else one for what it does. ICONS is
// the subset of Material Design Icons in icons.js (scripts/make_icons.py).
const KIND_ICONS = { respond: "mdi:message-text-outline", action: "mdi:flash-outline", script: "mdi:script-text-outline", scene: "mdi:palette", timer: "mdi:timer-outline", music: "mdi:music", nothing: "mdi:close-circle-outline" };

// Whether a response says something when done: a plain one, or done's.
const respondsWhenDone = (response) => Boolean(typeof response === "string" ? response
  : response && response.done && (typeof response.done === "string" || Object.values(response.done).some(Boolean)));

function kindOf(spec) {
  return spec.script ? "script" : spec.scene ? "scene" : spec.timer ? "timer" : spec.music ? "music" : spec.action ? "action"
    : respondsWhenDone(spec.response) ? "respond" : "nothing";
}

function iconFor(spec) {
  return spec && spec.icon && ICONS[spec.icon] ? spec.icon : KIND_ICONS[kindOf(spec || {})];
}

// An icon; `label`, when given, is what a screen reader says for it.
function icon(name, cls, label) {
  const path = ICONS[name] || ICONS["mdi:message-text-outline"];
  return h("span", { class: `icon ${cls || ""}`, title: label === undefined ? name : label || null, role: label ? "img" : null, "aria-label": label || null,
    html: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>` });
}

// replaceChildren() would print null and false as text.
function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(`api/${path}`, options);
  let data = null;
  let unreadable = false;
  try { data = await response.json(); } catch (_) { unreadable = true; }
  if (!response.ok || (data && data.ok === false)) {
    throw new Error((data && data.error) || `${response.status} ${response.statusText}`);
  }
  if (unreadable) throw new Error(`The app's answer to ${path.split("?")[0]} could not be read`);
  return data;
}

function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2500);
}

function errorBox(err) {
  return h("div", { class: "notice bad" }, String(err.message || err));
}

// More about something, for whoever wants it: a small "?" that shows it on
// hover, on focus, or when clicked or tapped -- and that a screen reader
// reads as its description.
let hints = 0;
function hint(text) {
  if (text === null || text === undefined || text === "") return null;
  const id = `hint-${++hints}`;
  const tip = h("span", { class: "hint-text", id, role: "tooltip", hidden: true }, text);
  const button = h("button", { type: "button", class: "hint", "aria-label": "More", "aria-describedby": id, "aria-expanded": "false" }, "?");
  // Near the right edge, it opens leftwards.
  const place = () => { tip.classList.toggle("flip", button.getBoundingClientRect().left > window.innerWidth / 2); };
  button.addEventListener("mouseenter", place);
  button.addEventListener("focus", place);
  button.addEventListener("click", (e) => {
    place();
    e.preventDefault();
    e.stopPropagation();
    const open = tip.hidden;
    for (const other of document.querySelectorAll(".hint-text.pinned")) { other.hidden = true; other.classList.remove("pinned"); }
    tip.hidden = !open;
    tip.classList.toggle("pinned", open);
    button.setAttribute("aria-expanded", String(open));
  });
  return h("span", { class: "hint-wrap" }, button, tip);
}
document.addEventListener("click", () => {
  for (const tip of document.querySelectorAll(".hint-text.pinned")) { tip.hidden = true; tip.classList.remove("pinned"); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") for (const tip of document.querySelectorAll(".hint-text.pinned")) { tip.hidden = true; tip.classList.remove("pinned"); }
});

// A labelled control; `help` goes in a hint beside the label.
let fields = 0;
function field(label, control, help) {
  const labelled = control instanceof HTMLElement && ["INPUT", "SELECT", "TEXTAREA"].includes(control.tagName);
  if (labelled && !control.id) control.id = `field-${++fields}`;
  return h("div", { class: "field" },
    h("div", { class: "label-row" }, h(labelled ? "label" : "span", { class: "label", for: labelled ? control.id : null }, label), hint(help)),
    control);
}

// A heading with its hint.
function heading(tag, text, help, attrs) {
  return h(tag, { ...(attrs || {}), class: `with-hint ${(attrs && attrs.class) || ""}` }, text, hint(help));
}

function time(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Numbers for people: 3 decimals at most, "—" for nothing.
function num(value, digits = 3) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "number" ? String(Number(value.toFixed(digits))) : String(value);
}

// A wake word model's probability, 0-1, as two decimals: "0.90".
function prob(value) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

function clone(value) { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)); }

function slug(text) {
  return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function asList(value) {
  if (value === undefined || value === null || value === "") return [];
  return Array.isArray(value) ? value : [value];
}

function stopPolling() {
  for (const id of state.pollers) clearInterval(id);
  state.pollers = [];
  // What a page started that must not outlive it (listening live).
  for (const fn of state.leaving.splice(0)) { try { fn(); } catch (_) { /* gone already */ } }
}

function poll(fn, ms) {
  // A restart or a dropped connection is not worth an error: try again next time.
  state.pollers.push(setInterval(async () => { try { await fn(); } catch (_) { /* retry */ } }, ms));
}

// Chips: a list of short phrases. Enter or comma adds; × removes.
// What each example template stands for -- its sentences, or what's wrong
// with it -- under the example sentences.
function examplesInfo(values) {
  const box = h("div", { class: "small examples-info" });
  async function show(list) {
    const templates = asList(list).map(String).filter((v) => /[()[\]|{}<>]/.test(v));
    if (!templates.length) { fill(box); return; }
    try {
      const data = await api("POST", "examples", { examples: templates });
      fill(box, data.examples.map((e) => (e.error
        ? h("div", { class: "notice bad" }, `“${e.example}”: ${e.error}`)
        : h("div", { class: "muted", title: e.sentences.join("\n") },
          `“${e.example}” is ${e.count} sentence${e.count === 1 ? "" : "s"}: `, e.sentences.slice(0, 3).join(" · "), e.count > 3 ? " …" : ""))));
    } catch (err) { fill(box, h("div", { class: "notice bad" }, err.message)); }
  }
  show(values);
  return { box, show };
}

function chipsInput(values, onChange, placeholder) {
  const list = asList(values).map(String);
  const wrap = h("div", { class: "chips" });
  const input = h("input", { type: "text", placeholder: placeholder || "type and press Enter" });
  function render() {
    fill(wrap, 
      ...list.map((v, i) => h("span", { class: "chip" }, v, h("button", { type: "button", title: "remove", onclick: () => { list.splice(i, 1); render(); onChange(list.slice()); } }, "×"))),
      input,
    );
  }
  function add() {
    for (const part of input.value.split(",")) {
      const v = part.trim();
      if (v && !list.includes(v)) list.push(v);
    }
    input.value = "";
    render();
    onChange(list.slice());
    input.focus();
  }
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
    else if (e.key === "Backspace" && !input.value && list.length) { list.pop(); render(); onChange(list.slice()); }
  });
  input.addEventListener("blur", () => { if (input.value.trim()) add(); });
  render();
  return wrap;
}

// Groups of alternatives: [[a, b], [c]]
function groupsEditor(groups, onChange, addLabel) {
  const data = asList(groups).map((g) => asList(g).map(String));
  const wrap = h("div");
  function emit() { onChange(data.filter((g) => g.length).map((g) => g.slice())); }
  function render() {
    fill(wrap, 
      ...data.map((group, i) => h("div", { class: "group" },
        chipsInput(group, (v) => { data[i] = v; emit(); }, "alternatives: turn on, switch on"),
        h("button", { type: "button", class: "link", onclick: () => { data.splice(i, 1); emit(); render(); } }, "remove"))),
      h("button", { type: "button", onclick: () => { data.push([]); render(); } }, addLabel),
    );
  }
  render();
  return wrap;
}

// Search Home Assistant (areas, floors, voice-domain entities).
async function catalog(kind, q, domain) {
  const params = new URLSearchParams({ kind, q: q || "" });
  if (domain) params.set("domain", domain);
  return (await api("GET", `hass/catalog?${params}`)).results;
}

const ENTITY_ID = /^[a-z0-9_]+\.[a-z0-9_]+$/;

// Search Home Assistant's areas, floors or entities. Entities are searched in
// the voice domains only, so any entity id may also be typed in full.
function hassPicker(kind, onPick, opts = {}) {
  const plural = { entity: "entities", area: "areas", floor: "floors" }[kind] || `${kind}s`;
  const hint = kind === "entity" ? `Search Home Assistant ${opts.domain ? `${opts.domain}s` : plural}, or type an entity id…` : `Search Home Assistant ${plural}…`;
  const input = h("input", { type: "search", placeholder: opts.placeholder || hint });
  const results = h("div", { class: "results hidden" });
  let timer = null;
  const pick = (item) => { results.classList.add("hidden"); input.value = ""; onPick(item); };
  // An entity id typed in full, in the picker's domain if it has one.
  const typedId = () => {
    const value = input.value.trim().toLowerCase();
    if (kind !== "entity" || !ENTITY_ID.test(value)) return null;
    return opts.domain && !value.startsWith(`${opts.domain}.`) ? null : value;
  };
  async function search() {
    const typed = typedId();
    const useTyped = (items) => (typed && !items.some((item) => item.id === typed) ? h("div", {
      class: "result",
      onmousedown: (e) => { e.preventDefault(); pick({ id: typed, name: "" }); },
    }, "Use ", h("code", {}, typed), h("span", { class: "muted small" }, " (not in the list)")) : null);
    try {
      const items = await catalog(kind, input.value, opts.domain);
      const extra = useTyped(items);
      fill(results, extra, ...(items.length ? items.map((item) => h("div", {
        class: "result",
        onmousedown: (e) => { e.preventDefault(); pick(item); },
      }, h("span", {}, item.name), " ", h("span", { class: "muted small" }, item.id, item.area ? ` · ${item.area}` : "", item.floor ? ` · ${item.floor}` : ""))) : extra ? [] : [h("div", { class: "result muted" }, kind === "entity" ? "Nothing found. Type the whole entity id to use it anyway." : "Nothing found (is Home Assistant connected?)")]));
      results.classList.remove("hidden");
    } catch (err) {
      fill(results, useTyped([]), h("div", { class: "result muted" }, String(err.message)));
      results.classList.remove("hidden");
    }
  }
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const typed = typedId();
    if (typed) pick({ id: typed, name: "" });
  });
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 200); });
  input.addEventListener("focus", search);
  input.addEventListener("blur", () => setTimeout(() => results.classList.add("hidden"), 150));
  return h("div", { class: "picker" }, input, results);
}

// Area/satellite multi-select for scopes.
function idChips(ids, labels, onRemove) {
  return h("div", { class: "chips" }, ...ids.map((id, i) => h("span", { class: "chip" }, labels[id] || id, h("button", { type: "button", onclick: () => onRemove(i) }, "×"))));
}

async function yamlDump(value) {
  return (await api("POST", "yaml/dump", { value })).text;
}

// ------------------------------------------------------------------ status

async function refreshStatus() {
  try {
    state.status = await api("GET", "status");
  } catch (err) {
    document.getElementById("status").replaceChildren(h("span", { class: "badge bad" }, "app unreachable"));
    return;
  }
  const s = state.status;
  const badges = [
    h("span", { class: `badge ${s.hass ? "ok" : "bad"}` }, s.hass ? "Home Assistant" : "Home Assistant offline"),
    h("span", { class: "badge" }, `${s.commands} command${s.commands === 1 ? "" : "s"}`),
  ];
  const debugging = Object.keys(s.debug_sessions || {});
  if (debugging.length) badges.push(h("a", { class: "badge warn", href: `#/satellite/${debugging[0]}` }, "debugging a satellite"));
  if (s.settings && s.settings.keep_audio) badges.push(h("span", { class: "badge warn" }, "keeping audio"));
  document.getElementById("status").replaceChildren(...badges);
  const banner = document.getElementById("banner");
  const notes = [];
  if (s.store_error) notes.push(h("div", { class: "notice bad" }, "commands.yaml has an error, so the previous commands are still in use: ", s.store_error));
  if (s.integration && s.integration !== "ready" && s.integration !== "connecting") notes.push(h("div", { class: "notice warn" }, s.integration));
  if (s.calendar && s.calendar !== "ready" && s.calendar !== "connecting") notes.push(h("div", { class: "notice warn" }, "Alarms and reminders won't work: ", s.calendar));
  if (s.unspeakable && s.unspeakable.length) notes.push(h("div", { class: "notice warn" }, "These phrases can't be spelled in the speech model's pieces and will never be heard: ", s.unspeakable.join(", ")));
  fill(banner, ...notes.map((n) => h("div", {}, n)));
}

async function loadStore() {
  state.store = await api("GET", "store");
  return state.store;
}

async function loadSatellites() {
  const data = await api("GET", "satellites");
  state.satellites = data.satellites;
  state.integration = data.integration;
  return state.satellites;
}

async function areaLabels() {
  if (!state.catalog.area) {
    try { state.catalog.area = await catalog("area", ""); } catch (_) { state.catalog.area = []; }
  }
  return Object.fromEntries(state.catalog.area.map((a) => [a.id, a.name]));
}

function satelliteLabels() {
  return Object.fromEntries(state.satellites.map((s) => [s.key, s.name]));
}

// ---------------------------------------------------------------- commands

function listensText(spec) {
  const parts = [];
  if (spec.examples && spec.examples.length) parts.push(`“${spec.examples[0]}”${spec.examples.length > 1 ? ` +${spec.examples.length - 1}` : ""}`);
  if (spec.require && spec.require.length) parts.push(asList(spec.require).map((g) => asList(g)[0]).join(" + "));
  return parts.join(" · ");
}

// What happens when nothing matched: the closest command by meaning, and
// GLiNER -- each only for the commands chosen for it.
// How much a command says when it's done (commands.VERBOSITY).
const LEVELS = [
  ["silent", "Silent: nothing, not even a beep"],
  ["beep", "Beep"],
  ["brief", "Brief: a fixed sentence"],
  ["specific", "Specific: what was heard"],
  ["verbose", "Verbose: everything it did"],
];
const levelName = (value) => ({ silent: "silent", beep: "beep", brief: "brief", specific: "specific", verbose: "verbose" }[value] || value);

// When a satellite stops listening: a voice activity detector, and how much
// speech and silence it takes. An empty box is the default shown in it.
const VAD_NAMES = { silero: "Silero VAD", micro: "microVAD" };

function endOfSpeechPanel() {
  const status = (state.status && state.status.end_of_speech) || {};
  const set = status.set || {};
  const defaults = status.defaults || {};
  const vad = h("select", {}, Object.entries(status.vads || {}).map(([id, v]) => h("option", { value: id, selected: (set.vad || defaults.vad) === id, disabled: !v.installed },
    `${v.name}${v.installed ? "" : " (not installed)"}${id === defaults.vad ? " (default)" : ""}`)));
  const number = (key, step, max) => defaultNumber(set[key], defaults[key], step, max);
  const inputs = {
    speech_threshold: number("speech_threshold", 0.05, 1),
    silence_threshold: number("silence_threshold", 0.05, 1),
    speech_seconds: number("speech_seconds", 0.05, 3),
    silence_seconds: number("silence_seconds", 0.1, 5),
    resume_seconds: number("resume_seconds", 0.05, 2),
    max_seconds: number("max_seconds", 1, 60),
  };
  async function save(body) {
    try { await api("PUT", "settings", { end_of_speech: body }); toast("Saved"); await refreshStatus(); pageSettings(); }
    catch (err) { toast(err.message); }
  }
  const others = h("input", { type: "checkbox", checked: (set.end_on_other_voice ?? defaults.end_on_other_voice) !== false });
  // On, with nobody to tell apart: it does nothing until someone is enrolled.
  const noVoices = () => !others.checked ? null
    : status.voices === null ? h("div", { class: "notice warn" }, "Speaker identification is turned off, so this does nothing.")
    : status.voices === 0 ? h("div", { class: "notice warn" }, "No voices are enrolled, so this does nothing yet: ", h("a", { href: "#/speakers" }, "enroll one on the Speakers page"), ".")
    : null;
  const note = h("div", {}, noVoices());
  others.addEventListener("change", () => fill(note, noVoices()));
  const values = () => ({
    vad: vad.value === defaults.vad ? null : vad.value,
    ...Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, numberOrNull(input)])),
    end_on_other_voice: others.checked === (defaults.end_on_other_voice !== false) ? null : others.checked,
  });
  return h("div", { class: "panel" },
    heading("h2", "End of speech", "When a satellite stops listening: once you have started speaking, after enough silence. A voice activity detector says how likely each moment of the audio is to be speech."),
    h("div", { class: "row" },
      field("Detector", vad, "Silero VAD judges 32 ms at a time and notices the end of speech soonest. microVAD, microWakeWord's, judges 10 ms at a time and is smoother, but notices the end about half a second later: give it a shorter silence. It says nothing for its first 0.76 s.")),
    h("div", { class: "row" },
      field("Speech at", inputs.speech_threshold, "Audio this likely to be speech, or more, is speech."),
      field("Silence below", inputs.silence_threshold, "Speech lasts until the likelihood falls below this; in between, nothing changes. Lower holds on through quiet words.")),
    h("div", { class: "row" },
      field("Speech to start (s)", inputs.speech_seconds, "Speech that starts the command. Until then, silence ends nothing: the satellite waits for you."),
      field("Silence to end (s)", inputs.silence_seconds, "Silence after speech that ends the command. Shorter answers sooner; longer lets you pause. The default is the app's option, “End-of-command silence”."),
      field("Speech to resume (s)", inputs.resume_seconds, "Speech in a row that starts the silence over. Shorter sounds — a cough, a click — only pause it."),
      field("Longest command (s)", inputs.max_seconds, "From the wake word: the satellite stops listening then, whatever it heard.")),
    h("label", {}, others, "End when someone else talks", hint("Once the speaker is identified by their voice, someone else starting to talk ends the command at the speaker's last word, about a second in, rather than when they stop.")),
    note,
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => save(values()) }, "Save"),
      h("button", { onclick: () => save(null) }, "Restore defaults")));
}

// What a command that beeps says when it acted out of sight.
function responsesPanel() {
  const verbosity = (state.status && state.status.verbosity) || {};
  const select = h("select", { onchange: async (e) => {
    try { await api("PUT", "settings", { beep_elsewhere: e.target.value }); toast("Saved"); await refreshStatus(); }
    catch (err) { toast(err.message); }
  } }, ...LEVELS.filter(([value]) => value !== "silent").map(([value, label]) => h("option", { value, selected: (verbosity.beep_elsewhere || "specific") === value }, value === "beep" ? "Beep anyway" : label)));
  return h("div", { class: "panel" },
    heading("h2", "Responses", "Each command says as much as its own level when it's done: a beep, a brief fixed sentence (“Turned on the lights.”), a specific one (“Turned on the lights in the Kitchen.”) or a verbose one (“Turned on Ceiling and Lamp in the Kitchen.”). Lights beep; timers, locks and lists say what they did. Set it on the command's page."),
    field("A beep out of sight says", select, "A beep is enough where you can see it happen: in the satellite's own area. Somewhere else — another area, a floor, a device in no area — a command that beeps says this instead. Home Assistant's own says the brief one."));
}

function fallbackPanel() {
  const status = (state.status && state.status.fallback) || {};
  const models = status.models || {};
  const meaning = h("input", { type: "checkbox", checked: status.meaning !== false });
  const llm = h("input", { type: "checkbox", checked: status.llm !== false });
  const model = h("select", {}, Object.entries(models).map(([id, m]) => h("option", { value: id, selected: status.model === id },
    `${m.name} (${m.mb} MB, ${m.licence})${m.installed === false ? " — not exported" : ""}`)));
  const topK = h("input", { type: "number", min: 1, max: 64, value: status.top_k || 8, style: "width:70px" });
  const threshold = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: status.threshold ?? 0.3, style: "width:80px" });
  const thresholdField = field("Threshold", threshold, "GLiNER's probability for the command it picks, which must also beat “not a command”. Higher refuses more.");
  const count = (n) => `${n || 0} command${n === 1 ? "" : "s"} chosen`;
  async function save() {
    try {
      await api("PUT", "settings", { fallback_meaning: meaning.checked, fallback_llm: llm.checked, fallback_model: model.value,
        fallback_top_k: Number(topK.value), fallback_threshold: Number(threshold.value) });
      toast("Saved"); await refreshStatus(); pageSettings();
    } catch (err) { toast(err.message); }
  }
  return h("div", { class: "panel" },
    h("div", { class: "row" }, heading("h2", "When nothing matches", "Keywords and example sentences come first. When they find nothing, these may run a command — only one chosen for it, here or on the command's page.", { class: "grow", style: "margin:0" }),
      h("button", { onclick: () => { location.hash = "#/fallback"; } }, "Choose commands")),
    h("label", {}, meaning, "By meaning", hint("Runs the command a sentence is very close to in meaning (“kill the lights in the office”); the model hears what was said in it, such as the room."),
      h("span", { class: "badge" }, count(status.meaning_commands)),
      status.meaning_available === false ? h("span", { class: "badge" }, "needs the embedding model") : null),
    h("label", {}, llm, "By model", hint("GLiNER picks a command and hears what it needs (“I'm in the mood for jazz”)."),
      h("span", { class: "badge" }, count(status.llm_commands)),
      h("span", { class: "badge" }, status.llm_status || "off")),
    h("div", { class: "row" },
      field("Model", model, "GLiNER hears what was said in the command meaning picked, and otherwise picks a command itself, or “not a command”, in about a quarter of a second on a Raspberry Pi 5. A command's versions (on this satellite, a device, an area or floor) are one choice to it: what it hears picks the version."),
      field("Choices", topK, "The closest in meaning. GLiNER is given commands that are not chosen too, to tell the chosen ones from, and runs only a chosen one."),
      thresholdField,
      h("button", { class: "primary", onclick: save }, "Save")));
}

// Which commands the steps after keywords and examples may run.
async function pageFallback() {
  const store = await loadStore();
  const errors = h("div");
  const filter = h("input", { type: "search", placeholder: "Filter commands", style: "max-width:260px" });
  const rows = Object.entries(store.commands || {}).map(([cid, spec]) => {
    const steps = asList(spec && spec.fallback);
    const needsSaid = !!(store.details[cid] || {}).needs_said;
    const meaning = h("input", { type: "checkbox", checked: steps.includes("meaning"),
      title: needsSaid ? "Meaning picks it; the model hears what was said in it (a name, number, value or text)" : "" });
    // GLiNER picks only commands that hear something: meaning takes the rest.
    const glinerSkips = !needsSaid;
    const llm = h("input", { type: "checkbox", checked: steps.includes("llm"), disabled: glinerSkips && !steps.includes("llm"),
      title: glinerSkips ? "It hears nothing: GLiNER leaves it to meaning" : "" });
    const row = h("div", { class: "item" }, icon(iconFor(spec || {}), "big"),
      h("div", { class: "grow" }, h("div", { class: "title" }, (spec && (spec.title || spec.description)) || cid),
        h("div", { class: "muted small" }, cid, needsSaid ? " · hears something" : "")),
      h("label", { title: meaning.title }, meaning, "meaning"), h("label", { title: llm.title }, llm, "model"));
    return { cid, spec, meaning, llm, row };
  });
  filter.addEventListener("input", () => {
    const text = filter.value.trim().toLowerCase();
    for (const r of rows) r.row.style.display = !text || r.cid.includes(text) || String((r.spec && (r.spec.title || r.spec.description)) || "").toLowerCase().includes(text) ? "" : "none";
  });
  const all = (step, on) => rows.forEach((r) => { if (r.row.style.display !== "none" && !r[step].disabled) r[step].checked = on; });
  async function save() {
    fill(errors);
    try {
      await api("PUT", "fallback", { meaning: rows.filter((r) => r.meaning.checked).map((r) => r.cid), llm: rows.filter((r) => r.llm.checked).map((r) => r.cid) });
      toast("Saved"); await refreshStatus(); pageFallback();
    } catch (err) { errors.append(errorBox(err)); }
  }
  fill($main,
    h("div", { class: "row" },
      h("a", { href: "#/settings" }, "← Settings"),
      h("h1", { class: "grow", style: "margin:0" }, "When nothing matches"),
      h("button", { class: "primary", onclick: save }, "Save")),
    errors,
    h("div", { class: "panel" },
      h("div", { class: "row" }, heading("h2", "Commands", "When keywords and example sentences find nothing, “meaning” may run a command a sentence is very close to (the model hears the names, numbers or text said in it), and “model” lets GLiNER pick it and hear what it needs (only a command that hears something). A command not chosen is never run by them.", { class: "grow", style: "margin:0" }), filter,
        h("button", { onclick: () => all("meaning", true) }, "All by meaning"), h("button", { onclick: () => all("llm", true) }, "All by model"),
        h("button", { onclick: () => { all("meaning", false); all("llm", false); } }, "None")),
      h("div", { class: "list" }, rows.map((r) => r.row))),
    h("div", { class: "row" }, h("span", { class: "grow" }), h("button", { class: "primary", onclick: save }, "Save")),
  );
}

// What nothing here understood: a Home Assistant conversation agent, in a
// conversation kept on each satellite.
function agentPanel() {
  const status = (state.status && state.status.agent) || {};
  const agent = h("select", { "aria-label": "Conversation agent" }, h("option", { value: "" }, "None"),
    status.agent ? h("option", { value: status.agent, selected: true }, status.agent) : null);
  const note = h("span");
  api("GET", "agents").then((data) => {
    fill(agent, h("option", { value: "", selected: !status.agent }, "None"),
      ...data.agents.map((a) => h("option", { value: a.id, selected: status.agent === a.id }, `${a.name} (${a.id})`)),
      status.agent && !data.agents.some((a) => a.id === status.agent) ? h("option", { value: status.agent, selected: true }, `${status.agent} (not found)`) : null);
    note.textContent = data.error ? `Home Assistant's agents could not be listed: ${data.error}` : "";
  }).catch(() => {});
  const keep = h("input", { type: "number", min: 0, max: 1440, step: 0.5, value: status.keep_minutes ?? 5, style: "width:80px" });
  const sats = satelliteLabels();
  const kept = Object.entries(status.conversations || {});
  async function save() {
    try {
      await api("PUT", "settings", { agent: agent.value || null, agent_keep_minutes: Number(keep.value) });
      toast("Saved"); await refreshStatus(); pageSettings();
    } catch (err) { toast(err.message); }
  }
  return h("div", { class: "panel" },
    heading("h2", "Conversation agent", ["What no command, skill or step under “When nothing matches” took goes to one of Home Assistant's conversation agents — its own, or an LLM's (OpenAI, Google, Ollama …) — and its answer is said. What it doesn't understand either is the Unknown command's. A dry run never asks it, since an agent can act in your home. An LLM agent hears what the speech model wrote, mishearings and all."]),
    h("div", { class: "row" },
      h("div", { class: "field" }, agent, note),
      field("Keep for", h("label", {}, keep, "minutes"), "After its last answer on a satellite, the next sentence there continues the conversation (“how tall is it?”), as on the Voice PE. 0: every sentence starts a new one."),
      h("button", { class: "primary", onclick: save }, "Save")),
    kept.length ? h("p", { class: "small" }, "Kept: ", kept.map(([key, seconds]) => `${sats[key] || key || "typed with no satellite"} (${Math.round(seconds)} s more)`).join(", ")) : null);
}

// The Unknown command: what is said when nothing -- no command, skill, the
// fallback, the conversation agent -- understood a sentence. A template, rendered by Home Assistant.
const UNKNOWN_EXAMPLES = [
  "Sorry, I couldn't understand the sentence {{ transcript }}.",
  "{% if hint %}{{ hint }}{% else %}Sorry, I didn't catch “{{ transcript }}”.{% endif %}",
  "{{ response }} {% if speaker.name %}Sorry, {{ speaker.name }}.{% endif %}",
];

async function pageUnknown() {
  const store = await loadStore();
  const area = h("textarea", { placeholder: "Sorry, I couldn't understand the sentence {{ transcript }}." });
  area.value = (store.unknown || {}).response || "";
  const errors = h("div");
  async function save() {
    fill(errors);
    try { await api("PUT", "unknown", { response: area.value }); toast("Saved"); refreshStatus(); }
    catch (err) { errors.append(errorBox(err)); }
  }
  const vars = [
    ["transcript", "what the speech model heard (Home Assistant's STT, or the built-in TDT)"],
    ["heard", "what the keyword matcher heard in the audio"],
    ["hint", "the near miss: “It looks like you're trying to turn on the lights, but I didn't hear which room.” Empty when nothing came close."],
    ["closest", "that command: closest.id, closest.description; none when nothing came close"],
    ["response", "what is said without this response: the hint, or “Sorry, I didn't understand that.”"],
    ["satellite", "id, name, area_id, area_name, floor_id, floor_name, device_id"],
    ["speaker", "name (none when not recognized) and score"],
    ["place", "place.said (“in the Kitchen”), place.areas: the satellite's area"],
  ];
  fill($main,
    h("div", { class: "row" },
      h("a", { href: "#/home" }, "← Home"),
      heading("h1", "Unknown command", "When nothing understood a sentence — no command, no skill, nothing that runs when nothing matches, nor the conversation agent — the satellite says this. Something understood but impossible as said (“the fan in the office” where there is none) still says why. Type a sentence no command takes under a satellite's Test it to hear it.", { class: "grow", style: "margin:0" }),
      h("button", { class: "primary", onclick: save }, "Save")),
    errors,
    h("div", { class: "panel" },
      h("p", { class: "muted small" }, "Empty: the closest command (“It looks like you're trying to …”), or “Sorry, I didn't understand that.”"),
      field("Response", area, "A Home Assistant template. Something that renders to nothing says the default."),
      h("div", { class: "row small" }, h("span", { class: "muted" }, "Start from:"),
        ...UNKNOWN_EXAMPLES.map((example) => h("button", { type: "button", class: "link", onclick: () => { area.value = example; area.focus(); } }, h("code", {}, example)))),
      h("div", { class: "row small" }, h("span", { class: "muted" }, "Variables:"),
        ...vars.map(([name, text]) => h("span", { class: "var" }, h("code", {}, name), hint(text)))),
      )
  );
}

// The command editor. `spec` is edited in place; fields it does not know
// about are kept as loaded.
async function pageCommand(id, prefill, query) {
  await Promise.all([loadStore(), loadSatellites()]);
  const areas = await areaLabels();
  const isNew = !id;
  let spec = isNew ? (prefill || { description: "", examples: [] }) : clone(state.store.commands[id]);
  if (!spec) { fill($main, errorBox(`No command ${id}`)); return; }
  const wanted = new URLSearchParams(query || "").get("satellite");
  if (isNew && wanted && !spec.satellite) spec.satellite = wanted;
  // Back to where it is: the Home, or its satellite's page.
  const back = () => (spec.satellite && state.satellites.some((x) => x.key === spec.satellite) ? `#/satellite/${spec.satellite}` : "#/home");
  let commandId = id || slug(spec.description) || "";
  const actionYaml = { value: await yamlDump(spec.action) };
  const errors = h("div");
  let tab = "form";

  const body = h("div");
  const tabs = h("div", { class: "tabs" });

  function renderTabs() {
    fill(tabs, 
      h("button", { class: tab === "form" ? "active" : "", onclick: () => { tab = "form"; renderTabs(); render(); } }, "Form"),
      h("button", { class: tab === "yaml" ? "active" : "", onclick: async () => { await syncFromForm(); tab = "yaml"; renderTabs(); render(); } }, "YAML"),
    );
  }

  let yamlText = "";
  async function syncFromForm() {
    const form = formSpec();
    try {
      const checked = await api("POST", "commands/check", { id: commandId || "draft", spec: form });
      yamlText = checked.yaml;
    } catch (_) {
      yamlText = await yamlDump(form);
    }
  }

  function formSpec() {
    const out = clone(spec);
    out.action_yaml = actionYaml.value;
    return out;
  }

  async function save() {
    fill(errors, );
    if (!commandId) { errors.append(errorBox("Give the command an id")); return; }
    try {
      if (tab === "yaml") {
        await api("PUT", `commands/${commandId}`, { yaml: yamlText, old_id: id || undefined });
      } else {
        await api("PUT", `commands/${commandId}`, { spec: formSpec(), old_id: id || undefined });
      }
      toast("Saved");
      refreshStatus();
      if (commandId !== id) location.hash = `#/command/${commandId}`;
      else pageCommand(commandId);
    } catch (err) {
      errors.append(errorBox(err));
    }
  }

  async function remove() {
    if (!confirm(`Delete the command ${id}?`)) return;
    await api("DELETE", `commands/${id}`);
    toast("Deleted");
    location.hash = back();
  }

  // Fixed slots: the entity, area or floor the command targets.
  function targetEditor(target) {
    target.slots = target.slots || {};
    const wrap = h("div");
    const labels = {};
    function render() {
      const slots = target.slots || {};
      const rows = [];
      for (const kind of ["entity", "area", "floor"]) {
        const ids = asList(slots[kind]);
        rows.push(field(kind === "entity" ? "Entities" : kind === "area" ? "Areas" : "Floors", h("div", {},
          idChips(ids, kind === "area" ? { ...areas, ...labels } : labels, (i) => { ids.splice(i, 1); setSlot(kind, ids); render(); }),
          hassPicker(kind, (item) => { labels[item.id] = item.name ? `${item.name} (${item.id})` : item.id; if (!ids.includes(item.id)) ids.push(item.id); setSlot(kind, ids); render(); }))));
      }
      const others = Object.keys(slots).filter((k) => !["entity", "area", "floor"].includes(k));
      fill(wrap, h("div", { class: "grid2" }, ...rows),
        others.length ? h("p", { class: "muted small" }, "Other fixed slots (edit in YAML): ", others.map((k) => `${k}=${JSON.stringify(slots[k])}`).join(", ")) : null);
    }
    function setSlot(kind, ids) {
      target.slots = target.slots || {};
      if (ids.length) target.slots[kind] = ids.slice();
      else delete target.slots[kind];
      if (!Object.keys(target.slots).length) delete target.slots;
    }
    render();
    return wrap;
  }

  // What happens: respond / script / scene / action YAML, and a response.
  function behaviorEditor(target, yamlBox) {
    const wrap = h("div");
    const current = () => (target.script ? "script" : target.scene ? "scene" : target.intent ? "intent" : target.timer ? "timer" : target.music ? "music"
      : (yamlBox.value || "").trim() ? "action" : respondsWhenDone(target.response) ? "respond" : "nothing");
    let mode = current();
    function render() {
      const choices = [["respond", "Only respond"], ["script", "Run a script"], ["scene", "Activate a scene"], ["action", "Run an action"], ["intent", "Run an intent"], ["timer", "A timer"], ["music", "Music"], ["nothing", "Nothing"]];
      const radios = h("div", { class: "row" }, ...choices.map(([value, label]) => h("label", {},
        h("input", { type: "radio", name: `do-${Math.random()}`, checked: mode === value, onchange: () => {
          // Responses for outcomes only the old kind had go (a script's own stay with an action).
          const scripted = (m) => m === "script" || m === "action";
          if (!(scripted(mode) && scripted(value))) keepOutcomes(value === "nothing" ? [] : value === "respond" ? ["done"] : value === "intent" ? INTENT_OUTCOMES : ["done", "failed"]);
          mode = value;
          delete target.script; delete target.scene; delete target.timer; delete target.music; delete target.intent;
          if (value === "intent") target.intent = { name: "HassTurnOn", slots: { domain: "light" } };
          if (value === "timer") target.timer = "start";
          if (value === "music") target.music = "play";
          if (value !== "action") yamlBox.value = "";
          if (value === "action" && !yamlBox.value) yamlBox.value = "action: light.turn_on\ntarget: \"{{ target }}\"\n";
          render();
        } }), label)));
      let control = null;
      if (mode === "script" || mode === "scene") {
        const domain = mode;
        control = field(mode === "script" ? "Script" : "Scene", h("div", {},
          target[domain] ? h("div", { class: "chips" }, h("span", { class: "chip" }, target[domain], h("button", { type: "button", onclick: () => { delete target[domain]; render(); } }, "×"))) : null,
          hassPicker("entity", (item) => { target[domain] = item.id; render(); }, { domain })),
          mode === "script" ? "The script gets slots, satellite, speaker, target and command as variables; what it returns (stop with response_variable) is the response's result." : null);
      } else if (mode === "timer") {
        const ops = { start: "Start a timer", cancel: "Cancel a timer", cancel_all: "Cancel every timer", pause: "Pause a timer", resume: "Resume a timer", add: "Add time", subtract: "Take time off", status: "Say how long is left", alarm: "Set an alarm", cancel_alarm: "Cancel an alarm", alarm_status: "Say the alarms", reminder: "Remind at a time", cancel_reminder: "Cancel a reminder", reminder_status: "Say the reminders" };
        control = field("Timer", h("select", { onchange: (e) => { target.timer = e.target.value; keepOutcomes([...Object.keys(timerOutcomes()), "failed"]); render(); } },
          ...Object.entries(ops).map(([op, label]) => h("option", { value: op, selected: target.timer === op }, label))),
          "On the satellite's own timers (its voice_timer_list in Home Assistant). Which timer: slots.timer_name (a running timer's name, from the running_timers list), slots.timer (free text, matched loosely), slots.duration, or the only one. Alarms and reminders are on the Voice Call calendar in Home Assistant (English): slots.time says when, slots.reminder what (and, to cancel one, which); a reminder without a time goes on the to-do list among the entities.");
      } else if (mode === "music") {
        const ops = { play: "Play what was asked for", add: "Add it to the queue", radio: "Play music like it (or like what's playing)", pause: "Pause", resume: "Resume", stop: "Stop", next: "Next track", previous: "Previous track", volume_up: "Volume up", volume_down: "Volume down", volume_set: "Set the volume", mute: "Mute", unmute: "Unmute", shuffle_on: "Shuffle on", shuffle_off: "Shuffle off", repeat_all: "Repeat all", repeat_one: "Repeat this track", repeat_off: "Repeat off", status: "Say what's playing", move: "Move it to the player named (area, floor or speaker)" };
        control = field("Music", h("select", { onchange: (e) => { target.music = e.target.value; keepOutcomes([...Object.keys(musicOutcomes()), "failed"]); render(); } },
          ...Object.entries(ops).map(([op, label]) => h("option", { value: op, selected: target.music === op }, label))),
          "On a Music Assistant player: one the command names (Target, or slots.area), else the satellite's own (its Sendspin player), or, to pause, skip or ask what's playing, whichever plays in the satellite's area. Playing searches Music Assistant for slots.query (free text: “play …”); slots.media_type (artist, album, track, playlist, radio) narrows it. Set the volume takes a percentage.");
      } else if (mode === "intent") {
        const intent = typeof target.intent === "string" ? { name: target.intent } : { ...(target.intent || {}) };
        const save = () => { target.intent = { name: intent.name, ...(intent.slots && Object.keys(intent.slots).length ? { slots: intent.slots } : {}), ...(intent.anywhere ? { anywhere: true } : {}), ...(intent.home ? { home: true } : {}) }; };
        const name = h("input", { type: "text", list: "intent-names", value: intent.name || "", placeholder: "HassTurnOn", oninput: (e) => { intent.name = e.target.value.trim(); save(); } });
        const known = h("datalist", { id: "intent-names" }, ...INTENTS.map((n) => h("option", { value: n })));
        const lines = h("textarea", { class: "code short", placeholder: "domain: light\nbrightness: {{ slots.percentage }}",
          oninput: (e) => { intent.slots = intentSlots(e.target.value); save(); } });
        lines.value = Object.entries(intent.slots || {}).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join("\n");
        const where = h("select", { "aria-label": "With nothing named", onchange: (e) => { intent.anywhere = e.target.value === "anywhere"; intent.home = e.target.value === "home"; save(); } },
          h("option", { value: "", selected: !intent.anywhere && !intent.home }, "The satellite's area"),
          h("option", { value: "anywhere", selected: !!intent.anywhere }, "The satellite's area, else the whole home"),
          h("option", { value: "home", selected: !!intent.home }, "The whole home"));
        const anywhere = field("With nothing named", where);
        control = h("div", {},
          field("Intent", h("div", {}, name, known), "Run as Assist would: only on what is exposed to Assist."),
          field("Slots", lines, "One per line; a template sees what was heard. It acts on what was named (device, area or floor), else the satellite's area: those slots are filled in."),
          anywhere);
      } else if (mode === "action") {
        const area = h("textarea", { class: "code", oninput: (e) => { yamlBox.value = e.target.value; } });
        area.value = yamlBox.value;
        control = field("Action", area,
          "Home Assistant script syntax: one action or a list. Templates see slots, satellite, speaker, target and command. target: \"{{ target }}\" is what was named, or else the satellite's area -- every device there, exposed to Assist or not (Run an intent acts only on exposed ones).");
      }
      fill(wrap, radios, control, verbosityField(), responsesEditor());
    }

    // How much it says when done: the command's level, or what it does by default.
    const question = () => mode === "respond" || (mode === "timer" && target.timer === "status") || (mode === "music" && target.music === "status");
    function verbosityField() {
      const kindDefault = mode === "music" && !["play", "add", "radio", "status"].includes(target.music) ? "beep" : mode === "nothing" ? "silent" : "specific";
      const levels = LEVELS.filter(([value]) => !(question() && (value === "beep" || value === "silent")));
      const select = h("select", { onchange: (e) => { if (e.target.value) target.verbosity = e.target.value; else delete target.verbosity; render(); } },
        h("option", { value: "", selected: !target.verbosity }, `Default (${levelName(kindDefault)})`),
        ...levels.map(([value, label]) => h("option", { value, selected: target.verbosity === value }, label)));
      const help = question()
        ? "A question always answers; with an answer written for each level, this picks which."
        : "Silent says nothing at all, for what needs no answer (“never mind”). A beep is the acknowledgement sound, for what you can see happen: done somewhere else (another area, a floor, a device in no area), it says what the Settings page's “Responses” says instead. Only when it's done; what went wrong is always said.";
      return field("How much it says", select, help);
    }

    // A response for each way the command can turn out; a plain string is the
    // done one. Done's may be one for each level ({brief, specific, verbose}).
    const responses = () => (typeof target.response === "string" ? { done: target.response } : { ...(target.response || {}) });
    const ORDER = ["done", "no_timers", "no_player", "nothing_playing", "not_found", "ambiguous", "already", "missing_duration", "missing_query", "missing_volume"];
    const rank = (key) => (key === "failed" ? 99 : ORDER.includes(key) ? ORDER.indexOf(key) : 50);
    const written = (value) => (value && typeof value === "object" ? Object.values(value).some(Boolean) : Boolean(value));
    function setResponses(map) {
      const keys = Object.keys(map).filter((k) => written(map[k])).sort((a, b) => rank(a) - rank(b));
      if (!keys.length) delete target.response;
      else if (keys.length === 1 && keys[0] === "done" && typeof map.done === "string") target.response = map.done;
      else target.response = Object.fromEntries(keys.map((k) => [k, map[k]]));
    }
    let byLevel = null;
    // The done response: one for every level, or one for each (brief,
    // specific, verbose) with the built-in sentences as placeholders.
    const LEVEL_HELP = "A level without one says the closest written (less first). Keep the brief one fixed — no templates — so text-to-speech can cache it.";
    function doneEditor(placeholder, levelDefaults, big, more) {
      const help = more ? `${LEVEL_HELP} ${more}` : LEVEL_HELP;
      const done = responses().done;
      if (byLevel === null) byLevel = Boolean(done && typeof done === "object");
      const toggle = h("button", { type: "button", class: "link", onclick: () => {
        const next = responses();
        if (byLevel) {
          const d = next.done && typeof next.done === "object" ? next.done : {};
          const only = Object.keys(d).filter((k) => d[k]);
          if (only.length > 1 && !confirm("Keep only the specific one, for every level?")) return;
          next.done = d.specific || d[only[0]] || "";
        } else if (typeof next.done === "string" && next.done) {
          next.done = { specific: next.done };
        }
        byLevel = !byLevel;
        setResponses(next);
        render();
      } }, byLevel ? "One for every level" : "One for each level");
      if (!byLevel) {
        const area = h("textarea", { class: big ? "" : "short", placeholder: placeholder || (levelDefaults && levelDefaults.specific) || "", oninput: (e) => { const next = responses(); next.done = e.target.value; setResponses(next); } });
        area.value = typeof done === "string" ? done : "";
        return field(h("span", {}, "When it's done ", toggle), area, help);
      }
      const boxes = LEVELS.filter(([level]) => level !== "beep" && level !== "silent").map(([level]) => {
        const area = h("textarea", { class: "short", placeholder: (levelDefaults && levelDefaults[level]) || (level === "brief" ? "A fixed sentence, e.g. Turned on the lights." : ""), oninput: (e) => {
          const next = responses();
          const d = next.done && typeof next.done === "object" ? { ...next.done } : {};
          if (e.target.value) d[level] = e.target.value; else delete d[level];
          next.done = d;
          setResponses(next);
        } });
        area.value = done && typeof done === "object" ? done[level] || "" : "";
        return field(level[0].toUpperCase() + level.slice(1), area);
      });
      return h("div", {}, h("div", { class: "label-row" }, h("span", { class: "label" }, "When it's done ", toggle), hint(help)),
        h("div", { class: "grid2" }, ...boxes));
    }
    function keepOutcomes(allowed) {
      if (!("response" in target)) return;
      const map = responses();
      for (const key of Object.keys(map)) if (!allowed.includes(key)) delete map[key];
      setResponses(map);
    }
    const outcomeInfo = () => (state.status && state.status.outcomes) || { timer: {} };
    const timerOutcomes = () => outcomeInfo().timer[target.timer] || {};
    const musicOutcomes = () => (outcomeInfo().music || {})[target.music] || {};
    function responsesEditor() {
      const map = responses();
      const info = outcomeInfo();
      const box = (key, placeholder, big) => {
        const area = h("textarea", { class: big ? "" : "short", placeholder, oninput: (e) => { const next = responses(); next[key] = e.target.value; setResponses(next); } });
        area.value = map[key] || "";
        return area;
      };
      const vars = "Rendered by Home Assistant with the same variables, plus result: what a script returned, result.outcome, and result.speech (what is said without a response).";
      if (mode === "nothing") {
        return h("p", { class: "muted small" }, "Ends the conversation, and that's all (“never mind”).", hint("To say something, choose Only respond; for the acknowledgement sound, set How much it says to Beep."));
      }
      if (mode === "respond") {
        return doneEditor("It's {{ now().strftime('%H:%M') }}", null, true, vars);
      }
      const rows = [];
      if (mode === "intent") {
        const said = outcomeInfo().intent || {};
        const labels = { no_devices: "Nothing of the kind there", not_exposed: "None of it exposed to Assist", unavailable: "All of it unavailable", no_area: "This satellite has no area" };
        for (const key of INTENT_OUTCOMES) {
          if (key !== "done" && key !== "failed") rows.push(field(labels[key], box(key, said[key] || "", false)));
        }
        rows.push(field("If it fails", box("failed", info.failed, false), "Home Assistant refused. result.error says why."));
        return h("div", {}, h("div", { class: "label-row" }, h("span", { class: "label" }, "Responses"),
            hint(`Each outcome says its own sentence (shown) unless you give one here. Templates also see result.count, result.names and result.ids (what it acted on), result.unavailable, and result.speech (Home Assistant's own). ${vars}`)),
          doneEditor("Turned on the lights {{ place.said }}", null, false),
          h("div", { class: "grid2" }, ...rows));
      }
      if (mode === "music") {
        const said = musicOutcomes();
        const levels = (info.music_levels || {})[target.music];
        const labels = { no_player: "No music player", nothing_playing: "Nothing playing", not_found: "Nothing found", missing_query: "Nothing asked for", missing_volume: "No volume heard", already: "Already playing" };
        for (const [key, example] of Object.entries(said)) {
          if (key !== "done") rows.push(field(labels[key] || key, box(key, example || "(a short sound)", false)));
        }
        rows.push(field("If it fails", box("failed", info.failed, false), "Home Assistant or Music Assistant refused."));
        return h("div", {}, h("div", { class: "label-row" }, h("span", { class: "label" }, "Responses"),
            hint(`Each outcome says its own sentence (shown) unless you give one here. Templates also see player (the one it acted on), players and, when it searched, media (name, artist, media_type, uri). ${vars}`)),
          doneEditor(said.done, levels, false),
          h("div", { class: "grid2" }, ...rows));
      }
      if (mode === "timer") {
        const said = timerOutcomes();
        const levels = (info.timer_levels || {})[target.timer];
        const labels = { no_timers: "No timers running", not_found: "No such timer", ambiguous: "Several timers fit (asks which)", already: target.timer === "pause" ? "Already paused" : "Already running", missing_duration: "No length heard" };
        for (const [key, example] of Object.entries(said)) {
          if (key !== "done") rows.push(field(labels[key] || key, box(key, example, false)));
        }
        rows.push(field("If it fails", box("failed", info.failed, false), `Home Assistant refused. Without the Voice Call integration it says "${info.timer_failed}"`));
        return h("div", {}, h("div", { class: "label-row" }, h("span", { class: "label" }, "Responses"),
            hint(`Each outcome says its own sentence (shown) unless you give one here. Templates also see timer (the timer it was about) and timers. ${vars}`)),
          doneEditor(said.done, levels, false),
          h("div", { class: "grid2" }, ...rows));
      }
      rows.push(field("If it fails", box("failed", info.failed, false), "Home Assistant refused the action. result.error says why."));
      if (mode === "script" || mode === "action") {
        for (const key of Object.keys(map).filter((k) => k !== "done" && k !== "failed")) {
          rows.push(field(h("span", {}, "Outcome ", h("code", {}, key), " ", h("button", { type: "button", class: "link", onclick: () => { const next = responses(); delete next[key]; setResponses(next); render(); } }, "remove")), box(key, "", false)));
        }
      }
      const extra = mode === "script" || mode === "action" ? (() => {
        const name = h("input", { type: "text", placeholder: "outcome name, e.g. nobody_home" });
        const add = () => {
          const key = name.value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
          if (!key || !/^[a-z]/.test(key)) return;
          const next = responses();
          if (!(key in next)) next[key] = "{{ result.speech }}";
          setResponses(next);
          render();
        };
        return h("div", { class: "row" }, name, h("button", { type: "button", onclick: add }, "Add an outcome"),
          hint("A script picks one by returning outcome (stop with response_variable: {outcome: nobody_home})."));
      })() : null;
      return h("div", {}, doneEditor(mode === "script" ? "Spoken response template; without one, the script's speech is said" : "Spoken response template, e.g. Turned on the lights {{ place.said }}", null, false),
        h("div", { class: "grid2" }, ...rows), extra,
        h("p", { class: "muted small" }, "Optional.", hint(`${vars} place says where it acted: place.said (“in the Kitchen”), place.here, place.areas. Nothing to say plays a short sound.`)));
    }
    render();
    return wrap;
  }

  function namesEditor() {
    spec.names = spec.names || {};
    const wrap = h("div");
    const shared = state.store.names || {};
    const kinds = { entity: "entities", area: "areas", floor: "floors" };
    function render() {
      fill(wrap, ...Object.entries(kinds).map(([type, plural]) => {
        const req = spec.names[type];
        const on = !!req;
        const r = typeof req === "number" ? { count: req } : (req || {});
        const available = Object.entries(shared[plural] || {});
        const update = (next) => {
          const merged = { ...r, ...next };
          for (const k of Object.keys(merged)) if (merged[k] === undefined || (Array.isArray(merged[k]) && !merged[k].length)) delete merged[k];
          spec.names[type] = merged;
        };
        return h("div", { class: "group", style: "align-items:flex-start" },
          h("label", { style: "min-width:120px" }, h("input", { type: "checkbox", checked: on, onchange: (e) => { if (e.target.checked) spec.names[type] = { count: 1 }; else delete spec.names[type]; render(); slotsChanged(); } }), `Listen for ${type === "entity" ? "a device" : `a${type === "area" ? "n" : ""} ${type}`}`),
          on ? h("div", { class: "grow stack" },
            h("div", { class: "row" },
              h("label", {}, "how many", h("input", { type: "number", min: 1, max: 3, value: r.count || 1, style: "width:60px", onchange: (e) => { update({ count: Number(e.target.value) || 1 }); } }))),
            available.length ? h("div", {},
              h("div", { class: "small muted" }, `Only these ${plural} (none chosen: all of them):`),
              h("div", { class: "row" }, ...available.map(([nid, entry]) => h("label", { class: "small" },
                h("input", { type: "checkbox", checked: asList(r.ids).includes(nid), onchange: (e) => {
                  const ids = asList(r.ids).filter((x) => x !== nid);
                  if (e.target.checked) ids.push(nid);
                  update({ ids: ids.length ? ids : undefined });
                } }), typeof entry === "string" ? entry : entry.name)))) :
              h("div", { class: "small muted" }, `No ${plural} have names yet: add them on the Names page.`),
            type === "entity" ? field("Only these domains", chipsInput(r.domains || [], (v) => update({ domains: v.length ? v : undefined }), "light, switch")) : null) : null);
      }));
    }
    render();
    return wrap;
  }

  function numberEditor() {
    const wrap = h("div");
    function render() {
      const n = spec.number;
      const preset = typeof n === "string" ? n : (n && n.preset) || (n ? "custom" : "none");
      const obj = typeof n === "object" && n ? n : {};
      const select = h("select", { onchange: (e) => {
        const v = e.target.value;
        if (v === "none") delete spec.number;
        else if (v === "custom") spec.number = { min: 0, max: 100 };
        else spec.number = v;
        render();
        slotsChanged();
      } }, ...["none", ...((state.status && state.status.number_presets) || []), "custom"].map((p) => h("option", { value: p, selected: p === preset }, p === "none" ? "No number" : p === "custom" ? "Custom range" : p)));
      const extra = [];
      if (preset !== "none") {
        const asObj = () => (typeof spec.number === "string" ? { preset: spec.number } : spec.number);
        extra.push(h("label", {}, "slot name", h("input", { type: "text", value: obj.slot || "", placeholder: preset === "duration" ? "duration" : preset === "custom" ? "number" : preset, style: "width:120px", onchange: (e) => { spec.number = { ...asObj(), slot: e.target.value || undefined }; if (!spec.number.slot) delete spec.number.slot; slotsChanged(); } })));
      }
      if (preset === "custom") {
        for (const key of ["min", "max", "step"]) {
          extra.push(h("label", {}, key, h("input", { type: "number", value: obj[key] ?? "", style: "width:80px", onchange: (e) => { if (e.target.value === "") delete spec.number[key]; else spec.number[key] = Number(e.target.value); } })));
        }
      }
      fill(wrap, h("div", { class: "row" }, select, ...extra,
        preset === "duration" ? hint("Heard as “ten minutes”, “an hour and a half”: slots.duration has hours, minutes, seconds, total_seconds and text (HH:MM:SS).") : null,
        preset === "percentage" ? hint("0–100, multiples of 10 preferred where the audio is unsure (fifteen vs fifty).") : null));
    }
    render();
    return wrap;
  }

  function listsEditor() {
    const wrap = h("div");
    const dynamicLists = (state.status && state.status.dynamic_lists) || {};
    const sharedLists = [...new Set([...Object.keys(state.store.lists || {}), ...Object.keys(state.store.builtin_lists || {})]), ...Object.keys(dynamicLists)];
    function render() {
      const lists = spec.lists || {};
      fill(wrap, 
        ...Object.entries(lists).map(([slot, def]) => {
          const d = typeof def === "string" ? { list: def } : def;
          const valuesText = d.values ? valuesToText(d.values) : "";
          const ta = h("textarea", { class: "code", placeholder: "red: red, crimson\nblue", style: "min-height:70px", onchange: (e) => { spec.lists[slot] = { values: textToValues(e.target.value) }; } });
          ta.value = valuesText;
          return h("div", { class: "panel" },
            h("div", { class: "row" }, h("b", {}, `slots.${slot}`),
              h("select", { onchange: (e) => { spec.lists[slot] = e.target.value ? { list: e.target.value } : { values: {} }; render(); } },
                h("option", { value: "" }, "its own values"), ...sharedLists.map((n) => h("option", { value: n, selected: d.list === n }, dynamicLists[n] ? `${n}: ${dynamicLists[n]}` : `shared list: ${n}`))),
              h("button", { type: "button", class: "link right", onclick: () => { delete spec.lists[slot]; if (!Object.keys(spec.lists).length) delete spec.lists; render(); slotsChanged(); } }, "remove")),
            d.list ? null : field("Values", ta, "One value per line: value: phrase, other phrase. A line with only a phrase is its own value."));
        }),
        h("button", { type: "button", onclick: () => { const name = prompt("Slot name (e.g. color)"); if (!name) return; spec.lists = spec.lists || {}; spec.lists[slug(name)] = { values: {} }; render(); slotsChanged(); } }, "Add a value list"),
      );
    }
    render();
    return wrap;
  }

  function wildcardEditor() {
    const wrap = h("div");
    function render() {
      const w = spec.wildcard;
      if (!w) {
        fill(wrap, h("button", { type: "button", onclick: () => { spec.wildcard = { slot: "text", after: [], max_words: 3 }; render(); slotsChanged(); } }, "Add free text"));
        return;
      }
      const mode = w.bounded ? "bounded" : w.to_end ? "to_end" : "either";
      fill(wrap, 
        h("div", { class: "row" },
          h("label", {}, "slot", h("input", { type: "text", value: w.slot, style: "width:120px", onchange: (e) => { w.slot = slug(e.target.value) || "text"; slotsChanged(); } })),
          h("label", {}, "at most", h("input", { type: "number", min: 1, max: 12, value: w.max_words || 3, style: "width:60px", onchange: (e) => { w.max_words = Number(e.target.value) || 3; } }), "words"),
          h("button", { type: "button", class: "link right", onclick: () => { delete spec.wildcard; render(); slotsChanged(); } }, "remove")),
        h("div", { class: "row" },
          ...[["bounded", "between two keywords (add … to the list)"], ["to_end", "after a keyword, to the end (remind me to …)"], ["either", "right after or right before a keyword (the … timer)"]].map(([value, label]) =>
            h("label", { class: "small" }, h("input", { type: "radio", name: `wild-${Math.random()}`, checked: mode === value, onchange: () => { delete w.bounded; delete w.to_end; if (value === "bounded") w.bounded = true; if (value === "to_end") w.to_end = true; render(); } }), label))),
        h("div", { class: "grid2" },
          field("Starts right after", chipsInput(w.after || [], (v) => { w.after = v; }, "add, put"), "Must also be keywords of the command."),
          mode === "to_end" ? null : field("Ends right before", chipsInput(w.before || [], (v) => { w.before = v; }, "to, on"))),
      );
    }
    render();
    return wrap;
  }

  // What it listens for, by slot name, in the order the app uses.
  function heardSlots() {
    const out = Object.keys(spec.names || {});
    out.push(...Object.keys(spec.lists || {}));
    if (spec.number) {
      const n = spec.number;
      const obj = typeof n === "object" ? n : { preset: n };
      out.push(obj.slot || (obj.preset === "duration" || obj.units ? "duration" : obj.preset || "number"));
    }
    if (spec.wildcard && spec.wildcard.slot) out.push(spec.wildcard.slot);
    return [...new Set(out)];
  }
  // The ways it may be said: sets of slots, each heard whole. Kept in step
  // with the slots: a removed one leaves every way, a new one joins them all.
  const combosWrap = h("div");
  let knownSlots = heardSlots();
  function slotsChanged() {
    const slots = heardSlots();
    if (spec.combinations) {
      const added = slots.filter((x) => !knownSlots.includes(x));
      const seen = new Set();
      spec.combinations = spec.combinations.map((c) => [...c.filter((x) => slots.includes(x)), ...added])
        .map((c) => slots.filter((x) => c.includes(x)))
        .filter((c) => { const key = c.join("+"); if (seen.has(key)) return false; seen.add(key); return true; });
      if (spec.combinations.length === 1 && spec.combinations[0].length === slots.length) delete spec.combinations;
    }
    knownSlots = slots;
    renderCombos();
  }
  function renderCombos() {
    const slots = heardSlots();
    if (!slots.length) { fill(combosWrap, h("p", { class: "muted small" }, "One: it hears nothing said.")); return; }
    const combos = spec.combinations || [slots];
    const set = (next) => {
      if (next.length === 1 && next[0].length === slots.length) delete spec.combinations;
      else spec.combinations = next.map((c) => slots.filter((x) => c.includes(x)));
      renderCombos();
    };
    const label = (slot) => ({ entity: "a device", area: "an area", floor: "a floor" }[slot] || slot.replace(/_/g, " "));
    fill(combosWrap,
      ...combos.map((combo, index) => h("div", { class: "row" },
        h("span", { class: "small muted", style: "min-width:70px" }, index === 0 ? "First:" : "Or:"),
        ...slots.map((slot) => h("label", { class: "small" }, h("input", { type: "checkbox", checked: combo.includes(slot), onchange: (e) => {
          const next = combos.map((c) => [...c]);
          next[index] = e.target.checked ? [...next[index], slot] : next[index].filter((x) => x !== slot);
          set(next);
        } }), label(slot))),
        combo.length ? null : h("span", { class: "small muted" }, "nothing named: its fixed target, or the satellite's area"),
        combos.length > 1 ? h("button", { type: "button", class: "link right", onclick: () => set(combos.filter((_, i) => i !== index)) }, "remove") : null)),
      h("button", { type: "button", onclick: () => set([...combos, []]) }, "Add a way to say it"));
  }
  renderCombos();

  function renderForm() {
    const idInput = h("input", { type: "text", value: commandId, placeholder: "turn_on_the_lights", oninput: (e) => { commandId = slug(e.target.value); } });
    const description = h("input", { type: "text", value: spec.description || "", style: "width:100%", placeholder: "turn on the lights", oninput: (e) => { spec.description = e.target.value; if (isNew && !idInput.dataset.touched) { commandId = slug(e.target.value); idInput.value = commandId; } } });
    idInput.addEventListener("input", () => { idInput.dataset.touched = "1"; });
    const iconBox = h("div");
    const drawIcon = (open) => {
      const current = h("button", { type: "button", class: "icon-button", title: "Change the icon", onclick: () => drawIcon(!open) }, icon(iconFor(spec), "big"));
      const choice = (name, label) => h("button", { type: "button", class: `icon-choice ${(spec.icon || "") === (name || "") ? "on" : ""}`, title: label || name,
        onclick: () => { if (name) spec.icon = name; else delete spec.icon; drawIcon(false); } }, icon(name || KIND_ICONS[kindOf(spec)]));
      fill(iconBox, current, open ? h("div", { class: "icon-grid" }, choice(null, "Default: what it does"), ...ICON_PICKER.map((n) => choice(n)), spec.icon && !ICON_PICKER.includes(spec.icon) ? choice(spec.icon) : null) : null);
    };
    drawIcon(false);
    return h("div", {},
      h("div", { class: "panel" },
        h("div", { class: "row", style: "align-items:flex-start" },
          field("Icon", iconBox),
          h("div", { class: "grid2 grow" },
            field("What it does", description, "A verb phrase. It's also how a near miss is described: “It looks like you're trying to turn on the lights, but…”"),
            field("Id", idInput, "Lowercase letters, digits and underscores."))),
        h("div", { class: "row" },
          h("label", {}, h("input", { type: "checkbox", checked: spec.disabled, onchange: (e) => { if (e.target.checked) spec.disabled = true; else delete spec.disabled; } }), "Turned off")),
        field("Where", h("select", { onchange: (e) => { if (e.target.value) spec.satellite = e.target.value; else delete spec.satellite; } },
          h("option", { value: "", selected: !spec.satellite }, "The Home"),
          ...state.satellites.map((x) => h("option", { value: x.key, selected: spec.satellite === x.key }, x.name)),
          spec.satellite && !state.satellites.some((x) => x.key === spec.satellite) ? h("option", { value: spec.satellite, selected: true }, `${spec.satellite} (not here any more)`) : null),
          "The Home's run on every satellite that takes the Home's. One satellite's goes before the Home's commands that take the same sentence."),
        field("When nothing else matches", h("div", { class: "wake-list" },
          [["meaning", "By meaning"], ["llm", "By model"]].map(([step, label]) => h("label", {},
            h("input", { type: "checkbox", checked: asList(spec.fallback).includes(step), onchange: (e) => {
              const now = asList(spec.fallback).filter((x) => x !== step);
              if (e.target.checked) now.push(step);
              const ordered = ["meaning", "llm"].filter((x) => now.includes(x));
              if (ordered.length) spec.fallback = ordered; else delete spec.fallback;
            } }), label,
            // Turned off for every command: ticked here, it still won't run.
            ((state.status || {}).fallback || {})[step] === false
              ? h("a", { class: "badge warn", href: "#/settings", title: "Turned off for every command under “When nothing matches” on the Settings page" }, "off in Settings") : null))),
          (id && ((state.store.details || {})[id] || {}).needs_said)
            ? "It hears something (a name, number, value or text): meaning picks the command, and the model hears what was said in it."
            : "Only after keywords and example sentences found nothing. See “When nothing matches” on the Settings page.")),
      h("div", { class: "panel" }, heading("h2", "Target", "What the command acts on, passed to Home Assistant as slots as if it had been heard. The phrase can then be anything."),
        targetEditor(spec)),
      h("div", { class: "panel" }, h("h2", {}, "Listen for"),
        ((info) => field("Example sentences", h("div", {},
          chipsInput(spec.examples || [], (v) => { spec.examples = v; info.show(v); }, "type a whole sentence and press Enter"), info.box),
          "Said word for word, an example always matches, ahead of keywords — when it's all that was said: “pause” isn't “pause the timer”. Write (a|b) for either and [a] for what may be left out, inside a word too: “(turn|switch) on the light[s]” is four sentences. No placeholders: slots come from keywords below."))(examplesInfo(spec.examples || [])),
        heading("h3", "Keywords", "Every required group must be heard, by any one of its alternatives, in any order. Write “turn … on” for words that may be split (“turn the lights on”)."),
        field("Required", groupsEditor(spec.require, (v) => { spec.require = v; }, "Add a required group")),
        field("Optional", groupsEditor(spec.optional, (v) => { spec.optional = v; }, "Add an optional group"), "Words that may be said and should not count against the match."),
        field("Must not hear", chipsInput(spec.exclude || [], (v) => { spec.exclude = v; }, "is, are"), "A statement is not a command: “the door is locked”."),
        h("h3", {}, "Names"), namesEditor(),
        h("h3", {}, "Number"), numberEditor(),
        h("h3", {}, "Value lists"), listsEditor(),
        heading("h3", "Free text", "Re-read from the TDT transcript for spelling. Keep it bounded: the fewer words it may take, the fewer mishearings it swallows."), wildcardEditor(),
        heading("h3", "Ways to say it", "What it hears is never optional: each way is said whole, and a command never runs with something said left out. “Turn off the lights” could be said with nothing named (the satellite's area), with an area, or with a floor. The first way gets the example sentences."),
        combosWrap),
      h("div", { class: "panel" }, h("h2", {}, "What happens"), behaviorEditor(spec, actionYaml)),
    );
  }

  function renderYaml() {
    const area = h("textarea", { class: "code big", oninput: (e) => { yamlText = e.target.value; } });
    area.value = yamlText;
    return h("div", { class: "panel" }, area);
  }

  function render() {
    fill(body, tab === "form" ? renderForm() : renderYaml());
  }

  renderTabs();
  render();
  fill($main, 
    h("div", { class: "row" },
      h("a", { href: back() }, `← ${spec.satellite ? placeName(spec.satellite) : "Home"}`),
      h("h1", { class: "grow", style: "margin:0" }, isNew ? "New command" : (spec.title || spec.description || id)),
      isNew ? null : h("button", { class: "danger", onclick: remove }, "Delete"),
      isNew ? null : h("button", { onclick: () => duplicateDialog(id) }, "Duplicate"),
      h("button", { class: "primary", onclick: save }, "Save")),
    spec.needs ? h("div", { class: "notice info" }, asList(spec.needs).join(" ")) : null,
    errors, tabs, body,
    h("div", { class: "row" }, h("span", { class: "grow" }), h("button", { class: "primary", onclick: save }, "Save")),
  );
}

function valuesToText(values) {
  if (Array.isArray(values)) return values.join("\n");
  return Object.entries(values || {}).map(([value, phrases]) => {
    const list = asList(phrases);
    return list.length === 1 && String(list[0]) === String(value) ? value : `${value}: ${list.join(", ")}`;
  }).join("\n");
}

function textToValues(text) {
  const out = {};
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const at = line.indexOf(":");
    if (at < 0) { out[line.trim()] = line.trim(); continue; }
    const value = line.slice(0, at).trim();
    const phrases = line.slice(at + 1).split(",").map((p) => p.trim()).filter(Boolean);
    out[value] = phrases.length ? phrases : value;
  }
  return out;
}

// ----------------------------------------------------------------- dialogs

// A dialog over the page, closed by Escape, a click outside or Close.
// `content` is put under its title; `cls` sizes it.
function openDialog(title, content, cls) {
  const previous = document.activeElement;
  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (previous && previous.focus) previous.focus();
  }
  function onKey(e) { if (e.key === "Escape") close(); }
  const overlay = h("div", { class: "overlay", onclick: (e) => { if (e.target === overlay) close(); } },
    h("div", { class: `dialog ${cls || ""}`, role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "row" }, h("h2", { class: "grow", style: "margin:0" }, title),
        h("button", { type: "button", class: "link", "aria-label": "Close", onclick: close }, "Close")),
      content));
  document.addEventListener("keydown", onKey);
  document.body.append(overlay);
  return close;
}

// Duplicate a command, as saved: a copy to change -- on a satellite, to do
// something else there, where it goes before the Home's.
async function duplicateDialog(id) {
  const [store] = await Promise.all([loadStore(), loadSatellites()]);
  const spec = store.commands[id] || {};
  const from = (store.details[id] || {}).satellite || "";
  const idInput = h("input", { type: "text", value: "" });
  const title = h("input", { type: "text", value: `${spec.title || spec.description || id} (copy)`, style: "width:100%" });
  const place = h("select", {},
    h("option", { value: "", selected: !from }, "The Home"),
    ...state.satellites.map((x) => h("option", { value: x.key, selected: from === x.key }, x.name)));
  const errors = h("div");
  // An id that says where it is: lights_on_kitchen_vpe.
  function suggest() {
    if (idInput.dataset.touched) return;
    let copyId = place.value ? `${id}_${slug(placeName(place.value))}` : `${id}_copy`;
    for (let n = 2; store.commands[copyId]; n++) copyId = `${place.value ? `${id}_${slug(placeName(place.value))}` : `${id}_copy`}_${n}`;
    idInput.value = copyId;
  }
  idInput.addEventListener("input", () => { idInput.dataset.touched = "1"; });
  place.addEventListener("change", suggest);
  suggest();
  async function create() {
    fill(errors);
    try {
      const result = await api("POST", `commands/${id}/duplicate`, { new_id: slug(idInput.value), title: title.value.trim(), satellite: place.value || null });
      close();
      toast(`Made ${result.id}`);
      refreshStatus();
      location.hash = `#/command/${result.id}`;
    } catch (err) { errors.append(errorBox(err)); }
  }
  const close = openDialog(`Duplicate ${spec.title || spec.description || id}`, h("div", { class: "stack" },
    field("Where", place, "On a satellite, a copy goes before the Home's commands that take the same sentence: duplicate one of the Home's there to have it do something else on that satellite."),
    h("div", { class: "grid2" }, field("Id", idInput, "Lowercase letters, digits and underscores."), field("Title", title)),
    errors,
    h("div", { class: "row" }, h("span", { class: "grow" }, ""), h("button", { type: "button", onclick: () => close() }, "Cancel"), h("button", { class: "primary", onclick: create }, "Duplicate"))), "compact");
  idInput.focus();
}

// ------------------------------------------------------------- new command

// New command: the library's commands to start from -- searched, filtered by
// group -- and, always there, a custom one. A command with versions is one
// row that opens to them: on this satellite (nothing named), on a device by
// name, in an area or floor by name. Each version is added on its own.
const VARIANT_ICONS = { here: "mdi:crosshairs-gps", device: "mdi:devices", place: "mdi:home-map-marker" };

// `target`: where it goes -- the Home (null) or a satellite's key.
async function newCommandDialog(target) {
  let data, store, skillData;
  try {
    [data, store, skillData] = await Promise.all([api("GET", "library"), loadStore(), api("GET", "skills"), loadSatellites()]);
  } catch (err) { toast(err.message); return; }
  const where = target ? placeName(target) : "the Home";
  // Added: one of the commands there has its id, or its id and a suffix (a
  // satellite's copy: play_music_kitchen). A command whose id you changed is
  // yours, and the library's can be added again.
  const here = Object.keys(store.commands || {}).filter((id) => ((store.details[id] || {}).satellite || null) === (target || null));
  const yours = { has: (libraryId) => here.some((id) => id === libraryId || id.startsWith(`${libraryId}_`)) };
  const skillsHere = new Set(target ? (skillData.satellites[target] || []) : skillData.home);
  // On a satellite that takes the Home's: what it has from the Home already.
  const sat = target ? state.satellites.find((x) => x.key === target) : null;
  const homeIds = sat && sat.home ? Object.keys(store.commands || {}).filter((id) => !(store.details[id] || {}).satellite) : [];
  const fromHome = (libraryId) => homeIds.some((id) => id === libraryId || id.startsWith(`${libraryId}_`));
  const homeHas = h("span", { class: "badge", style: "margin-left:8px", title: "It runs here as the Home's. Add it to change it for this satellite only." }, "the Home's has it");
  const QUESTIONS = "Questions";
  const groups = [...new Map(data.entries.map((e) => [e.group, e.group_icon])).entries(), [QUESTIONS, "mdi:comment-question-outline"]];
  // Each command and its versions, in the library's order.
  const families = [];
  const byKey = new Map();
  for (const entry of data.entries) {
    const key = `${entry.file}/${entry.family}`;
    if (!byKey.has(key)) {
      const family = { key, group: entry.group, title: entry.family_title, icon: entry.family_icon, versions: [] };
      byKey.set(key, family);
      families.push(family);
    }
    byKey.get(key).versions.push(entry);
  }
  const opened = new Set();
  let group = "";
  const search = h("input", { type: "search", placeholder: "Search the library: lights, timer, play…", "aria-label": "Search the library" });
  const list = h("div", { class: "list" });
  const chips = h("div", { class: "row filter-chips" });
  const add = async (entry) => {
    try {
      const result = await api("POST", "library/add", { file: entry.file, id: entry.id, satellite: target, new_id: target ? `${entry.id}_${slug(where)}` : undefined });
      toast(`Added ${result.id}`);
      close();
      refreshStatus();
      location.hash = `#/command/${result.id}`;
    } catch (err) { toast(err.message); }
  };
  const custom = h("button", { type: "button", class: "custom-command", onclick: () => { close(); location.hash = target ? `#/command/?satellite=${target}` : "#/command/"; } },
    icon("mdi:pencil-plus-outline", "big"),
    h("span", { class: "grow" }, h("span", { class: "title" }, "Custom command"),
      h("span", { class: "muted small" }, "Start from scratch.")));
  const matches = (entry, words) => {
    const text = [entry.title, entry.description, entry.id, entry.group, entry.family_title, entry.family_description, listensText(entry.spec)].join(" ").toLowerCase();
    return words.every((w) => text.includes(w));
  };
  // One version: what it is, and Add (or Open, once added). Its family's
  // description is said once, on the family.
  const row = (entry, iconName) => {
    const added = yours.has(entry.id);
    const description = entry.variant && entry.description === entry.family_description ? "" : entry.description;
    return h("div", { class: `item ${added ? "added" : ""}` },
      icon(iconName, "big"),
      h("div", { class: "grow" },
        h("div", { class: "title" }, entry.title, added ? h("span", { class: "badge ok", style: "margin-left:8px" }, "added") : fromHome(entry.id) ? homeHas.cloneNode(true) : null),
        h("div", { class: "muted small" }, [description, listensText(entry.spec)].filter(Boolean).join(" · ")),
        entry.needs.length ? h("div", { class: "small" }, "Then: ", entry.needs.join(" ")) : null,
        entry.error ? h("div", { class: "notice bad" }, entry.error) : null),
      added ? h("button", { type: "button", class: "link", onclick: () => add(entry) }, "Add a copy") : null,
      added ? h("button", { type: "button", onclick: () => { close(); location.hash = `#/command/${entry.id}`; } }, "Open")
        : h("button", { type: "button", class: "primary", onclick: () => add(entry) }, "Add"));
  };
  // A command with versions: a row that opens to them -- opened while
  // searching, to show which matched.
  const familyRow = (family, versions, searching) => {
    const added = family.versions.filter((e) => yours.has(e.id)).length;
    const details = h("details", { class: "family" },
      h("summary", { class: "item" },
        icon("mdi:chevron-right", "chevron"),
        icon(family.icon || iconFor(family.versions[0].spec), "big"),
        h("div", { class: "grow" },
          h("div", { class: "title" }, family.title),
          h("div", { class: "muted small" }, [...new Set(family.versions.map((e) => e.description))].join(" or "))),
        added ? h("span", { class: "badge ok" }, `${added} of ${family.versions.length} added`) : null,
        h("span", { class: "badge" }, `${family.versions.length} versions`)),
      h("div", { class: "versions" }, versions.map((e) => row(e, VARIANT_ICONS[e.variant] || iconFor(e.spec)))));
    details.open = searching || opened.has(family.key);
    details.addEventListener("toggle", () => {
      if (searching) return;
      if (details.open) opened.add(family.key); else opened.delete(family.key);
    });
    return details;
  };
  // A skill: added there, or Add.
  const skillMatches = (skill, words) => words.every((w) => [skill.name, skill.description, ...skill.examples, QUESTIONS].join(" ").toLowerCase().includes(w));
  const skillRow = (skill) => {
    const added = skillsHere.has(skill.id);
    return h("div", { class: `item ${added ? "added" : ""}` },
      icon(skill.icon, "big"),
      h("div", { class: "grow" },
        h("div", { class: "title" }, skill.name, h("span", { class: "badge", style: "margin-left:8px" }, "skill"),
          added ? h("span", { class: "badge ok", style: "margin-left:8px" }, "added") : sat && sat.home && skillData.home.includes(skill.id) ? homeHas.cloneNode(true) : null),
        h("div", { class: "muted small" }, skill.description, " · ", skill.examples.map((e) => `“${e}”`).join(" · "))),
      added ? h("button", { type: "button", onclick: () => { close(); location.hash = `#/skill/${skill.id}${target ? `?satellite=${target}` : ""}`; } }, "Open")
        : h("button", { type: "button", class: "primary", onclick: async () => {
          try {
            await api("PUT", `skills/${skill.id}`, target ? { satellites: { [target]: true } } : { home: true });
            toast(`Added ${skill.name}`); close(); route();
          } catch (err) { toast(err.message); }
        } }, "Add"));
  };
  function render() {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const shown = families.filter((f) => !group || f.group === group)
      .map((f) => [f, f.versions.filter((e) => matches(e, words))]).filter(([, versions]) => versions.length);
    const skills = !group || group === QUESTIONS ? skillData.skills.filter((sk) => skillMatches(sk, words)) : [];
    fill(chips, ...[["", null, "All"], ...groups.map(([g, i]) => [g, i, g])].map(([value, ic, label]) => {
      const count = value === QUESTIONS ? skillData.skills.filter((sk) => skillMatches(sk, words)).length
        : families.filter((f) => (!value || f.group === value) && f.versions.some((e) => matches(e, words))).length
          + (value ? 0 : skillData.skills.filter((sk) => skillMatches(sk, words)).length);
      return h("button", { type: "button", class: `filter-chip ${group === value ? "on" : ""}`, "aria-pressed": String(group === value),
        onclick: () => { group = value; render(); } }, ic ? icon(ic) : null, `${label} (${count})`);
    }));
    fill(list, ...shown.map(([family, versions]) => (family.versions.length === 1 && !family.versions[0].variant
      ? row(versions[0], iconFor(versions[0].spec))
      : familyRow(family, versions, words.length > 0))),
    ...skills.map(skillRow),
    shown.length || skills.length ? null : h("p", { class: "muted" }, "Nothing in the library matches. Start a custom command above."));
  }
  search.addEventListener("input", render);
  const close = openDialog(`Add to ${where}`, [
    custom,
    h("p", { class: "muted small", style: "margin:10px 0 4px" }, "Or from the library:", hint("Adding one copies it into your commands, where you can change anything about it. Most come in versions — on this satellite, on a device by name, in an area or floor by name — each added on its own. Skills (Questions) answer what no command takes.")),
    search, chips,
    h("div", { class: "dialog-list" }, list)]);
  render();
  search.focus();
}

async function pageLibrary() {
  location.hash = "#/home";
  newCommandDialog(null);
}

// --------------------------------------------------------------------- home

// Where a command or skill is: the Home's (null), or a satellite's key.
const placeName = (key) => {
  if (!key) return "the Home";
  const s = state.satellites.find((x) => x.key === key);
  return s ? s.name : key;
};

function satelliteIcon(s) {
  return (s && s.icon && ICONS[s.icon]) ? s.icon : "mdi:speaker";
}

// Pick an icon from `choices`; `onPick(name | null)` (null: the default).
function iconChooser(current, fallback, choices, onPick) {
  const box = h("div");
  const draw = (open, value) => {
    const choice = (name, label) => h("button", { type: "button", class: `icon-choice ${(value || "") === (name || "") ? "on" : ""}`, title: label || name,
      onclick: () => { onPick(name); draw(false, name); } }, icon(name || fallback));
    fill(box, h("button", { type: "button", class: "icon-button", title: "Change the icon", onclick: () => draw(!open, value) }, icon(value || fallback, "big")),
      open ? h("div", { class: "icon-grid" }, choice(null, "Default"), ...choices.map((n) => choice(n))) : null);
  };
  draw(false, current);
  return box;
}

// One command, as listed on the Home or a satellite's page.
function commandItem(store, id) {
  const spec = store.commands[id] || {};
  const detail = store.details[id] || {};
  const elsewhere = detail.satellite && !state.satellites.some((s) => s.key === detail.satellite);
  return h("div", { class: "item clickable", title: `${id}: ${spec.description || ""}`, onclick: () => { location.hash = `#/command/${id}`; } },
    icon(iconFor(spec), "big"),
    h("div", { class: "grow" },
      h("div", { class: "title" }, spec.title || spec.description || id, spec.disabled ? h("span", { class: "badge" }, " off") : null),
      h("div", { class: "muted small" }, listensText(spec) || spec.description || id)),
    elsewhere ? h("span", { class: "badge warn", title: `Its satellite, ${detail.satellite}, isn't here: change where it is on its page` }, "satellite gone") : null,
    asList(spec.fallback).length ? h("span", { class: "badge", title: "may be run when nothing else matches" }, asList(spec.fallback).map((f) => (f === "llm" ? "model" : f)).join(" + ")) : null,
    detail.error ? h("span", { class: "badge bad", title: detail.error }, "error") : h("span", { class: "badge accent" }, detail.does || ""));
}

function skillItem(skill, place) {
  return h("div", { class: "item clickable", title: skill.description, onclick: () => { location.hash = `#/skill/${skill.id}${place ? `?satellite=${place}` : ""}`; } },
    icon(skill.icon, "big"),
    h("div", { class: "grow" },
      h("div", { class: "title" }, skill.name),
      h("div", { class: "muted small" }, skill.examples.map((e) => `“${e}”`).join(" · "))),
    h("span", { class: "badge accent" }, "skill"));
}

// Commands and skills grouped by what they're for: your own (Custom)
// first, then the library's sections, then the skills (Questions).
// `place`: the Home (null) or a satellite's key.
function placedSections(store, skillData, place, emptyText) {
  const known = new Set(state.satellites.map((s) => s.key));
  const ids = Object.keys(store.commands || {}).filter((id) => {
    const where = (store.details[id] || {}).satellite || null;
    // A command whose satellite is gone is listed on the Home, to be moved.
    return place ? where === place : (!where || !known.has(where));
  });
  const skillIds = new Set(place ? (skillData.satellites[place] || []) : skillData.home);
  const skillsHere = skillData.skills.filter((s) => skillIds.has(s.id));
  const bySection = {};
  for (const id of ids) {
    const section = (store.details[id] || {}).library || "";
    (bySection[section] = bySection[section] || []).push(id);
  }
  const section = (title, iconName, items) => h("div", { class: "section" },
    h("h3", { class: "row" }, icon(iconName), title, h("span", { class: "badge" }, String(items.length))),
    h("div", { class: "list" }, items));
  const out = [];
  if ((bySection[""] || []).length) out.push(section("Custom", "mdi:pencil-outline", bySection[""].map((id) => commandItem(store, id))));
  for (const s of store.sections || []) {
    if ((bySection[s.file] || []).length) out.push(section(s.title, s.icon || "mdi:folder-outline", bySection[s.file].map((id) => commandItem(store, id))));
  }
  if (skillsHere.length) out.push(section("Questions", "mdi:comment-question-outline", skillsHere.map((skill) => skillItem(skill, place))));
  return out.length ? out : [h("p", { class: "muted small" }, emptyText)];
}

function satelliteTile(s, wake) {
  const own = (s.commands || []).length + (s.skills || []).length;
  const summary = [
    wakeWordName(wake, (s.wake_words_used || [])[0]),
    s.home ? (own ? `the Home's + ${own} of its own` : "the Home's") : `only its own (${own})`,
  ];
  return h("a", { class: "tile", href: `#/satellite/${s.key}`, title: `Set up and test ${s.name}` },
    h("div", { class: "row" }, icon(satelliteIcon(s), "big"), h("span", { class: "grow" }),
      h("span", { class: `dot ${s.connected ? (s.in_call ? "accent" : "ok") : ""}`, title: s.connected ? (s.in_call ? "in a call" : "connected") : "offline" })),
    h("div", { class: "title" }, s.name),
    h("div", { class: "small" }, s.area_name || s.area || h("span", { class: "muted" }, "No area")),
    h("div", { class: "muted small" }, summary.join(" · ")),
    s.debug ? h("span", { class: "badge warn" }, `debugging${s.debug.dry_run ? " · dry run" : ""}`) : null,
    s.ringing ? h("span", { class: "badge warn" }, "timer ringing") : null);
}

// Commands or names in other languages than the app's: kept, but not heard
// or shown. ``hidden``: [{name, count}] by language.
function hiddenNotice(hidden, one, many, why) {
  if (!hidden || !hidden.length) return null;
  const total = hidden.reduce((n, l) => n + l.count, 0);
  const text = hidden.length === 1
    ? `${total} ${total === 1 ? one : many} in ${hidden[0].name} ${total === 1 ? "is" : "are"} hidden.`
    : `${total} ${many} in other languages are hidden: ${hidden.map((l) => `${l.count} ${l.name}`).join(", ")}.`;
  return h("div", { class: "muted small" }, text, hint(why));
}

async function pageHome() {
  const [store, sats, skillData, wake] = await Promise.all([
    loadStore(), loadSatellites(), api("GET", "skills"),
    api("GET", "wake_words").catch(() => ({ wake_words: [], preferred: null })),
  ]);
  const unknown = (store.unknown || {}).response;
  const unknownItem = h("div", { class: "item clickable", onclick: () => { location.hash = "#/unknown"; } },
    icon("mdi:help-circle-outline", "big"),
    h("div", { class: "grow" },
      h("div", { class: "title" }, "Unknown command"),
      h("div", { class: "muted small" }, unknown ? `Says “${unknown}”` : "What's said when nothing understood a sentence.")),
    h("span", { class: "badge accent" }, unknown ? "respond" : "default"));
  fill($main,
    h("h1", {}, "Home"),
    store.error ? h("div", { class: "notice bad" }, store.error) : null,
    hiddenNotice(store.hidden, "command", "commands",
      "A command is in the language it was made in. It comes back when the app's Language option is that language again."),
    state.integration && state.integration !== "ready" ? h("div", { class: "notice warn" }, state.integration) : null,
    h("div", { class: "tiles" }, sats.length ? sats.map((s) => satelliteTile(s, wake))
      : h("div", { class: "tile muted" }, "No satellite has connected yet.")),
    h("div", { class: "panel" },
      h("div", { class: "row" },
        heading("h2", "Commands and skills", "The Home's: they run on every satellite that takes the Home's — all do, unless one is set not to. A satellite's own are on its page.", { class: "grow", style: "margin:0" }),
        h("button", { class: "primary", onclick: () => newCommandDialog(null) }, "Add")),
      placedSections(store, skillData, null, "No commands yet: press Add.")),
    h("div", { class: "panel" }, h("div", { class: "list" }, unknownItem)),
  );
}

// ---------------------------------------------------------------- satellite

// A section of a satellite's settings: collapsed unless in `opened`, with
// what it uses in its summary.
function settingsGroup(id, title, summary, opened, ...body) {
  const group = h("details", { class: "settings-group", "data-group": id },
    h("summary", {}, icon("mdi:chevron-right", "chevron"), h("span", { class: "title" }, title), h("span", { class: "muted small" }, summary)),
    h("div", { class: "group-body" }, ...body));
  group.open = !!(opened && opened.has(id));
  return group;
}

// One satellite: what it is and how it listens and speaks, its own
// commands and skills, and testing it. `opened`: the settings sections left
// open (after a save).
async function pageSatellite(key, opened) {
  const [sats, voices, wake, store, skillData] = await Promise.all([
    loadSatellites(),
    api("GET", "voices").catch(() => ({ stt: [], tts: [], media_players: [] })),
    api("GET", "wake_words").catch(() => ({ wake_words: [], preferred: null })),
    loadStore(),
    api("GET", "skills"),
  ]);
  const s = sats.find((x) => x.key === key);
  if (!s) { fill($main, h("a", { href: "#/home" }, "← Home"), errorBox(`No satellite ${key}`)); return; }
  const areas = await areaLabels();
  const settings = (state.status && state.status.settings) || {};
  const label = (value) => value || "built-in";

  // What it is.
  let chosenIcon = s.icon || null;
  const iconBox = iconChooser(s.icon, "mdi:speaker", SATELLITE_ICONS, (name) => { chosenIcon = name; });
  const name = h("input", { type: "text", value: s.name });
  const area = h("select", {}, h("option", { value: "" }, "No area"), ...Object.entries(areas).map(([id, text]) => h("option", { value: id, selected: s.area === id }, text)));
  const own = s.voice || {};
  const used = s.voice_used || {};
  const stt = voiceSelect("stt", voices.stt, own.stt, label(settings.stt));
  const tts = voiceSelect("tts", voices.tts, own.tts, label(settings.tts));
  const sttLanguage = languagePicker(voices.stt, stt, own.stt_language, settings.stt_language || "automatic", settings.stt);
  const ttsLanguage = languagePicker(voices.tts, tts, own.tts_language, settings.tts_language || "automatic", settings.tts);
  const voice = voicePicker(tts, own.tts_voice, settings.tts_voice || "", ttsLanguage);
  const sttAudio = sttAudioSelect(own.stt_audio, settings.stt_audio || "agc");
  const sttRuns = sttRunsSelect(own.stt_always, !!settings.stt_always);
  const speakOn = h("select", {},
    h("option", { value: "", selected: !own.speak_on }, s.device_media_player ? `This satellite (${s.device_media_player})` : "This satellite (its media player isn't in Home Assistant)"),
    ...voices.media_players.filter((m) => m.entity_id !== s.device_media_player).map((m) => h("option", { value: m.entity_id, selected: own.speak_on === m.entity_id }, `${m.name} (${m.entity_id})`)));
  const found = s.media_player_picked ? null : s.media_player;
  const player = h("select", {},
    h("option", { value: "" }, found ? `Its Sendspin player (${found})` : "Its Sendspin player (not found)"),
    ...(s.music_players || []).map((m) => h("option", { value: m.entity_id, selected: s.media_player_picked && s.media_player === m.entity_id }, `${m.name} (${m.entity_id})`)));
  // Music, Input and Output: collapsed, each saying in its summary what it uses.
  const playerName = (id) => ((s.music_players || []).find((m) => m.entity_id === id) || {}).name || id;
  const groups = [
    settingsGroup("music", "Music", s.media_player ? playerName(s.media_player) : "no player found", opened,
      h("div", { class: "row" }, field("Player", player, "Where music asked for here plays. Its Sendspin player in Music Assistant unless you pick another."))),
    settingsGroup("input", "Input", [used.stt || "Parakeet TDT", used.stt_language, used.stt && used.stt_audio !== "agc" ? (used.stt_audio === "aec_boost" ? "AEC, boosted" : "AEC") : null, used.stt_always ? "always" : null].filter(Boolean).join(" · "), opened,
      h("div", { class: "row" }, field("Speech-to-text", stt, "Commands are matched on the audio first; the speech-to-text entity writes the words when they're needed: free text, example sentences, hints."), field("Language", sttLanguage),
        field("Audio", sttAudio, STT_AUDIO_HINT), field("Runs", sttRuns, STT_RUNS_HINT))),
    settingsGroup("output", "Output", [used.tts || "Piper", used.tts_language, used.tts_voice, used.speak_on ? `on ${used.speak_on}` : null].filter(Boolean).join(" · "), opened,
      h("div", { class: "row" }, field("Text-to-speech", tts), field("Language", ttsLanguage), field("Voice", voice),
        field("Speak on", speakOn, "With a text-to-speech entity: this satellite, or another media player. The acknowledgement sound stays here."))),
  ];
  const save = async () => {
    try {
      await api("PUT", `satellites/${s.key}`, {
        name: name.value, area: area.value || null, icon: chosenIcon, media_player: player.value || null,
        stt: stt.value || null, tts: tts.value || null, tts_voice: voice.value || null, speak_on: speakOn.value || null,
        stt_language: languageValue(sttLanguage, true), tts_language: languageValue(ttsLanguage, true),
        stt_audio: sttAudio.value || null, stt_always: sttRunsValue(sttRuns),
      });
      toast("Saved"); pageSatellite(key, new Set(groups.filter((g) => g.open).map((g) => g.dataset.group)));
    } catch (err) { toast(err.message); }
  };

  // Its commands and skills.
  const takesHome = h("input", { type: "checkbox", checked: s.home, onchange: async (e) => {
    try { await api("PUT", `satellites/${s.key}`, { home: e.target.checked }); toast(e.target.checked ? "Takes the Home's" : "Only its own"); pageSatellite(key); }
    catch (err) { toast(err.message); }
  } });

  // Testing it: a sentence typed, as if said here.
  const input = h("input", { type: "text", class: "grow", placeholder: "turn on the lights" });
  const run = h("input", { type: "checkbox" });
  const speak = h("input", { type: "checkbox" });
  const out = h("div");
  async function go() {
    fill(out, h("p", { class: "muted" }, speak.checked ? "Synthesizing and recognizing…" : "Recognizing…"));
    try {
      const record = await api("POST", "try", { text: input.value, satellite: s.key, run_actions: run.checked, speak: speak.checked });
      fill(out, h("div", { class: "panel inset" }, sessionGlance(record), pipeline(record), h("p", { class: "small" }, h("a", { href: `#/session/${record.id}` }, "Open its session"))));
    } catch (err) { fill(out, errorBox(err)); }
  }
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });

  fill($main,
    h("div", { class: "row" },
      h("a", { href: "#/home" }, "← Home"),
      icon(satelliteIcon(s), "big"),
      h("h1", { class: "grow", style: "margin:0" }, s.name),
      h("span", { class: `badge ${s.connected ? "ok" : ""}` }, s.connected ? (s.in_call ? "in a call" : "connected") : "offline"),
      s.debug ? h("span", { class: "badge warn" }, `debugging${s.debug.dry_run ? " · dry run" : ""}`) : null,
      h("a", { href: `#/sessions?satellite=${s.key}` }, "Its sessions")),
    s.ringing ? h("div", { class: "notice warn" }, "A timer is ringing.") : null,
    h("div", { class: "panel" },
      h("h2", {}, "Settings"),
      h("div", { class: "row", style: "align-items:flex-start" },
        field("Icon", iconBox),
        h("div", { class: "row grow" }, field("Name", name),
          field("Area", area, "What “the lights” means here. With the Voice Call integration, it is the area of its device in Home Assistant, and can be changed in either place."))),
      h("div", { class: "settings-groups" }, ...groups),
      h("div", { class: "row" }, h("button", { class: "primary", onclick: save }, "Save")),
      h("details", { class: "small", style: "margin-top:8px" }, h("summary", {}, "About it"),
        h("div", { class: "kv", style: "margin-top:6px" },
          h("div", {}, "Id"), h("div", {}, h("code", {}, s.key)),
          h("div", {}, "Satellite"), h("div", {}, s.esphome_name || "—", s.host ? ` at ${s.host}` : ""),
          h("div", {}, "In Home Assistant"), h("div", {}, s.device_id ? ["device ", h("code", {}, s.device_id), s.timer_list ? [" · timers ", h("code", {}, s.timer_list)] : null] : "not registered (needs the Voice Call integration)"),
          h("div", {}, "Floor"), h("div", {}, s.floor_name || "—"),
          h("div", {}, "Wake word"), h("div", {}, wakeWordName(wake, (s.wake_words_used || [])[0]), s.wake_words ? " (its own)" : " (the preferred one)",
            (s.wake_words_need_firmware || []).length ? h("span", { class: "badge bad" }, `${wakeWordName(wake, s.wake_words_need_firmware[0])} needs newer firmware`) : null,
            " · ", h("a", { href: "#/wake" }, "change")),
          s.timers && s.timers.length ? [h("div", {}, "Timers"), h("div", {}, s.timers.map((t) => `${t.name || "unnamed"} (${t.status}, ${Math.round(t.remaining)} s left)`).join(" · "))] : null))),
    h("div", { class: "panel" },
      h("div", { class: "row" },
        h("h2", { class: "grow", style: "margin:0" }, "Commands and skills"),
        h("button", { class: "primary", onclick: () => newCommandDialog(s.key) }, "Add")),
      h("label", {}, takesHome, "Include the Home's", hint("Its own, below, and the Home's commands and skills. Where its own and the Home's take the same sentence, its own goes first: duplicate one of the Home's here to do something else on this satellite.")),
      placedSections(store, skillData, s.key, "None of its own yet.")),
    h("div", { class: "panel" },
      heading("h2", "Test it", "What you type is recognized like a command said here, after the wake word, as if every word had been heard exactly as typed: no speech recognition."),
      h("div", { class: "row" }, input, h("button", { class: "primary", onclick: go }, "Test")),
      h("label", { class: "small" }, run, "Really run it", hint("Otherwise a dry run: nothing happens in Home Assistant, and the conversation agent isn't asked.")),
      h("label", { class: "small" }, speak, "Say it first", hint("Spoken by the app's voice and recognized from the audio, to test speech recognition too. Synthesized speech is cleaner than a room, so treat a match then as necessary, not sufficient.")),
      out,
      liveSection(s),
      debugSection(s)),
  );
}

// ----------------------------------------------------------------- skills

// A skill's settings: an editor for each (by skill id), from the settings
// as GET /api/skills gives them -- { el, value() }, value() the settings to
// PUT, in full.
const SKILL_SETTINGS = { units: unitsEditor, spelling: spellingEditor, dates: holidaysEditor, clock: clockEditor, arithmetic: precisionEditor };

const MEASURE_NAMES = { length: "Length", area: "Area", volume: "Volume", weight: "Weight", time: "Time", data: "Data", temperature: "Temperature" };
const PRECISION_HINT = "Significant digits in an answer that isn't a whole number: 8.047 kilometers at 4, 8.05 at 3. Whole numbers are said in full.";
const namesText = (list) => asList(list).join(", ");
const namesList = (text) => String(text || "").split(",").map((s) => s.trim()).filter(Boolean);

// A table of things to edit: a row of inputs each, removable, and added to.
// `columns`: [label, (item) => control]; on a phone, each row stacks, its
// controls labelled.
function editTable(items, columns, blank, addLabel, rowClass) {
  const body = h("div");
  function render() {
    fill(body,
      items.length ? h("table", { class: "edit-table" },
        h("thead", {}, h("tr", {}, ...columns.map(([label]) => h("th", {}, label)), h("th", {}))),
        h("tbody", {}, ...items.map((item, i) => h("tr", { class: rowClass ? rowClass(item) : null },
          ...columns.map(([label, control]) => h("td", { "data-label": label }, control(item))),
          h("td", { class: "remove" }, h("button", { type: "button", class: "link", "aria-label": `Remove ${item.name || item.asked || "this row"}`, title: "Remove", onclick: () => { items.splice(i, 1); render(); } }, "×")))))) : null,
      h("button", { type: "button", onclick: () => { items.push(blank()); render(); body.querySelector("tbody tr:last-child input")?.focus(); } }, addLabel));
  }
  render();
  return { el: body, render };
}

// A text input bound to item[key]; `example`, a placeholder for a row just
// added (on one listed, it would read as its value).
function boundText(item, key, attrs, example) {
  return h("input", { type: "text", value: item[key] ?? "", placeholder: item.fresh ? example : null, oninput: (e) => { item[key] = e.target.value; }, ...attrs });
}

// A number input bound to item[key] (empty: null).
function boundNumber(item, key, attrs) {
  return h("input", { type: "number", step: "any", value: item[key] ?? "", oninput: (e) => { item[key] = e.target.value === "" ? null : Number(e.target.value); }, ...attrs });
}

// What was taken out of the built-in ones, to put back (`add`).
function putBack(removed, add, what) {
  const line = h("p", { class: "small muted" });
  function show() {
    fill(line, removed.length ? ["Taken out: ", ...removed.flatMap((item, i) => [i ? ", " : "",
      h("button", { type: "button", class: "link", title: `Put ${item.name} back`, onclick: () => { removed.splice(i, 1); add(item); show(); } }, item.name)]),
    hint(`Built-in ${what} you took out. Press one to put it back.`)] : null);
  }
  show();
  return line;
}

function precisionField(settings) {
  const input = defaultNumber(settings.digits === 4 ? null : settings.digits, 4, 1, 12);
  input.min = 1;
  return { el: field("Precision", input, PRECISION_HINT), value: () => numberOrNull(input) };
}

function precisionEditor(settings) {
  const precision = precisionField(settings);
  return { el: precision.el, value: () => ({ digits: precision.value() }) };
}

function clockEditor(settings) {
  const select = h("select", { style: "max-width:240px" },
    h("option", { value: "12", selected: settings.hours !== 24 }, "12-hour (4:45 PM)"),
    h("option", { value: "24", selected: settings.hours === 24 }, "24-hour (16:45)"));
  return { el: field("Clock", select), value: () => ({ hours: Number(select.value) }) };
}

function spellingEditor(settings) {
  const words = Object.entries(settings.words || {}).map(([asked, spelled]) => ({ asked, spelled }));
  const table = editTable(words, [
    ["Asked", (w) => boundText(w, "asked", { "aria-label": "Asked" }, "color")],
    ["Spelled", (w) => boundText(w, "spelled", { "aria-label": "Spelled" }, "colour")],
  ], () => ({ asked: "", spelled: "", fresh: true }), "Add a word");
  return {
    el: h("div", {}, heading("h3", "Spelled your way", "Asked to spell one of these — a word or a phrase, as the speech-to-text writes it — it spells it your way: colour for color, a name the speech-to-text writes differently, “my last name”."), table.el),
    value: () => ({ words: Object.fromEntries(words.filter((w) => w.asked.trim() || w.spelled.trim()).map((w) => [w.asked, w.spelled])) }),
  };
}

function holidaysEditor(settings) {
  const holidays = clone(settings.holidays || []).map((x) => ({ ...x, also: namesText(x.also) }));
  const removed = clone(settings.removed || []).map((x) => ({ ...x, also: namesText(x.also) }));
  const nextText = (x) => x.next ? new Date(`${x.next}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }) : "";
  const table = editTable(holidays, [
    ["Name", (x) => boundText(x, "name", { "aria-label": "Name" }, "My birthday")],
    ["Also called", (x) => boundText(x, "also", { "aria-label": "Also called" }, "my bday")],
    ["When", (x) => boundText(x, "when", { "aria-label": "When" }, "June 3")],
    ["Next", (x) => h("span", { class: "muted small nowrap" }, nextText(x))],
  ], () => ({ name: "", also: "", when: "", fresh: true }), "Add a holiday", (x) => x.built_in === false ? "yours" : null);
  return {
    el: h("div", {},
      heading("h3", "Holidays", "What “when is …” and “how many days until …” know. When: a date (December 25), a weekday in a month (fourth Thursday of November, last Monday of May), Easter or days from it (2 days before Easter), or a date with its year, for one that happens once (June 20 2027). Yours are marked at the left."),
      table.el, putBack(removed, (x) => { holidays.push(x); table.render(); }, "holidays")),
    value: () => ({ holidays: holidays.filter((x) => x.name.trim() || x.when.trim()).map((x) => ({ name: x.name, when: x.when, also: namesList(x.also) })) }),
  };
}

// Units, grouped by what they measure (collapsed), each group a table;
// volumes US customary or imperial (a preset of their sizes).
function unitsEditor(settings) {
  const measures = settings.measures || {};
  const presets = settings.presets || {};
  let volumes = settings.volumes || "us";
  const byMeasure = Object.fromEntries(Object.keys(measures).map((m) => [m, []]));
  for (const u of clone(settings.units || [])) (byMeasure[u.measures] ||= []).push({ ...u, also: namesText(u.also) });
  const removed = clone(settings.removed || []).map((u) => ({ ...u, also: namesText(u.also) }));
  const precision = precisionField(settings);
  const tables = {};
  const groups = Object.entries(measures).map(([measure, base]) => {
    const units = byMeasure[measure];
    const summary = h("span", { class: "muted small" });
    const columns = base ? [
      ["Name", (u) => boundText(u, "name", { "aria-label": "Name" }, "nautical mile")],
      // Empty: the usual plural, shown.
      ["Plural", (u) => boundText(u, "plural", { "aria-label": "Plural", placeholder: u.plural_of || null })],
      ["Also called", (u) => boundText(u, "also", { "aria-label": "Also called" }, "nmi")],
      [`Size (${base}s)`, (u) => boundNumber(u, "size", { "aria-label": `Size in ${base}s`, min: 0 })],
    ] : [
      ["Name", (u) => boundText(u, "name", { "aria-label": "Name" }, "Rankine")],
      ["Also called", (u) => boundText(u, "also", { "aria-label": "Also called" }, "R")],
      ["Water freezes", (u) => boundNumber(u, "freezes", { "aria-label": "Water freezes at" })],
      ["Water boils", (u) => boundNumber(u, "boils", { "aria-label": "Water boils at" })],
    ];
    const table = editTable(units, columns, () => ({ name: "", measures: measure, plural: "", also: "", size: null, freezes: null, boils: null, fresh: true }),
      "Add a unit", (u) => u.built_in === false ? "yours" : null);
    const show = () => {
      const names = units.map((u) => u.name).filter(Boolean).join(", ") || "none";
      summary.textContent = measure === "volume" && presets[volumes] ? `${presets[volumes].name}: ${names}` : names;
    };
    tables[measure] = () => { table.render(); show(); };
    table.el.addEventListener("click", (e) => { if (e.target.closest("button")) show(); });
    table.el.addEventListener("input", show);
    show();
    // Volumes: choosing measures sets the sizes of those it has.
    const preset = measure === "volume" && Object.keys(presets).length ? field("Measures",
      h("select", { style: "max-width:240px", onchange: (e) => {
        volumes = e.target.value;
        const sizes = presets[volumes].sizes;
        for (const u of [...units, ...removed]) {
          const size = sizes[u.name.trim().toLowerCase()];
          if (u.measures === "volume" && size !== undefined) u.size = size;
        }
        tables.volume();
      } }, ...Object.entries(presets).map(([id, p]) => h("option", { value: id, selected: id === volumes }, p.name))),
      "Sets the sizes of the fluid ounce, pint, quart and gallon, and of the cup and spoons (imperial's and metric's are 250, 15 and 5 mL, as UK and German recipes use). “An imperial pint” or “a US cup” in a question is that one, whichever is set.") : null;
    return h("details", { class: "settings-group" },
      h("summary", {}, icon("mdi:chevron-right", "chevron"), h("span", { class: "title" }, MEASURE_NAMES[measure] || measure), summary),
      h("div", { class: "group-body" }, preset, table.el));
  });
  return {
    el: h("div", {},
      precision.el,
      heading("h3", "Units", "What it converts between. A unit's size is in its measure's base unit: a cup is 236.6 milliliters. A temperature scale is set by where water freezes and boils on it. Volumes come in measures (US customary, imperial ...): Measures, under Volume. Yours are marked at the left."),
      h("div", { class: "settings-groups" }, ...groups),
      putBack(removed, (u) => { (byMeasure[u.measures] ||= []).push(u); tables[u.measures]?.(); }, "units")),
    value: () => ({
      digits: precision.value(),
      volumes,
      units: Object.values(byMeasure).flat().filter((u) => u.name.trim() || u.size != null || u.freezes != null).map((u) => (u.measures === "temperature"
        ? { name: u.name, measures: u.measures, freezes: u.freezes, boils: u.boils, also: namesList(u.also) }
        : { name: u.name, measures: u.measures, size: u.size, plural: u.plural || "", also: namesList(u.also) })),
    }),
  };
}

// A skill: what it answers, and how -- its level, and a response of your own
// for any level. Opened from the Home or a satellite (?satellite=), it can be
// taken off there; it's added with Add on the Home or a satellite's page.
async function pageSkill(id, query) {
  const place = new URLSearchParams(query || "").get("satellite");
  const [data] = await Promise.all([api("GET", "skills"), loadSatellites()]);
  const skill = data.skills.find((x) => x.id === id);
  const back = place ? `#/satellite/${place}` : "#/home";
  if (!skill) { fill($main, h("a", { href: back }, "← Back"), errorBox(`No skill ${id}`)); return; }
  const there = place ? (data.satellites[place] || []).includes(id) : data.home.includes(id);
  const saved = skill.responses || {};
  let verbosity = saved.verbosity || "";
  let response = typeof saved.response === "string" || !saved.response ? (saved.response || "") : { ...saved.response };
  const example = skill.example || { levels: {}, question: skill.examples[0] };
  const levels = example.levels || {};
  const LABELS = { brief: "Brief: the answer", specific: "Specific: the answer as a sentence", verbose: "Verbose: the question said back with it" };

  const settings = SKILL_SETTINGS[skill.id] ? SKILL_SETTINGS[skill.id](skill.settings || {}) : null;

  const editor = h("div");
  function render() {
    const select = h("select", { style: "max-width:360px", onchange: (e) => { verbosity = e.target.value; } },
      h("option", { value: "", selected: !verbosity }, "Default (specific)"),
      ...data.levels.map((level) => h("option", { value: level, selected: verbosity === level }, LABELS[level] || level)));
    const byLevel = typeof response === "object";
    const toggle = h("button", { type: "button", class: "link", onclick: () => {
      if (byLevel) {
        const only = Object.keys(response).filter((k) => response[k]);
        if (only.length > 1 && !confirm("Keep only the specific one, for every level?")) return;
        response = response.specific || response[only[0]] || "";
      } else {
        response = response ? { specific: response } : {};
      }
      render();
    } }, byLevel ? "One for every level" : "One for each level");
    const boxes = byLevel
      ? h("div", { class: "grid2" }, ...data.levels.map((level) => {
        const area = h("textarea", { class: "short", placeholder: levels[level] || "", oninput: (e) => { if (e.target.value) response[level] = e.target.value; else delete response[level]; } });
        area.value = response[level] || "";
        return field(level[0].toUpperCase() + level.slice(1), area);
      }))
      : (() => {
        const area = h("textarea", { class: "short", placeholder: levels.specific || "", oninput: (e) => { response = e.target.value; } });
        area.value = response || "";
        return area;
      })();
    fill(editor,
      field("Level", select, `A question always answers. For “${example.question || skill.examples[0]}”: ${data.levels.map((level) => `${level} “${levels[level] || "—"}”`).join(", ")}.`),
      h("div", { class: "field" }, h("div", { class: "label-row" }, h("span", { class: "label" }, "Template ", toggle), hint("Empty: the built-in sentence (shown greyed, for the example). A level without one says the closest written (less first). A template, rendered by Home Assistant, sees answer (“96”), question (as asked), response (the built-in sentence at this level), satellite and speaker. If it can't be rendered, the built-in sentence is said.")), boxes));
  }
  render();
  async function save() {
    try {
      const body = { verbosity: verbosity || null, response: typeof response === "object" ? response : response.trim() };
      if (settings) body.settings = settings.value();
      await api("PUT", `skills/${id}`, body);
      toast("Saved"); pageSkill(id, query);
    } catch (err) { toast(err.message); }
  }
  async function restore() {
    if (!confirm(`Put ${skill.name}'s settings back as built in? Yours are lost.`)) return;
    try {
      await api("PUT", `skills/${id}`, { settings: null });
      toast("Restored"); pageSkill(id, query);
    } catch (err) { toast(err.message); }
  }
  async function takeOff() {
    if (!confirm(`Take ${skill.name} off ${placeName(place)}?`)) return;
    try {
      await api("PUT", `skills/${id}`, place ? { satellites: { [place]: false } } : { home: false });
      toast("Taken off"); location.hash = back;
    } catch (err) { toast(err.message); }
  }
  fill($main,
    h("div", { class: "row" },
      h("a", { href: back }, `← ${place ? placeName(place) : "Home"}`),
      icon(skill.icon, "big"),
      h("h1", { class: "grow", style: "margin:0" }, skill.name),
      there ? h("button", { class: "danger", onclick: takeOff }, place ? `Take off ${placeName(place)}` : "Take off the Home") : null,
      h("button", { class: "primary", onclick: save }, "Save")),
    there ? null : h("div", { class: "notice info" }, `${skill.name} isn't on ${placeName(place)}: add it with Add on ${place ? "its page" : "the Home page"}.`),
    h("div", { class: "panel" },
      h("p", {}, skill.description),
      h("p", { class: "small" }, "Try: ", skill.examples.map((e) => `“${e}”`).join(" · "), hint("A skill answers a question no command takes, exactly and at once, without an LLM: it parses the whole question and computes the answer, or leaves it to the next step. Skills answer before the steps that run when nothing else matches (meaning, the model). How it answers is the same wherever it's added.")),
    ),
    settings ? h("div", { class: "panel" }, h("h2", {}, "Settings"),
      skill.settings_error ? h("div", { class: "notice warn" }, "Yours can't be used, so the built-in ones are: ", skill.settings_error) : null,
      settings.el,
      h("div", { class: "row" }, h("span", { class: "grow" }),
        skill.settings_set ? h("button", { onclick: restore }, "Restore built-in") : null,
        h("button", { class: "primary", onclick: save }, "Save"))) : null,
    h("div", { class: "panel" }, h("h2", {}, "Responses"), editor,
      h("div", { class: "row" }, h("span", { class: "grow" }), h("button", { class: "primary", onclick: save }, "Save"))),
  );
}

// The Home's speech-to-text, text-to-speech and built-in speech model, which
// a satellite uses unless it has its own.
// Drawn at once, filled in when Home Assistant's voices have been listed.
function speechPanel() {
  const panel = h("div", { class: "panel" }, heading("h2", "Listening and speaking"), h("p", { class: "muted small" }, "Loading…"));
  api("GET", "voices").catch(() => ({ stt: [], tts: [], media_players: [] })).then((voices) => fillSpeechPanel(panel, voices));
  return panel;
}

function fillSpeechPanel(panel, voices) {
  const settings = (state.status && state.status.settings) || {};
  const globalStt = voiceSelect("stt", voices.stt, settings.stt);
  const globalTts = voiceSelect("tts", voices.tts, settings.tts);
  const globalSttLanguage = languagePicker(voices.stt, globalStt, settings.stt_language);
  const globalTtsLanguage = languagePicker(voices.tts, globalTts, settings.tts_language);
  const globalVoice = voicePicker(globalTts, settings.tts_voice, undefined, globalTtsLanguage);
  const globalSttAudio = sttAudioSelect(settings.stt_audio);
  const globalSttRuns = sttRunsSelect(!!settings.stt_always);
  const models = (state.status && state.status.tdt_models) || [];
  const tdtModel = h("select", {}, models.map((m) => h("option", { value: m.id, selected: ((state.status && state.status.tdt_choice) || "own") === m.id, disabled: !m.available },
    `${m.name} — ${m.detail}${m.available ? "" : " (not installed)"}`)));
  fill(panel,
    heading("h2", "Listening and speaking", "Home Assistant's speech-to-text and text-to-speech, in place of the built-in models, for every satellite (each can choose its own on its page). Commands are always matched on the device's audio first; the speech-to-text entity writes what is otherwise read by the TDT model: example sentences, free text (“play …”), and hints. A text-to-speech entity speaks on the satellite's own media player, or on another one picked per satellite."),
    voices.error ? h("div", { class: "notice warn" }, voices.error) : null,
    field("Built-in speech model", tdtModel, "What writes example sentences, free text and hints when no speech-to-text entity is picked. The multilingual model understands 25 European languages and finds the language itself, at about three times the time and 600 MB more memory. Commands are matched first by the English model either way."),
    state.status && state.status.tdt_status ? h("div", { class: "notice info" }, state.status.tdt_status) : null,
    h("div", { class: "row" },
      field("Speech-to-text", globalStt), field("Language", globalSttLanguage), field("Audio", globalSttAudio, STT_AUDIO_HINT), field("Runs", globalSttRuns, STT_RUNS_HINT),
      field("Text-to-speech", globalTts), field("Language", globalTtsLanguage), field("Voice", globalVoice),
      h("button", { class: "primary", onclick: async () => {
        try {
          await api("PUT", "settings", {
            stt: voiceSelectValue(globalStt), tts: voiceSelectValue(globalTts), tts_voice: globalVoice.value === "default" ? null : globalVoice.value || null,
            stt_language: languageValue(globalSttLanguage), tts_language: languageValue(globalTtsLanguage), tdt_model: tdtModel.value,
            stt_audio: globalSttAudio.value === "agc" ? null : globalSttAudio.value,
            stt_always: sttRunsValue(globalSttRuns),
          });
          await refreshStatus(); toast("Saved"); pageSettings();
        } catch (err) { toast(err.message); }
      } }, "Save")));
}

// ------------------------------------------------------------------- names

async function pageNames() {
  const store = await loadStore();
  const kinds = [["entities", "entity", "Devices"], ["areas", "area", "Areas"], ["floors", "floor", "Floors"]];
  const sections = kinds.map(([plural, kind, title]) => {
    const names = store.names[plural] || {};
    const form = h("div");
    function edit(itemId, entry, suggestion) {
      const name = h("input", { type: "text", value: entry ? (typeof entry === "string" ? entry : entry.name) : (suggestion ? suggestion.name : ""), placeholder: "name" });
      let aliases = entry && typeof entry !== "string" ? asList(entry.aliases) : (suggestion ? asList(suggestion.aliases) : []);
      fill(form, h("div", { class: "panel" },
        h("div", { class: "row" }, h("b", {}, itemId), suggestion ? h("span", { class: "muted small" }, "suggested from Home Assistant — edit before saving") : null),
        h("div", { class: "grid2" },
          field("Name", name),
          field("Also called", chipsInput(aliases, (v) => { aliases = v; }, "Echo Bee"), "Other names, and spellings the speech model uses (it writes ecobee as “Echo Bee”).")),
        h("div", { class: "row" },
          h("button", { class: "primary", onclick: async () => {
            try { await api("PUT", `names/${plural}/${encodeURIComponent(itemId)}`, { name: name.value, aliases }); toast("Saved"); pageNames(); refreshStatus(); }
            catch (err) { form.append(errorBox(err)); }
          } }, "Save"),
          h("button", { onclick: () => fill(form, ) }, "Cancel"))));
    }
    return h("div", { class: "panel" },
      h("h2", {}, title),
      Object.keys(names).length ? h("table", {}, h("tr", {}, h("th", {}, "Id"), h("th", {}, "Name"), h("th", {}, "Also called"), h("th", {})),
        ...Object.entries(names).map(([itemId, entry]) => h("tr", {},
          h("td", {}, h("code", {}, itemId)),
          h("td", {}, typeof entry === "string" ? entry : entry.name),
          h("td", {}, typeof entry === "string" ? "" : asList(entry.aliases).join(", ")),
          h("td", { style: "text-align:right; white-space:nowrap" },
            h("button", { class: "link", onclick: () => edit(itemId, entry) }, "edit"),
            h("button", { class: "link", onclick: async () => { if (!confirm(`Remove the name for ${itemId}?`)) return; try { await api("DELETE", `names/${plural}/${encodeURIComponent(itemId)}`); pageNames(); refreshStatus(); } catch (err) { toast(err.message); } } }, "remove"))))) :
        h("p", { class: "muted" }, "None yet."),
      field("Add", hassPicker(kind, (item) => edit(item.id, null, item)), "Pick from Home Assistant; its name and aliases are offered as a starting point."),
      form);
  });

  const listForm = h("div");
  function editList(name, values) {
    const nameInput = h("input", { type: "text", value: name || "", placeholder: "colors" });
    const ta = h("textarea", { class: "code", placeholder: "red: red, crimson\nblue" });
    ta.value = values ? valuesToText(values) : "";
    fill(listForm, h("div", { class: "panel" },
      field("List name", nameInput), field("Values", ta, "One value per line: value: phrase, other phrase."),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: async () => {
          try { await api("PUT", `lists/${encodeURIComponent(slug(nameInput.value))}`, { values: textToValues(ta.value) }); toast("Saved"); pageNames(); }
          catch (err) { listForm.append(errorBox(err)); }
        } }, "Save"), h("button", { onclick: () => fill(listForm, ) }, "Cancel"))));
  }

  fill($main, 
    heading("h1", "Names", "What things are called when spoken. Each name points at one Home Assistant id. Nothing is taken from Home Assistant automatically: its names and aliases are offered as suggestions."),
    hiddenNotice(store.hidden_names, "name", "names",
      "A name is in the language it was given in. Name a thing here for this language too; its other names come back when the app's Language option is theirs again."),
    ...sections,
    h("div", { class: "panel" }, heading("h2", "Value lists", "Shared lists a command's value slots can use: colors, modes, presets. The built-in ones come with the language; editing one makes it yours, and removing yours brings the built-in back."),
      hiddenNotice(store.hidden_lists, "list", "lists",
        "A list's phrases are in the language they were given in. They come back when the app's Language option is theirs again."),
      h("div", { class: "list" }, ...[...new Set([...Object.keys(store.lists), ...Object.keys(store.builtin_lists || {})])].sort().map((name) => {
        const own = store.lists[name];
        const builtin = (store.builtin_lists || {})[name];
        const values = own || builtin;
        return h("div", { class: "item" },
          h("div", { class: "grow" }, h("b", {}, name), " ", builtin ? h("span", { class: "badge" }, own ? "yours" : "built-in") : null,
            h("div", { class: "muted small" }, valuesToText(values).split("\n").join(" · "))),
          h("button", { class: "link", onclick: () => editList(name, values) }, "edit"),
          own ? h("button", { class: "link", onclick: async () => { try { await api("DELETE", `lists/${encodeURIComponent(name)}`); pageNames(); } catch (err) { toast(err.message); } } }, builtin ? "use built-in" : "remove") : null);
      })),
      h("button", { onclick: () => editList("", null) }, "Add a list"), listForm),
  );
}

// -------------------------------------------------------------- satellites

// Home Assistant STT and TTS entities: a select of them, with the built-in
// model and (for one satellite) "Default" -- every satellite's -- first.
function voiceSelect(kind, entities, value, inherit) {
  const builtin = kind === "stt" ? "Built-in (Parakeet TDT)" : "Built-in (Piper)";
  const options = [];
  if (inherit !== undefined) options.push(h("option", { value: "", selected: value === undefined }, `Default (${inherit})`));
  options.push(h("option", { value: "builtin", selected: inherit === undefined ? !value : value === "builtin" }, builtin));
  for (const e of entities) options.push(h("option", { value: e.entity_id, selected: value === e.entity_id }, e.entity_id));
  if (value && value !== "builtin" && !entities.some((e) => e.entity_id === value)) options.push(h("option", { value, selected: true }, `${value} (not found)`));
  return h("select", {}, ...options);
}

// The capture a speech-to-text entity hears; with `inherit`, "Default" first.
const STT_AUDIO_HINT = "What the speech-to-text entity hears. AGC: the microphone with automatic gain, as commands are matched. AEC: echo-cancelled, with the satellite's own playback (its music, its voice) taken out — about 25 dB quieter, as captured, or boosted to a normal level for an entity that doesn't level it itself. Sound from other speakers is in both.";
const STT_RUNS_HINT = "When needed: asked after you finish speaking, and only when the words are needed — free text, example sentences, hints. Always: it hears every command as you say it, so every session has its transcript, and one that is needed is ready sooner. Every command is sent to it.";
// When the speech-to-text entity runs: true (always), false (when needed);
// `inherit`, every satellite's, for one satellite's Default.
function sttRunsSelect(value, inherit) {
  const name = (always) => (always ? "Always, as you speak" : "When needed");
  return h("select", {},
    inherit !== undefined ? h("option", { value: "", selected: value === undefined || value === null }, `Default (${name(inherit).toLowerCase()})`) : null,
    h("option", { value: "false", selected: inherit === undefined ? !value : value === false }, name(false)),
    h("option", { value: "true", selected: value === true }, name(true)));
}
const sttRunsValue = (select) => (select.value === "" ? null : select.value === "true");

function sttAudioSelect(value, inherit) {
  const name = { agc: "AGC", aec: "AEC (echo-cancelled)", aec_boost: "AEC, boosted" };
  return h("select", {},
    inherit !== undefined ? h("option", { value: "", selected: !value }, `Default (${name[inherit] || name.agc})`) : null,
    ...Object.entries(name).map(([id, label]) => h("option", { value: id, selected: inherit === undefined ? (value || "agc") === id : value === id }, label)));
}

function voiceSelectValue(select, inheritable) {
  return select.value === "builtin" && !inheritable ? null : select.value || null;
}

// The language an STT or TTS entity is asked in: every one it supports,
// "automatic" (its best match for English) first, and for one satellite
// "Default" (every satellite's). Follows the entity picked in `entitySelect`,
// whose value may be "" (the default: `inheritEntity`).
function languagePicker(entities, entitySelect, value, inherit, inheritEntity) {
  const select = h("select", {});
  function load() {
    const entityId = entitySelect.value || inheritEntity || "";
    const entity = entities.find((e) => e.entity_id === entityId);
    const options = [];
    if (inherit !== undefined) options.push(h("option", { value: "", selected: value === undefined }, `Default${inherit ? ` (${inherit})` : ""}`));
    options.push(h("option", { value: "auto", selected: inherit === undefined ? !value : value === "auto" }, entity && entity.automatic ? `Automatic (${entity.automatic})` : "Automatic"));
    for (const code of (entity && entity.languages) || []) options.push(h("option", { value: code, selected: value === code }, code));
    if (value && value !== "auto" && !(entity && entity.languages.includes(value))) options.push(h("option", { value, selected: true }, `${value} (not supported)`));
    fill(select, options);
    select.disabled = !entity;
  }
  entitySelect.addEventListener("change", () => { value = undefined; load(); });
  load();
  return select;
}

// What a language select means: "" (the default), null (automatic) or a code.
function languageValue(select, inheritable) {
  if (select.value === "auto") return inheritable ? "auto" : null;
  return select.value || null;
}

// A TTS entity's voices, loaded when it (or its language) is picked.
function voicePicker(ttsSelect, value, inherit, languageSelect) {
  const select = h("select", {});
  async function load() {
    const tts = ttsSelect.value;
    const options = [];
    if (inherit !== undefined) options.push(h("option", { value: "", selected: value === undefined }, `Default${inherit ? ` (${inherit})` : ""}`));
    options.push(h("option", { value: "default", selected: inherit === undefined ? !value : value === "default" }, "Its default voice"));
    if (tts && tts.startsWith("tts.")) {
      const language = languageSelect && languageSelect.value && languageSelect.value !== "auto" ? `?language=${encodeURIComponent(languageSelect.value)}` : "";
      try {
        const data = await api("GET", `voices/${encodeURIComponent(tts)}${language}`);
        for (const v of data.voices) options.push(h("option", { value: v.id, selected: value === v.id }, v.name || v.id));
      } catch (_) { /* no voices to offer */ }
    }
    fill(select, options);
  }
  ttsSelect.addEventListener("change", () => { value = undefined; load(); });
  if (languageSelect) languageSelect.addEventListener("change", () => { value = undefined; load(); });
  load();
  return select;
}

// ---------------------------------------------------------------- wake words

function wakeWordName(wake, id) {
  if (!id) return "none";
  const word = (wake.wake_words || []).find((w) => w.id === id);
  return word ? word.name : id;
}

const THRESHOLD_FIELDS = [
  ["theta_competitor", "Confusable margin", "Refused when a confusable fits what was heard better than the wake word does, by more than this. Higher accepts more wake words said quickly or from across the room, and more confusables."],
  ["theta_last", "Last word", "Refused when its last word (“nabu”) fits worse than any speech would, by more than this. Higher accepts more, from further away."],
  ["theta_prefix", "First words", "The same for the words before the last (“okay”)."],
];

// A number input that is empty for the default, shown as its placeholder.
function defaultNumber(value, fallback, step, max) {
  return h("input", { type: "number", step, min: 0, max, value: value === undefined || value === null ? "" : value, placeholder: String(num(fallback)), style: "width:7em" });
}

function numberOrNull(input) {
  return input.value.trim() === "" ? null : Number(input.value);
}

// One wake word's settings: its cutoff on the satellite, and its verifier --
// confusables, pronunciations of words the verifier doesn't know, thresholds.
function wakeWordEditor(word, wake, reload) {
  const tuning = word.tuning || {};
  const defaults = word.defaults;
  const verifier = word.verifier;
  let confusables = (verifier ? verifier.confusables : tuning.confusables || []).slice();
  const pron = Object.assign({}, defaults.pronunciations, (verifier && verifier.pronunciations) || {});
  const guessed = new Set();  // filled in from a guess, and not edited since
  const cutoff = defaultNumber(tuning.cutoff, defaults.cutoff, "0.01", wake.max_cutoff);
  const chipsBox = h("div");
  const words = h("div");
  const problem = h("div");
  const restore = h("button", { type: "button", class: "link" }, `Restore the built-in list (${defaults.confusables.length})`);
  const thresholds = Object.fromEntries(THRESHOLD_FIELDS.map(([key]) => [key,
    defaultNumber((tuning.thresholds || {})[key], defaults.thresholds[key], "0.01", 10)]));
  const second = defaultNumber(tuning.second_opinion, defaults.second_opinion, "0.05", 10);
  const thresholdBox = h("div", {},
    heading("h3", "Thresholds", "How the verifier decides. Empty: the default, shown greyed. They were measured for “okay nabu” on a Voice PE: change them a little at a time, and test from where people stand."),
    h("div", { class: "thresholds" },
      ...[...THRESHOLD_FIELDS.map(([key, label, help]) => [label, thresholds[key], help]),
        ["Second opinion", second, "A wake word refused on its last or first words is accepted anyway when the speech model scores it at or below this. Higher overturns more. A confusable's refusal stands."]]
        .flatMap(([label, input, help]) => {
          input.id = input.id || `field-${++fields}`;
          return [h("div", { class: "label-row" }, h("label", { class: "label", for: input.id }, label), hint(help)), input];
        })));

  function shown() {
    restore.style.display = !word.custom && (confusables.length !== defaults.confusables.length || confusables.some((c) => !defaults.confusables.includes(c))) ? "" : "none";
    thresholdBox.style.display = !word.custom || confusables.length ? "" : "none";
  }
  function renderChips() {
    fill(chipsBox, chipsInput(confusables, (list) => { confusables = list; changed(); }, "a phrase that sounds like it, then Enter"));
    shown();
  }
  restore.addEventListener("click", () => { confusables = defaults.confusables.slice(); renderChips(); changed(); });

  // What the verifier can't say: a pronunciation each, guessed when none
  // was given.
  let timer = null;
  let asked = 0;
  function changed() {
    clearTimeout(timer);
    timer = setTimeout(check, 300);
    shown();
  }
  async function check() {
    const ask = ++asked;
    if (word.custom && !confusables.length) { fill(words); fill(problem); return; }
    let data;
    try { data = await api("POST", `wake_words/${encodeURIComponent(word.id)}/check`, { confusables, pronunciations: pron }); }
    catch (err) { fill(problem, errorBox(err)); return; }
    if (ask !== asked) return;
    const missing = data.words.filter((w) => !pron[w.word] || guessed.has(w.word));
    fill(words, data.words.length ? h("details", { open: missing.length > 0 || null },
      h("summary", {}, `Pronunciations (${data.words.length})`, hint(`Words not in the verifier's dictionary, in the model's phones (below), the stress mark before the vowel it falls on. A guess (from espeak) is filled in where none was given: check it before saving.`)),
      h("div", { class: "kv" }, data.words.flatMap((w) => {
        if (!pron[w.word] && w.guess) { pron[w.word] = w.guess; guessed.add(w.word); }
        const input = h("input", { type: "text", value: pron[w.word] || "", placeholder: "n ˈæ b u", style: "width:14em" });
        const badge = h("span", { class: "badge warn" });
        const mark = () => {
          badge.textContent = guessed.has(w.word) ? "guessed" : pron[w.word] ? "" : "needed";
          badge.style.display = badge.textContent ? "" : "none";
        };
        input.addEventListener("input", () => { pron[w.word] = input.value.trim(); guessed.delete(w.word); mark(); });
        input.addEventListener("change", changed);
        mark();
        return [h("div", {}, w.word), h("div", { class: "row" }, input, badge, h("span", { class: "muted small" }, w.in.join(", ")))];
      }))) : null);
    fill(problem, data.error ? h("div", { class: "notice warn" }, data.error) : null);
  }
  const phones = h("details", { class: "small" }, h("summary", {}, "The model's phones"),
    h("div", { class: "phones" }, (wake.phones || []).map(([phone, example, said]) =>
      h("span", { class: "chip", title: `${example}: ${said}` }, h("b", {}, phone), ` ${example}`))));

  async function save(body, button) {
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "Saving…";
    try {
      const data = await api("PUT", `wake_words/${encodeURIComponent(word.id)}`, body);
      toast(data.rebuilt && data.rebuilt.length ? "Saved; its verifier was rebuilt" : "Saved");
      reload();
    } catch (err) { toast(err.message); button.disabled = false; button.textContent = old; }
  }
  const saveButton = h("button", { class: "primary", onclick: () => save({
    cutoff: numberOrNull(cutoff), confusables, pronunciations: pron,
    thresholds: Object.fromEntries(Object.entries(thresholds).map(([k, input]) => [k, numberOrNull(input)])),
    second_opinion: numberOrNull(second),
  }, saveButton) }, "Save");
  const resetButton = h("button", { onclick: () => { if (confirm(`Back to ${word.name}'s defaults?`)) save({}, resetButton); } }, "Restore its defaults");
  renderChips();
  check();
  return h("div", { class: "wake-editor" },
    field("Cutoff", cutoff,
      `How sure the satellite's own model must be before it wakes, from 0 to ${wake.max_cutoff}. Lower hears it from further away and fires more often on other speech: `
      + (verifier ? "the verifier refuses those, but the ring lights and the music ducks while it checks. "
        : "nothing checks it again, so it wakes. ")
      + (word.custom ? `It was trained for ${num(word.manifest_cutoff)}.` : "The default was measured with its verifier.")),
    heading("h3", "Confusables", "Phrases that sound like it and must not wake the satellite. Each is decoded against the wake word, and the wake word is refused when one fits better. One too close refuses the real wake word too, said quickly or from across the room."),
    verifier ? null : h("p", { class: "muted small" }, "Not verified: add some to verify it.", hint("Nothing checks the satellite's detection. With confusables it is verified here like the built-in ones, which lets it run at a lower cutoff.")),
    chipsBox, restore, words, phones, problem,
    thresholdBox,
    h("div", { class: "row" }, saveButton,
      Object.keys(tuning).length ? resetButton : null,
      h("span", { class: "grow" }),
      word.custom ? h("button", { class: "danger", onclick: async () => {
        if (!confirm(`Delete the wake word ${word.name}?`)) return;
        try { await api("DELETE", `wake_words/${encodeURIComponent(word.id)}`); toast("Deleted"); reload(); }
        catch (err) { toast(err.message); }
      } }, "Delete") : null));
}

// A phrase's wake word id, as the app makes it ("Hey, Computer!" -> hey_computer).
function wakeWordId(phrase) {
  return String(phrase || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

// Whose recordings a wake word was trained with: "Alice (12), Bob (10)".
function recordingsText(recordings) {
  return Object.entries(recordings || {}).map(([name, n]) => `${name} (${n})`).join(", ");
}

// Training a wake word from a phrase: the form, with whose recordings of it
// to train with, or the job running (followed until it ends), and how the
// last one went.
function wakeTraining(first, reload) {
  const box = h("div");
  const phrase = h("input", { type: "text", placeholder: "hey computer", style: "max-width:260px", "aria-label": "Wake phrase" });
  const stages = { positives: "Saying it", preparing: "Preparing", training: "Training", converting: "Saving" };
  const whose = h("div", { class: "row small" });
  const button = h("button", { class: "primary", onclick: start }, "Train");
  let ticks = [];
  function recorded() {
    const word = wakeWordId(phrase.value);
    const found = (first.recorded || {})[word];
    const trained = first.trained || [];
    button.textContent = trained.includes(word) || trained.includes(`${word}_trained`) ? "Train again" : "Train";
    ticks = found ? Object.entries(found.speakers).map(([name, n]) => ({ name, box: h("input", { type: "checkbox", checked: true }), n })) : [];
    fill(whose, ticks.length
      ? ["With the recordings of", ticks.map((t) => h("label", {}, t.box, ` ${t.name} (${t.n})`))]
      : word.length >= 3 ? h("span", { class: "muted" }, "Nobody has recorded it: ", h("a", { href: "#/speakers" }, "record it on the Speakers page"), " for a model that hears them better.") : null);
  }
  phrase.addEventListener("input", recorded);
  let followed = false;
  async function poll() {
    if (!document.body.contains(box)) return;
    try { show(await api("GET", "wake_training")); } catch (err) { toast(err.message); }
  }
  async function start() {
    const body = { phrase: phrase.value };
    if (ticks.length) body.speakers = ticks.filter((t) => t.box.checked).map((t) => t.name);
    try { await api("POST", "wake_training", body); poll(); }
    catch (err) { toast(err.message); }
  }
  function show(status) {
    const job = status.job;
    if (job && job.state === "running") {
      followed = true;
      const left = job.seconds_left != null ? ` · about ${Math.max(1, Math.round(job.seconds_left / 60))} min left` : "";
      const recs = Object.keys(job.recordings || {}).length ? ` · with ${recordingsText(job.recordings)}` : "";
      fill(box, h("div", { class: "row" },
        h("div", { class: "grow" },
          h("div", {}, `${quoted(job.phrase)}: ${stages[job.stage] || job.stage}${left}${recs}`),
          h("progress", { max: 1, value: job.progress, style: "width:100%" })),
        h("button", { onclick: async () => { try { await api("DELETE", "wake_training"); poll(); } catch (err) { toast(err.message); } } }, "Cancel")));
      setTimeout(poll, 2000);
      return;
    }
    if (followed && job && job.state === "done") return reload(job.word);
    const last = !job ? null
      : job.state === "done" ? h("div", { class: "notice ok" }, `Trained ${quoted(job.phrase)}${Object.keys(job.recordings || {}).length ? ` with the recordings of ${recordingsText(job.recordings)}` : ""}. `, h("a", { href: `#/wake?word=${encodeURIComponent(job.word)}` }, "Tune it"))
      : job.state === "cancelled" ? null
      : h("div", { class: "notice bad" }, `${quoted(job.phrase)}: ${job.error || job.state} `, h("a", { href: "api/wake_training/log", target: "_blank" }, "Log"));
    fill(box, last,
      status.unavailable ? h("div", { class: "muted" }, status.unavailable)
        : [h("div", { class: "row" }, phrase, button), whose]);
  }
  show(first);
  return h("div", { class: "panel" },
    heading("h2", "Train", `Type a wake phrase. Piper says it ${first.clips.toLocaleString()} ways and a model learns it against speech, music and noise: about 20 minutes on a Raspberry Pi 5, using all of it. Speakers' own recordings of it (the Speakers page) are trained with too: several people's help everyone, and a built-in phrase trained so is a word of its own beside the built-in one. It is added unverified, at its own cutoff; try it before relying on it. Training a word trained here again replaces it when done.`),
    box);
}

async function pageWakeWords(query) {
  const params = new URLSearchParams(query || "");
  const [wake, sats, training] = await Promise.all([api("GET", "wake_words"), loadSatellites(), api("GET", "wake_training")]);
  const runs = wake.wake_words.filter((w) => w.runs);
  const option = (w, selected) => h("option", { value: w.id, selected: selected || null }, w.name);
  const reload = (word) => {
    const target = word ? `#/wake?word=${encodeURIComponent(word)}` : location.hash;
    if (location.hash === target) pageWakeWords(location.hash.split("?")[1]);
    else location.hash = target;
  };

  const preferred = h("select", { style: "max-width:320px", "aria-label": "Preferred wake word" }, runs.map((w) => option(w, w.id === wake.preferred)));
  const satRows = sats.map((s) => {
    const own = (s.wake_words || [])[0];
    const select = h("select", {},
      h("option", { value: "", selected: !own || null }, `The preferred one (${wakeWordName(wake, wake.preferred)})`),
      runs.map((w) => option(w, w.id === own)));
    return { s, select };
  });

  // Built-in words, the ones in use or changed, and custom ones that match
  // the filter: hundreds of custom words make a long page.
  const using = (id) => sats.filter((s) => ((s.wake_words || [])[0] || wake.preferred) === id);
  const opened = new Set(params.get("word") ? [params.get("word")] : []);
  const filter = wake.wake_words.length > 12 ? h("input", { type: "search", placeholder: `Filter ${wake.wake_words.length} wake words`, style: "max-width:260px" }) : null;
  const list = h("div", { class: "list" });
  function row(word) {
    const users = using(word.id);
    const details = h("details", { class: "family" },
      h("summary", { class: "item" },
        icon("mdi:chevron-right", "chevron"),
        h("div", { class: "grow" },
          h("div", { class: "title" }, word.name),
          h("div", { class: "muted small" }, [
            word.custom ? "custom" : "built in",
            word.verified ? `verified, ${word.verifier.confusables.length} confusables` : "not verified",
            `cutoff ${num(word.cutoff)}`,
            word.trained && Object.keys(word.trained.recordings || {}).length ? `trained with ${recordingsText(word.trained.recordings)}` : null,
            users.length ? `on ${users.map((s) => s.name).join(", ")}` : null,
          ].filter(Boolean).join(" · "))),
        word.id === wake.preferred ? h("span", { class: "badge accent" }, "preferred") : null,
        Object.keys(word.tuning || {}).length ? h("span", { class: "badge" }, "changed") : null,
        word.runs ? null : h("span", { class: "badge bad", title: word.error || "" }, "can't run")),
      h("div", { class: "versions" }));
    details.addEventListener("toggle", () => {
      if (details.open) { opened.add(word.id); if (!details.lastChild.firstChild) fill(details.lastChild, wakeWordEditor(word, wake, reload)); }
      else opened.delete(word.id);
    });
    if (opened.has(word.id)) { details.open = true; fill(details.lastChild, wakeWordEditor(word, wake, reload)); }
    return details;
  }
  const rows = new Map();
  function render() {
    const text = filter ? filter.value.trim().toLowerCase() : "";
    let room = 60;
    const shown = wake.wake_words.filter((w) => {
      const keep = !w.custom || w.id === wake.preferred || using(w.id).length || Object.keys(w.tuning || {}).length || opened.has(w.id);
      const match = !text || w.name.toLowerCase().includes(text) || w.id.includes(text);
      if (keep) return match || !text;
      return match && room-- > 0;
    });
    fill(list, shown.map((w) => { if (!rows.has(w.id)) rows.set(w.id, row(w)); return rows.get(w.id); }));
  }
  if (filter) filter.addEventListener("input", render);
  render();

  const upload = h("input", { type: "file", multiple: true, accept: ".json,.tflite" });
  fill($main,
    heading("h1", "Wake words", "What a satellite listens for before a command. Each listens for one: the preferred wake word, or one of its own. Its own model listens on the satellite; a verified wake word is then checked again here, against confusables — phrases that sound like it — so the satellite can listen more eagerly."),
    wake.error ? h("div", { class: "notice warn" }, wake.error) : null,
    h("div", { class: "panel" },
      heading("h2", "Preferred", "Every satellite listens for this one, unless given its own below."),
      h("div", { class: "row" }, preferred, h("button", { class: "primary", onclick: async () => {
        try { await api("PUT", "settings", { wake_words: [preferred.value] }); await refreshStatus(); toast("Saved"); reload(); }
        catch (err) { toast(err.message); }
      } }, "Save"))),
    sats.length ? h("div", { class: "panel" },
      h("h2", {}, "Satellites"),
      h("div", { class: "kv", style: "align-items:center" }, satRows.flatMap(({ s, select }) => [
        h("div", {}, s.name, s.connected ? null : h("span", { class: "muted small" }, " (offline)")),
        h("div", { class: "row" }, select,
          s.connected && (s.wake_words_need_firmware || []).length ? h("span", { class: "badge bad" }, `${wakeWordName(wake, s.wake_words_need_firmware[0])} needs newer firmware`)
            : s.connected && (s.wake_words_used || []).length ? h("span", { class: "muted small" }, `listening for ${wakeWordName(wake, s.wake_words_used[0])}`) : null)])),
      h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "primary", onclick: async () => {
        try {
          for (const { s, select } of satRows) {
            const own = (s.wake_words || [])[0] || "";
            if (select.value !== own) await api("PUT", `satellites/${s.key}`, { wake_words: select.value ? [select.value] : null });
          }
          toast("Saved"); reload();
        } catch (err) { toast(err.message); }
      } }, "Save"))) : null,
    h("div", { class: "panel" },
      heading("h2", "Tuning", "Built-in wake words are verified. Custom ones — a microWakeWord manifest (.json) and model (.tflite), as ESPHome and Tater share them — run at the cutoff they were trained for, unverified until given confusables."),
      filter, list),
    wakeTraining(training, reload),
    h("div", { class: "panel" },
      h("h2", {}, "Upload"),
      h("div", { class: "row" },
        field("Files", upload, ["A custom wake word's microWakeWord manifest (.json) and model (.tflite), picked together.", wake.custom_dir ? [" They can also be put in ", h("code", {}, wake.custom_dir), "."] : null]),
        h("button", { onclick: async () => {
          if (!upload.files.length) return toast("Pick the .json and the .tflite");
          const form = new FormData();
          for (const file of upload.files) form.append(file.name.endsWith(".json") ? "manifest" : "model", file, file.name);
          try {
            const response = await fetch("api/wake_words", { method: "POST", body: form });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
            toast(`Added ${data.id}`);
            location.hash = `#/wake?word=${encodeURIComponent(data.id)}`;
          } catch (err) { toast(err.message); }
        } }, "Upload"))),
  );
}

// ---------------------------------------------------------------- sessions

const OUTCOME_CLASS = { answered: "ok", done: "ok", "dry run": "accent", "not recognized": "warn", "wake rejected": "", error: "bad", timeout: "bad", "in progress": "accent", "timer stopped": "ok", "timer rang out": "warn" };

// What a session was: a call on a satellite, a test, a replay.
function sessionKind(record, sats) {
  if (record.kind === "call") return (sats && sats[record.satellite.id]) || record.satellite.name || "Session";
  return record.kind === "try" ? "test" : record.kind;
}

// Its icon: its satellite's, or a test's or a replay's.
function sessionIcon(record) {
  if (record.kind === "try") return "mdi:keyboard-outline";
  if (record.kind === "replay") return "mdi:replay";
  return satelliteIcon(state.satellites.find((s) => s.key === (record.satellite || {}).id));
}

// What was heard or said, in quotes, without the stray punctuation
// speech-to-text leaves at the start (", set the lights").
function quoted(text) {
  return `“${String(text).replace(/^[\s,.;:!?]+/, "")}”`;
}

function wakeTitle(wake) {
  return wake && wake.probability !== undefined ? `window average ${prob(wake.probability)}, highest ${prob(wake.max_probability)}, cutoff ${prob(wake.cutoff)}` : null;
}

// Speakers: the voices commands are attributed to, enrolling one from a
// satellite (they read the passage aloud while it listens), and their own
// wake words, recorded there for training.
async function pageSpeakers(preset, recordFor) {
  const data = await api("GET", "speakers");
  if (!data.enabled) {
    fill($main, h("h1", {}, "Speakers"), h("div", { class: "panel muted" }, "Speaker identification is turned off (--no-speaker-id)."));
    return;
  }
  const rgb = (c) => (c ? `rgb(${c.join(",")})` : "var(--line)");
  const list = h("div", { class: "list" });
  for (const sp of data.speakers) {
    const color = h("select", { "aria-label": `${sp.name}'s ring color`, style: "max-width:130px" },
      data.palette.map((p) => h("option", { value: p.name, selected: p.name === sp.color_name || null }, p.name)));
    color.addEventListener("change", async () => {
      try { await api("PATCH", `speakers/${encodeURIComponent(sp.name)}`, { color: color.value }); pageSpeakers(); }
      catch (err) { toast(err.message); }
    });
    const enrolled = (sp.enrolled || []).map((e) => e.satellite);
    const where = [...new Set(enrolled)];
    list.append(h("div", { class: "item" },
      h("span", { class: "dot", style: `background:${rgb(sp.color)}; width:14px; height:14px` }),
      h("div", { class: "grow" },
        h("div", { class: "title" }, sp.name),
        h("div", { class: "muted small" }, [
          `${num(sp.seconds, 0)} s of speech`,
          where.length ? `enrolled on ${where.join(", ")}` : null,
          sp.learned ? `learned from ${sp.learned} command${sp.learned === 1 ? "" : "s"}` : null,
        ].filter(Boolean).join(" · "))),
      color,
      h("button", { onclick: () => pageSpeakers(sp.name) }, "Add more"),
      h("button", { onclick: () => pageSpeakers(null, sp.name) }, "Wake word"),
      h("button", { onclick: async () => {
        const name = prompt(`Rename ${sp.name} to`, sp.name);
        if (!name || name === sp.name) return;
        try { await api("PATCH", `speakers/${encodeURIComponent(sp.name)}`, { name }); pageSpeakers(); }
        catch (err) { toast(err.message); }
      } }, "Rename"),
      h("button", { class: "danger", onclick: async () => {
        if (!confirm(`Forget ${sp.name}'s voice?`)) return;
        try { await api("DELETE", `speakers/${encodeURIComponent(sp.name)}`); pageSpeakers(); }
        catch (err) { toast(err.message); }
      } }, "Delete")));
    if ((sp.wake || []).length) list.append(h("div", { class: "wake-takes" }, sp.wake.map((w) => wakeTakes(sp, w))));
  }
  fill($main,
    h("h1", {}, "Speakers"),
    h("div", { class: "panel" },
      heading("h2", "Voices", `A command is attributed to the closest voice scoring ${num(data.threshold, 2)} or more, and the ring takes their color. Once someone is identified, someone else starting to talk ends their command (Settings, End of speech). Voices also learn from commands they clearly win.`),
      data.speakers.length ? list : h("div", { class: "muted" }, "No voices yet: enroll one below, or add a session's from its page.")),
    enrollPanel(data, preset),
    data.speakers.length ? wakeRecordPanel(data, recordFor) : null);
  if (recordFor) $main.lastChild.scrollIntoView({ block: "start" });
}

// One speaker's takes of a wake word: how many, whether the satellite heard
// them, each to play or delete.
function wakeTakes(sp, w) {
  const base = `speakers/${encodeURIComponent(sp.name)}/wake/${encodeURIComponent(w.word)}`;
  const remove = async (take) => {
    if (!take && !confirm(`Delete ${sp.name}'s ${w.count} recordings of ${quoted(w.phrase)}?`)) return;
    try { await api("DELETE", take ? `${base}/${take.id}` : base); pageSpeakers(); }
    catch (err) { toast(err.message); }
  };
  const heard = w.checked ? ` · the satellite heard ${w.heard} of ${w.checked}` : "";
  return h("details", { class: "small" },
    h("summary", {}, `${quoted(w.phrase)}: ${w.count} recording${w.count === 1 ? "" : "s"}${heard}`),
    w.takes.slice().reverse().map((t) => h("div", { class: "row" },
      h("audio", { controls: true, preload: "none", src: `api/${base}/${t.id}.wav`, style: "height:32px; max-width:260px" }),
      h("span", { class: "grow muted" }, [
        `${num(t.seconds, 1)} s`, t.satellite, t.heard === true ? "heard" : t.heard === false ? "not heard" : null,
        new Date(t.at * 1000).toLocaleDateString(),
      ].filter(Boolean).join(" · ")),
      h("button", { class: "danger", "aria-label": "Delete this recording", onclick: () => remove(t) }, "Delete"))),
    h("div", { class: "row" }, h("button", { class: "danger", onclick: () => remove(null) }, "Delete all")));
}

// Recording someone saying a wake phrase at a satellite, again and again:
// each take kept (or why not) as it comes.
function wakeRecordPanel(data, preset) {
  const box = h("div");
  const speaker = h("select", { style: "max-width:220px", "aria-label": "Whose" },
    data.speakers.map((sp) => h("option", { value: sp.name, selected: sp.name === preset || null }, sp.name)));
  const usable = data.satellites.filter((s) => s.enroll);
  const satellite = h("select", { style: "max-width:260px", "aria-label": "Satellite" },
    data.satellites.map((s) => h("option", { value: s.key, disabled: !s.enroll || null }, s.enroll ? s.name : `${s.name} (update its firmware)`)));
  if (usable.length) satellite.value = usable[0].key;
  const words = h("datalist", { id: "wake-phrases" }, data.wake_words.map((w) => h("option", { value: w })));
  const phrase = h("input", { type: "text", list: "wake-phrases", style: "max-width:220px", "aria-label": "Wake phrase" });
  let typed = false;
  const follow = () => { if (!typed) phrase.value = (data.satellites.find((s) => s.key === satellite.value) || {}).wake_word || ""; };
  phrase.addEventListener("input", () => { typed = true; });
  satellite.addEventListener("change", follow);
  follow();
  let timer = null;
  const stop = () => { if (timer) clearTimeout(timer); timer = null; };
  state.leaving.push(stop);

  async function refresh() {
    timer = null;
    if (!document.body.contains(box)) return;
    try { show((await api("GET", "speakers/recording")).recording); } catch (err) { toast(err.message); }
  }
  async function start() {
    try { show((await api("POST", "speakers/recording", { satellite: satellite.value, name: speaker.value, phrase: phrase.value })).recording); }
    catch (err) { toast(err.message); }
  }
  async function finish() {
    try { await api("DELETE", "speakers/recording"); pageSpeakers(null, speaker.value); } catch (err) { toast(err.message); }
  }
  let view = null;
  function listening(r) {
    if (!view) {
      view = {
        text: h("div"),
        progress: h("progress", { max: r.good, style: "width:100%" }),
        meter: h("meter", { min: -70, max: -10, low: -50, high: -20, optimum: -30, style: "width:100%", "aria-label": "Level" }),
        takes: h("div", { class: "small" }),
      };
      fill(box,
        h("div", { class: "row" },
          h("div", { class: "grow" }, view.text, view.progress, view.meter),
          h("button", { class: "primary", onclick: finish }, "Done")),
        h("div", { class: "passage" }, h("p", {}, `Say ${quoted(r.phrase)}, then pause. Again and again.`)),
        view.takes);
    }
    const heard = r.heard !== null ? ` · the satellite heard ${r.heard}` : "";
    view.text.textContent = `${r.speaker} on ${r.satellite_name}: ${r.kept} kept${r.kept >= r.target ? (r.kept >= r.good ? " (plenty)" : ` (enough; ${r.good} is better)`) : ` of ${r.target}`}${heard}${r.checking ? " · checking…" : ""}`;
    view.progress.value = Math.min(r.kept, r.good);
    view.meter.value = Math.max(-70, Math.min(-10, r.level === null ? -90 : r.level));
    view.meter.title = `Level ${num(r.level, 0)} dBFS`;
    fill(view.takes, r.takes.map((t) => h("div", { class: t.kept ? "" : "muted" },
      t.kept ? "✓ " : "✗ ",
      [t.why, `${num(t.seconds, 1)} s`, t.heard === true ? "heard" : t.heard === false ? "not heard" : null].filter(Boolean).join(" · "))));
  }
  function show(r) {
    stop();
    if (r && r.state === "listening") {
      listening(r);
      timer = setTimeout(refresh, 500);
      return;
    }
    view = null;
    const last = !r ? null
      : r.state === "failed" ? h("div", { class: "notice bad" }, `${r.speaker}: ${r.error || "failed"}`)
      : null;
    fill(box, last,
      usable.length
        ? h("div", { class: "row" }, words, speaker, satellite, phrase, h("button", { class: "primary", onclick: start }, "Start"))
        : h("div", { class: "muted" }, data.satellites.length ? "Update a satellite's firmware to record from it." : "No satellite is connected."));
  }
  show(data.recording);
  return h("div", { class: "panel" },
    heading("h2", "Record a wake word", "Everyone in the home: say the wake phrase at the satellite 25 times, pausing between, the way and from where you usually say it. The ring blinks red while it listens, and the phrase starts no command. A wake word trained with several people's recordings hears them all better, and people who made none: in micro-wake-word's tests 25 each from four people took them to 94% of their wake words, and 50 each from three took a fourth to 82%, where the shipped “Okay Nabu” got 56%. Train it on the Wake words page."),
    box);
}

function enrollPanel(data, preset) {
  const box = h("div");
  const names = h("datalist", { id: "speaker-names" }, data.speakers.map((sp) => h("option", { value: sp.name })));
  const name = h("input", { type: "text", list: "speaker-names", value: preset || "", placeholder: "name", style: "max-width:220px", "aria-label": "Whose voice" });
  const usable = data.satellites.filter((s) => s.enroll);
  const satellite = h("select", { style: "max-width:260px", "aria-label": "Satellite" },
    data.satellites.map((s) => h("option", { value: s.key, disabled: !s.enroll || null }, s.enroll ? s.name : `${s.name} (update its firmware)`)));
  if (usable.length) satellite.value = usable[0].key;
  let timer = null;
  const stop = () => { if (timer) clearTimeout(timer); timer = null; };
  state.leaving.push(stop);

  async function refresh() {
    timer = null;
    if (!document.body.contains(box)) return;
    try { show((await api("GET", "speakers/enrollment")).enrollment); } catch (err) { toast(err.message); }
  }
  async function start() {
    try { show((await api("POST", "speakers/enrollment", { satellite: satellite.value, name: name.value })).enrollment); }
    catch (err) { toast(err.message); }
  }
  async function save() {
    try { await api("POST", "speakers/enrollment/save"); toast("Saved"); pageSpeakers(); }
    catch (err) { toast(err.message); refresh(); }
  }
  async function cancel() {
    try { await api("DELETE", "speakers/enrollment"); pageSpeakers(); } catch (err) { toast(err.message); }
  }
  // While it listens: built once, then updated in place (a rebuild every
  // half second would lose clicks).
  let view = null;
  function listening(e) {
    if (!view) {
      view = {
        text: h("div"),
        progress: h("progress", { max: e.target_s, style: "width:100%" }),
        meter: h("meter", { min: -70, max: -10, low: -50, high: -20, optimum: -30, style: "width:100%", "aria-label": "Level" }),
        save: h("button", { class: "primary", onclick: save }, "Save"),
      };
      fill(box,
        h("div", { class: "row" },
          h("div", { class: "grow" }, view.text, view.progress, view.meter),
          view.save,
          h("button", { onclick: cancel }, "Cancel")),
        h("div", { class: "passage" }, data.passage.split("\n\n").map((para) => h("p", {}, para))));
    }
    view.text.textContent = `${e.name} on ${e.satellite_name}: ${num(e.speech_s, 0)} of ${num(e.target_s, 0)} s of speech${e.full ? " (that's all it takes)" : ""}`;
    view.progress.value = Math.min(e.speech_s, e.target_s);
    view.meter.value = Math.max(-70, Math.min(-10, e.level === null ? -90 : e.level));
    view.meter.title = `Level ${num(e.level, 0)} dBFS`;
    view.save.disabled = e.speech_s < e.min_s;
  }
  function show(e) {
    stop();
    if (e && e.state === "listening") {
      listening(e);
      timer = setTimeout(refresh, 500);
      return;
    }
    view = null;
    const last = !e ? null
      : e.state === "failed" ? h("div", { class: "notice bad" }, `${e.name}: ${e.error || "failed"}`)
      : null;
    fill(box, last,
      usable.length
        ? h("div", { class: "row" }, names, name, satellite, h("button", { class: "primary", onclick: start }, "Start"))
        : h("div", { class: "muted" }, data.satellites.length ? "Update a satellite's firmware to enroll from it." : "No satellite is connected."));
  }
  show(data.enrollment);
  return h("div", { class: "panel" },
    heading("h2", "Enroll", "Read the passage aloud in your normal voice while walking slowly around the room: close to the satellite, across the room, facing away now and then. About a minute. The ring blinks red while it listens. Enrolling again on another satellite, or in another room, adds to the same voice."),
    box);
}

async function pageSessions(query) {
  const satellite = new URLSearchParams(query || "").get("satellite");
  await loadSatellites();
  const settings = await api("GET", "settings");
  const list = h("div", { class: "list" });
  const sats = satelliteLabels();
  const session = satellite && state.satellites.find((s) => s.key === satellite);

  // A session: what was heard, big; what it ran and said; when, where and
  // with which wake word.
  const row = (c) => h("a", { class: "item session-row", href: `#/session/${c.id}` },
    icon(sessionIcon(c), "big", null),
    h("div", { class: "grow" },
      h("div", { class: "heard" }, c.heard ? quoted(c.heard) : c.summary ? c.summary[0].toUpperCase() + c.summary.slice(1) : "…"),
      c.command || c.said ? h("div", { class: "muted small" }, [c.command, c.said ? quoted(c.said) : null].filter(Boolean).join(" · ")) : null),
    h("div", { class: "session-side" },
      h("div", { class: "session-meta muted small" },
        h("span", {}, time(c.started)),
        h("span", { title: wakeTitle(c.wake) }, sessionKind(c, sats),
          c.wake ? ` · ${c.wake.name || c.wake.wake_word}${c.wake.probability !== undefined ? ` ${prob(c.wake.probability)}` : ""}` : "")),
      c.kept ? icon("mdi:waveform", "", "Audio kept") : null,
      c.dry_run && c.outcome !== "dry run" ? h("span", { class: "badge" }, "dry run") : null,
      h("span", { class: `badge ${OUTCOME_CLASS[c.outcome] || ""}` }, c.outcome)));

  async function refresh() {
    const data = await api("GET", `calls${satellite ? `?satellite=${satellite}` : ""}`);
    fill(list, ...(data.calls.length ? data.calls.map(row) : [h("p", { class: "muted" }, "No sessions yet.")]));
  }

  const keep = h("input", { type: "checkbox", checked: settings.keep_audio, onchange: async (e) => { await api("PUT", "settings", { keep_audio: e.target.checked }); refreshStatus(); toast(e.target.checked ? "Keeping audio of every session" : "Not keeping audio"); } });
  const max = h("input", { type: "number", min: 1, value: settings.max_saved_calls, style: "width:80px", onchange: async (e) => { await api("PUT", "settings", { max_saved_calls: Number(e.target.value) }); toast("Saved"); } });
  fill($main,
    h("div", { class: "row" },
      h("h1", { style: "margin:0; flex:1 0 auto" }, satellite ? `Sessions on ${sats[satellite] || satellite}` : "Sessions"),
      satellite ? h("a", { href: "#/sessions" }, "All satellites") : null,
      h("label", { class: "small" }, keep, "Keep every session", hint("Keeps the audio and full trace of every session — everything from the wake word to the answer. Without this, the last sessions' traces are kept in memory only. Debugging one satellite (on its page, under Test it) keeps its audio.")),
      h("label", { class: "small" }, "At most", max)),
    session && session.debug ? h("div", { class: "notice warn" }, `Debugging ${session.name}${session.debug.dry_run ? " (dry run: nothing is executed)" : ""}. New sessions appear here as they happen.`) : null,
    h("div", { class: "panel", style: "margin-top:12px" }, list),
  );
  await refresh();
  poll(refresh, 2000);
}

const MARK_COLORS = {
  detection: "#e5534b", kaldi_wake_start: "#b083f0", kaldi_split: "#986ee2", wake_start: "#57ab5a", wake_end: "#57ab5a", cut: "#daaa3f", end: "#768390",
};
const MARK_LABELS = {
  detection: "detection (end of pre-roll)", kaldi_wake_start: "Kaldi wake start", kaldi_split: "Kaldi wake end", wake_start: "wake phrase (CTC)", wake_end: "", cut: "command starts", end: "end of speech",
};

function decodeWav(buffer) {
  const view = new DataView(buffer);
  let offset = 12, rate = 16000, data = null;
  while (offset + 8 <= view.byteLength) {
    const id = String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3));
    const size = view.getUint32(offset + 4, true);
    if (id === "fmt ") rate = view.getUint32(offset + 12, true);
    if (id === "data") { data = new Int16Array(buffer.slice(offset + 8, offset + 8 + size)); break; }
    offset += 8 + size + (size % 2);
  }
  return { rate, samples: data || new Int16Array(0) };
}

function waveform(record, name, spans) {
  const url = `api/calls/${record.id}/audio/${name}`;
  const audio = h("audio", { controls: true, src: url, preload: "auto", style: "width:100%" });
  const canvas = h("canvas", { width: 1000, height: 140 });
  const legend = h("div", { class: "legend" });
  let wav = null;
  const marks = name === "command" ? {} : (record.marks || {});
  function draw() {
    const ctx = canvas.getContext("2d");
    const W = canvas.width = canvas.clientWidth * devicePixelRatio;
    const H = canvas.height = 140 * devicePixelRatio;
    ctx.clearRect(0, 0, W, H);
    if (!wav) return;
    const { samples, rate } = wav;
    const duration = samples.length / rate || 1;
    const x = (t) => (t / duration) * W;
    for (const span of spans) {
      ctx.fillStyle = span.color;
      ctx.fillRect(x(span.start), 0, Math.max(2, x(span.end) - x(span.start)), H);
    }
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--muted");
    const per = Math.max(1, Math.floor(samples.length / W));
    for (let px = 0; px < W; px++) {
      let lo = 0, hi = 0;
      for (let i = px * per; i < Math.min(samples.length, (px + 1) * per); i++) { if (samples[i] < lo) lo = samples[i]; if (samples[i] > hi) hi = samples[i]; }
      const y1 = H / 2 - (hi / 32768) * (H / 2), y2 = H / 2 - (lo / 32768) * (H / 2);
      ctx.fillRect(px, y1, 1, Math.max(1, y2 - y1));
    }
    for (const [key, t] of Object.entries(marks)) {
      if (t === null || t === undefined) continue;
      ctx.fillStyle = MARK_COLORS[key] || "#888";
      ctx.fillRect(x(t) - devicePixelRatio, 0, 2 * devicePixelRatio, H);
    }
    ctx.fillStyle = "#1f6feb";
    ctx.fillRect(x(audio.currentTime) - devicePixelRatio, 0, 2 * devicePixelRatio, H);
  }
  canvas.addEventListener("click", (e) => {
    if (!wav) return;
    const rect = canvas.getBoundingClientRect();
    audio.currentTime = ((e.clientX - rect.left) / rect.width) * (wav.samples.length / wav.rate);
    audio.play();
  });
  audio.addEventListener("timeupdate", draw);
  audio.addEventListener("seeked", draw);
  window.addEventListener("resize", draw);
  fetch(url).then((r) => r.arrayBuffer()).then((buffer) => { wav = decodeWav(buffer); draw(); });
  fill(legend, 
    ...Object.keys(marks).filter((k) => MARK_LABELS[k]).map((k) => h("span", { style: `--c:${MARK_COLORS[k]}` }, `${MARK_LABELS[k]} ${marks[k].toFixed(2)}s`)),
    ...spans.filter((s) => s.label).map((s) => h("span", { style: `--c:${s.color}` }, s.label)),
  );
  return h("div", { class: "wave" }, canvas, legend, audio);
}

const SPAN_COLORS = { require: "rgba(31,111,235,.25)", optional: "rgba(31,111,235,.12)", filler: "rgba(118,131,144,.15)", name: "rgba(26,127,55,.25)", number: "rgba(154,103,0,.25)", wildcard: "rgba(207,34,46,.18)" };

function stageOf(record, name) {
  return (record.stages || []).find((s) => s.stage === name);
}

function recognitionSpans(record, offset) {
  const rec = stageOf(record, "recognition");
  const best = rec && rec.recognition && rec.recognition.matches && rec.recognition.matches[0];
  if (!best || !best.spans) return [];
  return best.spans.map((s) => ({ start: offset + s.start, end: offset + s.end, color: SPAN_COLORS[s.role] || "rgba(0,0,0,.1)", label: null }));
}

// Slots in a line: "area office · color blue".
function slotsText(slots) {
  return Object.entries(slots || {}).map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
}

// Why Kaldi refused a wake word, in a few words.
function wakeRejection(stage) {
  if (!stage || stage.accepted) return null;
  if (stage.error) return stage.error;
  if (stage.rejected_by === "confusable") return `sounded like “${stage.confusable_words || "a confusable"}”`;
  if (stage.rejected_by === "last") return "its last word was too far off";
  if (stage.rejected_by === "prefix") return "its first words were too far off";
  return stage.rejected_by ? `rejected by ${stage.rejected_by}` : "rejected";
}

// What a session came to: an icon, and a line.
function sessionResult(record) {
  const rec = stageOf(record, "recognition");
  const agent = stageOf(record, "agent");
  const unknown = stageOf(record, "unknown");
  if (record.outcome === "wake rejected") {
    const kaldi = (record.stages || []).filter((s) => s.stage === "wake_kaldi").pop();
    const why = wakeRejection(kaldi);
    return ["mdi:shield-off-outline", [`Wake word rejected${why ? `: ${why}` : ""}`]];
  }
  if (rec && rec.command) {
    return ["mdi:flash-outline", [record.dry_run ? "Would run " : "Ran ", h("a", { href: `#/command/${rec.command}` }, rec.command),
      rec.slots && Object.keys(rec.slots).length ? h("span", { class: "muted" }, ` (${slotsText(rec.slots)})`) : null]];
  }
  if (rec && rec.answer) return ["mdi:comment-question-outline", [`Answered by the ${rec.answer.skill || rec.answer.solver || rec.answer.kind} skill`]];
  if (agent) {
    const name = (agent.agent || "the conversation agent").replace(/^conversation\./, "");
    if (!agent.asked) return ["mdi:robot-outline", [`Would ask ${name}`]];
    if (agent.error) return ["mdi:robot-outline", [`Couldn't ask ${name}`]];
    return ["mdi:robot-outline", [agent.understood ? `Answered by ${name}` : `${name} understood nothing`]];
  }
  if (unknown || (rec && !rec.command)) {
    const closest = rec && rec.hint && rec.hint.intent;
    return ["mdi:help-circle-outline", ["Not recognized", closest ? h("span", { class: "muted" }, ` (closest: ${closest})`) : null]];
  }
  return ["mdi:circle-medium", [record.summary || record.outcome]];
}

// A session at a glance: what was heard, what it came to, what was said,
// and what went wrong.
function sessionGlance(record) {
  const rec = stageOf(record, "recognition");
  const typed = stageOf(record, "typed");
  const r = (rec && rec.recognition) || {};
  // An STT entity that always runs heard it, needed or not.
  const heard = r.tdt_transcript || (rec && rec.stt && rec.stt.text) || r.transcript || (typed && typed.text) || "";
  const speech = (record.stages || []).filter((s) => s.stage === "speech").pop();
  const errors = (record.stages || []).filter((s) => s.error && ["action", "error", "speech_failed", "agent"].includes(s.stage)).map((s) => s.error);
  const [resultIcon, result] = sessionResult(record);
  return h("div", { class: "glance" },
    heard ? h("div", { class: "glance-heard" }, icon(typed || (rec && rec.text_source === "typed") ? "mdi:keyboard-outline" : "mdi:ear-hearing", "", typed ? "Typed" : "Heard"), h("span", {}, quoted(heard))) : null,
    h("div", { class: "glance-line" }, icon(resultIcon, "", "Result"), h("span", {}, ...result)),
    speech ? h("div", { class: "glance-line" }, icon("mdi:volume-high", "", "Said"),
      h("span", {}, speech.text ? quoted(speech.text) : h("span", { class: "muted" }, speech.silent ? "Nothing: it's silent" : "A short sound"))) : null,
    errors.map((e) => h("div", { class: "notice bad" }, e)));
}

// How an intent command can turn out (commands.INTENT_OUTCOMES), and the
// intents offered by name.
const INTENT_OUTCOMES = ["done", "no_devices", "not_exposed", "unavailable", "no_area", "failed"];
const INTENTS = ["HassTurnOn", "HassTurnOff", "HassToggle", "HassLightSet", "HassClimateSetTemperature", "HassSetPosition",
  "HassMediaPause", "HassMediaUnpause", "HassMediaNext", "HassMediaPrevious", "HassSetVolume", "HassFanSetSpeed",
  "HassVacuumStart", "HassVacuumReturnToBase", "HassListAddItem", "HassGetState"];

// An intent's slots as written in the editor: "key: value" a line; a comma
// list is a list.
function intentSlots(text) {
  const slots = {};
  for (const line of String(text || "").split("\n")) {
    const at = line.indexOf(":");
    if (at < 1) continue;
    const key = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    if (!key || !value) continue;
    slots[key] = !value.includes("{") && value.includes(",") ? value.split(",").map((v) => v.trim()).filter(Boolean) : value;
  }
  return slots;
}

// What the transcripts are called: heard by the CTC and TDT heads (or an STT
// entity), or typed (a satellite's Test it) and read as spoken words.
function heardLabels(rec) {
  if (rec.text_source === "typed") return { ctc: "Read as", text: "Typed" };
  return { ctc: "CTC heard", text: `${rec.text_source && rec.text_source !== "TDT" ? rec.text_source : "TDT"} heard` };
}

function renderRecognition(rec) {
  const r = rec.recognition || {};
  const matchRows = (r.matches || []).map((m, i) => h("div", { class: "stage ok" },
    h("div", { class: "head" }, h("span", { class: "name" }, `${i + 1}. ${m.intent}`),
      m.example ? h("span", { class: "badge accent" }, `example (${m.example_source})`) : null,
      m.cost !== undefined ? h("span", { class: "muted small" }, `cost ${m.cost}`) : null,
      m.ambiguous_with ? h("span", { class: "badge warn" }, `ambiguous with ${m.ambiguous_with.join(", ")}`) : null),
    m.slots && Object.keys(m.slots).length ? h("div", { class: "small" }, "slots ", h("code", {}, JSON.stringify(m.slots))) : null,
    m.spans ? h("div", { class: "small muted" }, m.spans.map((s) => `${s.role} “${s.phrase}” ${num(s.start, 2)}–${num(s.end, 2)}s (${num(s.cost, 2)})`).join(" · "), " — seconds into the command") : null,
    m.unexplained && m.unexplained.length ? h("div", { class: "small" }, "unexplained: ", m.unexplained.map((w) => w.text).join(", ")) : null,
    (m.notes || []).map((n) => h("div", { class: "small muted" }, n))));
  const rejected = (r.rejected || []).slice().sort((a, b) => (b.heard_groups || 0) - (a.heard_groups || 0));
  return h("div", {},
    h("div", { class: "kv" },
      h("div", {}, heardLabels(rec).ctc), h("div", {}, r.transcript || "—"),
      h("div", {}, heardLabels(rec).text), h("div", {}, r.tdt_transcript || h("span", { class: "muted" }, "not run")),
      rec.stt && rec.stt.streamed && !rec.stt.asked ? [h("div", {}, `${rec.stt.entity} heard`), h("div", {}, rec.stt.text ? rec.stt.text : h("span", { class: "muted" }, rec.stt.error ? `nothing (${rec.stt.error})` : "nothing yet"), h("span", { class: "muted small" }, " · not needed"))] : null,
      rec.stt && rec.stt.streamed ? [h("div", {}, "Streamed"), h("div", { class: "small" }, `${num(rec.stt.streamed.seconds, 1)} s as you spoke`, rec.stt.streamed.after_speech !== null && rec.stt.streamed.after_speech !== undefined ? `; answered ${Math.round(rec.stt.streamed.after_speech * 1000)} ms after` : "")] : null,
      rec.stt && (rec.stt.asked || rec.stt.streamed) && rec.stt.audio ? [h("div", {}, "Sent"), h("div", {}, rec.stt.audio.channel === "aec" ? (rec.stt.audio.gain_db !== undefined ? `AEC, boosted ${num(rec.stt.audio.gain_db, 1)} dB` : "AEC") : `AGC${rec.stt.audio.why ? ` (${rec.stt.audio.why})` : ""}`,
        rec.stt.audio.from !== undefined ? h("span", { class: "muted" }, ` · from ${num(rec.stt.audio.from, 2)} s, where Kaldi ended the wake word`) : null)] : null,
      r.unspellable ? [h("div", {}, "Not in the model's pieces"), h("div", {}, r.unspellable.join(", "))] : null,
      r.lead_in ? [h("div", {}, "Lead-in ignored"), h("div", {}, r.lead_in)] : null,
      h("div", {}, "Timing"), h("div", { class: "small" }, Object.entries(rec.timings || {}).map(([k, v]) => `${k} ${Math.round(v * 1000)} ms`).join(" · "))),
    rec.skipped && rec.skipped.length ? h("div", { class: "notice warn" }, "Passed over: ", rec.skipped.map((s) => `${s.command} (${s.why})`).join(", ")) : null,
    rec.text_from_tdt && Object.keys(rec.text_from_tdt).length ? h("div", { class: "small muted" }, "Free text re-read from TDT; the CTC head heard ", JSON.stringify(rec.text_from_tdt)) : null,
    rec.answer ? h("div", { class: "notice info" }, rec.answer.text || "(no answer)",
      h("div", { class: "small muted" }, rec.answer.kind === "exact" ? `answered exactly (${rec.answer.skill || rec.answer.solver})` : `answered (${rec.answer.kind})`)) : null,
    rec.matched_by === "transcript" ? h("div", { class: "notice info" }, `Found by keywords in the ${rec.text_source && rec.text_source !== "TDT" ? `transcript from ${rec.text_source}` : "TDT transcript"}: the audio's own evidence wasn't enough.`) : null,
    !rec.command && rec.transcript_pass ? h("div", { class: "small muted" }, `Keywords on the transcript (“${rec.transcript_pass.text}”): `,
      rec.transcript_pass.matches && rec.transcript_pass.matches.length ? `${rec.transcript_pass.matches[0].intent} (cost ${rec.transcript_pass.matches[0].cost}), not runnable here` : "nothing matched") : null,
    rec.matched_by && rec.matched_by !== "ctc" && rec.matched_by !== "transcript" ? h("div", { class: "notice info" }, rec.matched_by === "gliner" ? "Found by GLiNER: nothing matched on keywords or examples." : `Found by meaning: nothing matched on keywords or examples, and this command was very close${rec.fallback && rec.fallback.meaning && rec.fallback.meaning.heard_by ? `; ${rec.fallback.meaning.heard_by} heard what was said in it` : ""}.`) : null,
    rec.fallback ? h("details", { open: !!(rec.fallback.llm && !rec.fallback.llm.skipped && !rec.command) }, h("summary", {}, "When nothing matched"),
      rec.fallback.meaning && rec.fallback.meaning.skipped ? h("div", { class: "small" }, `By meaning: skipped (${rec.fallback.meaning.skipped})`) : null,
      rec.fallback.meaning && !rec.fallback.meaning.skipped ? h("div", { class: "small" }, "By meaning: ", rec.fallback.meaning.command || "nothing", rec.fallback.meaning.similarity !== undefined ? ` (similarity ${rec.fallback.meaning.similarity}, ahead by ${rec.fallback.meaning.margin})` : "", rec.fallback.meaning.why ? ` — ${rec.fallback.meaning.why}` : "",
        rec.fallback.meaning.heard_by ? h("div", { class: "muted" }, `${rec.fallback.meaning.heard_by} heard `, h("code", {}, JSON.stringify(rec.fallback.meaning.arguments || {})),
          rec.fallback.meaning.combination ? ` — said with ${rec.fallback.meaning.combination.length ? rec.fallback.meaning.combination.join(" and ") : "nothing"}` : "",
          rec.fallback.meaning.problems ? ` — not run: ${rec.fallback.meaning.problems.join("; ")}` : "") : null,
        !rec.fallback.meaning.heard_by && rec.fallback.meaning.problems ? h("div", { class: "muted" }, `not run: ${rec.fallback.meaning.problems.join("; ")}`) : null) : null,
      rec.fallback.llm ? h("div", { class: "small" }, `${rec.fallback.llm.by || "GLiNER"}: `,
        rec.fallback.llm.skipped ? `skipped (${rec.fallback.llm.skipped})` : [
          h("code", {}, rec.fallback.llm.raw || "nothing"), ` in ${took(rec.fallback.llm.seconds || 0)}`,
          rec.fallback.llm.candidates ? h("div", { class: "muted" }, "chose from ", rec.fallback.llm.candidates.join(", ")) : null,
          rec.fallback.llm.combination ? h("div", { class: "muted" }, `said with ${rec.fallback.llm.combination.length ? rec.fallback.llm.combination.join(" and ") : "nothing"}`) : null,
          rec.fallback.llm.problems ? h("div", { class: "muted" }, "refused: ", rec.fallback.llm.problems.join("; ")) : null,
          rec.fallback.llm.refused ? h("div", { class: "muted" }, `not run: ${rec.fallback.llm.refused.command} is ${rec.fallback.llm.refused.why}`) : null,
          rec.fallback.llm.said ? h("div", { class: "muted" }, `it said: ${rec.fallback.llm.said}`) : null,
        ]) : null) : null,
    rec.hint ? h("div", { class: "notice info" }, rec.hint.text, h("div", { class: "small muted" }, `hint from ${rec.hint.source || "nothing"}`, rec.hint.similarity !== undefined ? ` · similarity ${rec.hint.similarity} vs ${rec.hint.next_similarity} (${rec.hint.next})` : "")) : null,
    h("h3", {}, `Matched (${(r.matches || []).length})`),
    matchRows.length ? matchRows : h("p", { class: "muted" }, "Nothing matched."),
    h("details", {}, h("summary", {}, `Why the other ${rejected.length} didn't match`),
      ...rejected.map((x) => h("div", { class: "stage" },
        h("div", { class: "head" }, h("span", { class: "name" }, x.intent), x.heard_groups ? h("span", { class: "muted small" }, `${x.heard_groups} required group(s) heard`) : null),
        ...(x.reasons || []).map((reason) => h("div", { class: "small muted" }, "– ", reason.replace(" (--full-search shows the closest placement)", "")))))),
  );
}

// The steps of a session's pipeline: each one's icon, name, and a line on
// how it went (status "ok", "bad", "warn" or "" for neither).
const STEP_ICONS = {
  call_start: "mdi:microphone", wake_kaldi: "mdi:shield-check-outline", wake_ratio: "mdi:scale-balance", wake_unverified: "mdi:shield-off-outline",
  endpoint: "mdi:waveform", speaker: "mdi:account-voice", split: "mdi:content-cut", typed: "mdi:keyboard-outline", replay_of: "mdi:replay",
  recognition: "mdi:ear-hearing", action: "mdi:flash-outline", skill_response: "mdi:comment-question-outline", agent: "mdi:robot-outline",
  unknown: "mdi:help-circle-outline", speech: "mdi:volume-high", speech_failed: "mdi:volume-off", timer_ringing: "mdi:bell-ring-outline",
  timer_stopped: "mdi:timer-off-outline", error: "mdi:alert-circle-outline", timeout: "mdi:alert-circle-outline",
};
const STATUS = { ok: ["mdi:check-circle", "Succeeded"], bad: ["mdi:close-circle", "Failed"], warn: ["mdi:alert-circle", "Partly"] };
const MATCHED_BY = { ctc: "in the audio", transcript: "in the transcript", meaning: "by meaning", gliner: "by GLiNER" };

// How long something took: "85 ms", "1.24 s"; "" when it isn't known.
function took(seconds) {
  if (seconds === null || seconds === undefined || seconds === "" || !Number.isFinite(Number(seconds))) return "";
  const s = Number(seconds);
  return s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`;
}
// "…, in 85 ms", when it's known.
const inTime = (seconds) => (took(seconds) ? `, in ${took(seconds)}` : "");

// Recognition's own time (the acoustic model and placing the command are the
// Command starts step's), and the speech model's part of it: TDT, or the STT
// entity (STT that always runs: what it was waited for).
function recognitionTime(d) {
  const timings = d.timings || {};
  const total = Object.entries(timings).filter(([k]) => k !== "acoustic+placement").reduce((sum, [, v]) => sum + Number(v || 0), 0);
  const model = timings.stt !== undefined ? `${d.stt && d.stt.streamed ? "STT, streamed" : "STT"} ${took(timings.stt)}` : timings.tdt !== undefined ? `TDT ${took(timings.tdt)}` : "";
  return Object.keys(timings).length ? `, in ${took(total)}${model ? ` (${model})` : ""}` : "";
}

// How the satellite said it: Piper's synthesis, or a TTS entity's request,
// start and end (seconds after asking).
function speechTime(d) {
  if (d.synthesis !== undefined && d.synthesis !== null) return ` · Piper, in ${took(d.synthesis)}`;
  if (d.started !== undefined && d.started !== null) return ` · started in ${took(d.started)}${d.ended !== null && d.ended !== undefined ? `, said in ${took(d.ended - d.started)}` : ""}`;
  if (d.requested !== undefined && d.requested !== null) return ` · asked for in ${took(d.requested)}`;
  return "";
}

function stepGlance(stage) {
  const d = stage;
  switch (stage.stage) {
    case "call_start":
      return { title: "Wake word", status: "ok", line: `${d.name || d.wake_word || "—"}${d.probability !== undefined ? ` · ${prob(d.probability)} (cutoff ${prob(d.cutoff)})` : ""}${d.detector === "fallback" ? " · mic 0 only" : ""}` };
    case "wake_kaldi":
      return { title: d.offset ? "Verification, again" : "Verification", status: d.accepted ? "ok" : "bad", line: `${d.accepted ? `accepted${d.phrase ? ` “${d.phrase}”` : ""}` : wakeRejection(d)}${d.decode_ms !== undefined ? ` in ${took(d.decode_ms / 1000)}` : ""}` };
    case "wake_ratio":
      return { title: "Second opinion", status: d.overturned ? "ok" : "bad", line: `${d.overturned ? "accepted it after all" : "agreed: rejected"}${inTime(d.seconds)}` };
    case "wake_unverified":
      return { title: "Verification", status: "", line: "none for this wake word" };
    case "endpoint":
      return { title: "End of speech", status: "", line: `${num(d.command_seconds, 1)} s of command${d.vad ? ` · ${VAD_NAMES[d.vad] || d.vad}` : ""}${d.others && d.others.at !== undefined ? " · someone else cut in" : ""}` };
    case "speaker":
      return { title: "Speaker", status: d.name ? "ok" : "", line: `${d.name ? `${d.name} (${num(d.score, 2)})` : d.best ? `not sure; closest ${d.best}` : "not recognized"}${inTime(d.seconds)}` };
    case "split":
      return { title: "Command starts", status: "", line: `at ${num(d.cut, 2)} s${d.phrase ? `, after “${d.phrase}”` : ""}${inTime(d.seconds)}` };
    case "typed":
      return { title: "Typed", status: "", line: `${quoted(d.text || "")}${d.spoken ? ", spoken and heard" : ""}` };
    case "replay_of":
      return { title: "Replay", status: "", line: `of ${d.call}` };
    case "recognition":
      if (d.command) return { title: "Recognition", status: "ok", line: `${d.command} ${d.text_source === "typed" && d.matched_by === "ctc" ? "in what was typed" : MATCHED_BY[d.matched_by] || ""}`.trim() + recognitionTime(d) };
      if (d.answer) return { title: "Recognition", status: "ok", line: `a question for the ${d.answer.skill || d.answer.solver || d.answer.kind} skill${recognitionTime(d)}` };
      return { title: "Recognition", status: "warn", line: `no command${recognitionTime(d)}` };
    case "action": {
      const calls = (d.sequence || []).map((x) => x.action || x.service).filter(Boolean);
      const what = d.music ? `music: ${d.music.operation}` : d.timer ? "timer" : calls.length ? calls.join(", ") : "a response only";
      const how = d.error ? "failed" : d.outcome && d.outcome !== "done" ? d.outcome.replace(/_/g, " ") : null;
      return { title: d.dry_run ? "Action (dry run)" : "Action", status: d.error || d.outcome === "failed" ? "bad" : how ? "warn" : d.dry_run ? "" : "ok", line: [how, what].filter(Boolean).join(" · ") + inTime(d.seconds) };
    }
    case "skill_response":
      return { title: "Skill's response", status: d.error ? "warn" : "ok", line: `${d.skill} · ${d.level}${d.template ? ` · your template${inTime(d.seconds)}` : ""}` };
    case "agent": {
      const name = (d.agent || "agent").replace(/^conversation\./, "");
      if (!d.asked) return { title: "Conversation agent", status: "", line: `${name}: not asked (dry run)` };
      if (d.error) return { title: "Conversation agent", status: "bad", line: `${name}: ${d.error}` };
      return { title: "Conversation agent", status: d.understood && d.text ? "ok" : "warn", line: `${name}: ${d.understood ? "answered" : "understood nothing"} in ${took(d.seconds || 0)}` };
    }
    case "unknown":
      return { title: "Unknown command", status: d.error ? "warn" : "", line: `${d.error ? "its response failed: said the default" : "said its response"}${inTime(d.seconds)}` };
    case "speech":
      return { title: "Said", status: "ok", line: (d.text ? quoted(d.text) : d.silent ? "nothing (silent)" : "a short sound") + speechTime(d) };
    case "speech_failed":
      return { title: "Speaking", status: "bad", line: d.error || "failed" };
    case "timer_ringing":
      return { title: "Timer ringing", status: "warn", line: (d.timers || []).map((t) => t.name || `${Math.round(t.created_duration)} s`).join(", ") };
    case "timer_stopped":
      return { title: "Timer stopped", status: "ok", line: d.by === "stop" ? "by “stop” or a call" : d.by === "button" ? "by the center button" : d.by === "timeout" ? "rang out" : d.by || "" };
    case "error":
      return { title: "Error", status: "bad", line: d.error || "" };
    case "timeout":
      return { title: `Timed out after ${num(d.seconds, 0)} s`, status: "bad", line: d.why || "" };
    default:
      return { title: stage.stage, status: "", line: "" };
  }
}

// A step's details: everything recorded about it.
function stepDetails(stage) {
  const { stage: name, at, ...data } = stage;
  if (name === "call_start") {
    return h("div", { class: "small" }, data.name || data.wake_word || "—",
      data.probability !== undefined
        ? ` · probability ${prob(data.probability)} (window average; highest ${prob(data.max_probability)}) against a cutoff of ${prob(data.cutoff)}`
        : " · its firmware doesn't say the probability",
      data.detector ? ` · heard by ${data.detector === "fallback" ? "mic 0 alone (the null missed it), so mic 0 is the call" : "the learn-tv null"}` : "",
      ` · pre-roll ${num(data.preroll, 2)} s`);
  }
  if (name === "wake_kaldi") {
    return h("div", { class: "small" }, data.offset ? `retried ${data.offset} s later · ` : "", data.accepted ? "accepted" : `rejected by ${data.rejected_by}`,
      ` · phrase ${data.phrase || "—"} · wake ${num(data.wake_start)}–${num(data.split)} s · lr ${num(data.lr)} (prefix ${num(data.lr_prefix)}, last ${num(data.lr_last)}) · confusable ${data.confusable_words || "—"} by ${num(data.confusable_margin)} · ${num(data.decode_ms, 0)} ms`);
  }
  if (name === "wake_ratio") return h("div", { class: "small" }, data.overturned ? "overturned" : "agreed with", ` the ${data.rejected_by} rejection · score ${num(data.score)}`);
  if (name === "split") {
    return h("div", { class: "small" }, data.phrase ? `wake phrase “${data.phrase}” at ${data.wake_start}–${data.wake_end} s (CTC)` : "wake phrase not found again; Kaldi's end used",
      ` · Kaldi ended it at ${data.kaldi_split} s · command from ${data.cut} s`);
  }
  if (name === "recognition") return renderRecognition(data);
  if (name === "action") {
    return h("div", {},
      data.outcome ? h("div", { class: "small" }, "Outcome: ", h("code", {}, data.outcome)) : null,
      data.verbosity ? h("div", { class: "small" }, "Said: ", h("code", {}, data.verbosity.level), ` (${data.verbosity.why}`,
        data.verbosity.place ? `; ${data.verbosity.place.here ? "here" : "elsewhere"}${data.verbosity.place.said ? ", " + data.verbosity.place.said : ""}` : "", ")") : null,
      data.timer ? h("div", { class: "small" }, "Timer: ", data.timer.timer ? JSON.stringify(data.timer.timer) : "none matched", " → ", data.timer.speech) : null,
      data.music ? h("div", { class: "small" }, "Music: ", data.music.operation, " · own player ", data.music.own || "none",
        data.music.found ? [" · found ", h("code", {}, data.music.found.uri), ` (${data.music.found.media_type}, score ${data.music.found.score})`] : null) : null,
      data.music && data.music.players ? h("details", {}, h("summary", {}, "Players"), h("pre", {}, JSON.stringify(data.music.players, null, 2))) : null,
      data.sequence && data.sequence.length ? h("div", {}, h("div", { class: "small muted" }, "Sequence"), h("pre", {}, JSON.stringify(data.sequence, null, 2))) : null,
      h("details", {}, h("summary", {}, "Variables sent to Home Assistant"), h("pre", {}, JSON.stringify(data.variables, null, 2))),
      data.response !== null && data.response !== undefined ? h("div", {}, h("div", { class: "small muted" }, "Returned"), h("pre", {}, JSON.stringify(data.response, null, 2))) : null,
      data.error ? h("div", { class: "notice bad" }, data.error) : null);
  }
  if (name === "agent") {
    return h("div", { class: "small" }, !data.asked ? "Not asked: a dry run, and an agent can act in the home." : data.error ? ["Could not be asked: ", data.error] : [
      data.understood ? ["Said “", data.text || "", "”"] : ["Understood nothing (", data.code || data.response_type, "): said the default."],
      h("div", { class: "muted" }, `${data.response_type || ""} in ${took(data.seconds || 0)} · ${data.continued ? "conversation kept from before" : "new conversation"}${data.conversation_id ? ` ${data.conversation_id}` : ""}${data.wants_answer ? " · it asked something back" : ""}`)]);
  }
  if (name === "unknown") return h("div", { class: "small" }, data.error ? ["Could not render it (", data.error, "): said the default."] : data.said ? ["Said “", data.said, "”"] : "Rendered to nothing: said the default.");
  if (name === "skill_response") return h("div", { class: "small" }, data.error ? ["Its template failed (", data.error, "): said the built-in sentence. "] : null, data.said ? ["Said “", data.said, "”"] : null);
  if (name === "speech") {
    return h("div", { class: "small" }, data.text || h("span", { class: "muted" }, data.silent ? "(nothing: it's silent)" : "(a short sound)"),
      data.tts ? h("div", { class: "muted" }, `by ${data.tts}${data.voice ? ` (${data.voice})` : ""} on ${data.on || "the satellite"}`) : null,
      data.tts && (data.requested !== undefined || data.started !== undefined) ? h("div", { class: "muted" },
        [data.requested !== undefined && data.requested !== null ? `Home Assistant took the request in ${took(data.requested)}` : null,
          data.started !== null && data.started !== undefined ? `the satellite started saying it ${took(data.started)} after asking` : data.on ? "no word from the player on when it started" : null,
          data.ended !== null && data.ended !== undefined ? `and ended ${took(data.ended)} after` : null].filter(Boolean).join(", ")) : null,
      data.synthesis !== undefined && data.synthesis !== null ? h("div", { class: "muted" }, `Piper made ${num(data.seconds, 2)} s of speech in ${took(data.synthesis)}`) : null);
  }
  if (name === "endpoint" && data.vad) {
    const s = data.settings || {};
    return h("div", { class: "small" }, `${VAD_NAMES[data.vad] || data.vad}: ${num(data.speech_seconds, 2)} s of speech, ${num(data.silence_seconds, 2)} s of silence at the end`,
      h("div", { class: "muted" }, `speech at ${num(s.speech_threshold)}, silence below ${num(s.silence_threshold)} · ${num(s.speech_seconds)} s to start, ${num(s.silence_seconds)} s to end, ${num(s.resume_seconds)} s to resume`),
      data.others ? h("div", { class: data.others.at !== undefined ? "" : "muted" }, data.others.at !== undefined
        ? `Someone other than ${data.others.speaker} talking by ${num(data.others.at, 2)} s (scored ${data.others.scores.map((v) => num(v, 2)).join(", ")}): ended at their last word, about ${num(data.others.last_word, 2)} s`
        : data.others.speaker ? `Watched for someone other than ${data.others.speaker} (${num(data.others.score, 2)})` : "Nobody identified: not watched for someone else") : null);
  }
  if (name === "timeout") {
    return h("div", { class: "small" }, `${num(data.audio_seconds, 1)} s of audio, the last ${num(data.audio_stopped, 1)} s ago`,
      data.vad ? h("div", { class: "muted" }, `${VAD_NAMES[data.vad] || data.vad}: ${num(data.speech_seconds, 2)} s of speech, ${num(data.silence_seconds, 2)} s of silence at the end` +
        `${data.speech_started ? "" : " (it never started)"}`) : h("div", { class: "muted" }, "Not listening for the command yet."));
  }
  if (name === "replay_of") return h("div", { class: "small" }, "Replays ", h("a", { href: `#/session/${data.call}` }, data.call), " with today's commands.");
  if (name === "speaker") return h("div", { class: "small" }, data.name || "not recognized", data.score !== null && data.score !== undefined ? ` · score ${num(data.score)}` : "", data.best ? ` · closest ${data.best}` : "", data.margin !== null && data.margin !== undefined ? ` · margin ${num(data.margin)}` : "");
  return Object.keys(data).length ? h("div", { class: "small muted" }, Object.entries(data).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")) : null;
}

// One step: when (seconds into the session), its icon, its name and how it
// went; open it for the details. `extra`: more in its details.
function stepRow(stage, before, extra) {
  const glance = stepGlance(stage);
  const status = STATUS[glance.status];
  const at = Number(stage.at) || 0;
  return h("details", { class: `step ${glance.status}` },
    h("summary", {},
      h("span", { class: "step-at", title: `${num(at - before, 2)} s after the step before` }, `+${at.toFixed(2)} s`),
      h("span", { class: "step-icon" }, icon(STEP_ICONS[stage.stage] || "mdi:circle-medium", "", null)),
      h("span", { class: "step-text" }, h("span", { class: "step-title" }, glance.title), glance.line ? h("span", { class: "step-line" }, glance.line) : null),
      status ? icon(status[0], "step-status", status[1]) : h("span", { class: "step-status" })),
    h("div", { class: "step-body" },
      stepDetails(stage),
      extra || null,
      h("details", { class: "raw" }, h("summary", {}, "Raw"), h("pre", {}, JSON.stringify(stage, null, 2)))));
}

// A session's steps, in order. `extras`: more for a step's details, by its name.
function pipeline(record, extras) {
  const stages = record.stages || [];
  return h("div", { class: "steps" }, stages.map((stage, i) => stepRow(stage, i ? Number(stages[i - 1].at) || 0 : 0, (extras || {})[stage.stage])));
}

async function pageSession(id) {
  const [record] = await Promise.all([api("GET", `calls/${id}`), state.satellites.length ? null : loadSatellites().catch(() => null)]);
  const out = h("div");
  async function replay() {
    fill(out, h("p", { class: "muted" }, "Replaying…"));
    try {
      const result = await api("POST", `calls/${id}/replay`, { run_actions: false });
      location.hash = `#/session/${result.id}`;
    } catch (err) { fill(out, errorBox(err)); }
  }
  const rec = stageOf(record, "recognition");
  const heard = rec && rec.recognition ? (rec.recognition.tdt_transcript || rec.recognition.transcript || "") : "";
  // A satellite's audio (not a typed or synthesized test): whose voice it was.
  const voice = record.audio && record.audio.call && record.kind !== "try" ? await whoSpoke(record) : null;
  const took = record.finished ? record.finished - record.started : null;
  fill($main,
    h("a", { href: "#/sessions" }, "← Sessions"),
    h("div", { class: "row session-head" },
      icon(sessionIcon(record), "big", null),
      h("div", { class: "grow" },
        h("h1", { style: "margin:0" }, sessionKind(record)),
        h("div", { class: "muted small" }, [time(record.started), took !== null ? `${num(took, 1)} s` : null, record.dry_run && record.outcome !== "dry run" ? "dry run" : null].filter(Boolean).join(" · "))),
      h("span", { class: `badge ${OUTCOME_CLASS[record.outcome] || ""}` }, record.outcome)),
    h("div", { class: "panel" },
      sessionGlance(record),
      h("div", { class: "row", style: "margin-top:12px" },
        record.audio && record.audio.call ? h("span", { class: "with-hint" }, h("button", { onclick: replay }, "Replay"), hint("Recognizes its audio again with today's commands, as a dry run: nothing happens in Home Assistant.")) : null,
        heard ? h("button", { onclick: () => { pageCommand(null, { description: "", examples: [heard.replace(/^[\s,.;:!?]+/, "").replace(/[.?!]+$/, "")] }); } }, "Make a command") : null,
        record.kept ? h("button", { class: "danger right", onclick: async () => { if (confirm("Delete this session and its audio?")) { await api("DELETE", `calls/${id}`); location.hash = "#/sessions"; } } }, "Delete") : null),
      out),
    h("div", { class: "panel" },
      heading("h2", "Steps", "Seconds since the session started (the wake word, or what was typed). Open a step for everything recorded about it."),
      pipeline(record, { speaker: voice })),
    audioSection(record),
  );
  if (record.outcome === "in progress") poll(async () => { const next = await api("GET", `calls/${id}`); if (next.outcome !== "in progress") { stopPolling(); pageSession(id); } }, 1000);
}

// A session's audio, folded away: drawn when opened.
function audioSection(record) {
  if (!record.audio || !Object.keys(record.audio).length) {
    return h("p", { class: "muted small" }, "No audio kept.", hint("Keep every session (on the Sessions page), or debug its satellite (its page, under Test it), to keep it."));
  }
  const split = stageOf(record, "split");
  const offset = split ? split.cut : 0;
  const body = h("div");
  const section = h("details", { class: "panel fold" },
    h("summary", {}, icon("mdi:chevron-right", "chevron", null), h("h2", {}, "Audio")),
    body);
  section.addEventListener("toggle", () => {
    if (!section.open || body.childElementCount) return;
    fill(body,
      record.audio.call ? [h("h3", {}, "Call (AGC): pre-roll, wake word, command"), waveform(record, "call", recognitionSpans(record, offset))] : null,
      record.audio["call-aec"] ? [h("h3", {}, "Call (AEC)"), waveform(record, "call-aec", [])] : null,
      record.audio.command ? [h("h3", {}, record.kind === "try" ? "Synthesized command" : "Command (endpointing cut)"), waveform(record, "command", record.kind === "try" ? recognitionSpans(record, 0) : [])] : null,
      h("div", { class: "legend" }, ...Object.entries(SPAN_COLORS).map(([role, color]) => h("span", { style: `--c:${color}` }, role))));
  });
  return section;
}

// Adding a session's voice to a speaker's: one already known, or a new one.
// Shown in its Speaker step.
async function whoSpoke(record) {
  let data;
  try { data = await api("GET", "speakers"); } catch (_) { return null; }
  if (!data.enabled) return h("p", { class: "muted small" }, "Speaker identification is off, so no voices are learned.");
  const NEW = "+new"; // never a speaker's name
  const found = stageOf(record, "speaker");
  const pick = found && (found.name || found.best);
  const who = h("select", { id: `who-${record.id}` },
    ...data.speakers.map((sp) => h("option", { value: sp.name, selected: sp.name === pick }, sp.name)),
    h("option", { value: NEW, selected: !data.speakers.length }, "A new speaker…"));
  const name = h("input", { type: "text", placeholder: "Name", "aria-label": "Name" });
  const out = h("div");
  const update = () => { name.style.display = who.value === NEW ? "" : "none"; };
  who.addEventListener("change", update);
  update();
  async function add() {
    fill(out);
    const speaker = who.value === NEW ? name.value.trim() : who.value;
    if (!speaker) { out.append(errorBox("Give the speaker a name")); return; }
    try {
      const added = await api("POST", `calls/${record.id}/speaker`, { name: speaker });
      toast(`Added to ${added.name}'s voice`);
      fill(out, h("p", { class: "notice ok small" }, `${added.speech_s} s of speech added to ${added.name}'s voice, which now has ${added.seconds} s.`,
        added.learned === 0 && who.value === NEW ? " A new voice from one session is a start: add a few more of theirs to make it sure." : ""));
    } catch (err) { out.append(errorBox(err)); }
  }
  return h("div", { style: "margin-top:8px" },
    h("div", { class: "row" }, h("label", { for: who.id }, "This is"), who, name, h("button", { class: "primary", onclick: add }, "Add this voice"),
      hint("Adds the wake word and the command, as the satellite heard them, to that person's voice: they are recognized better from then on. Only add a session you know is theirs.")),
    out);
}

// -------------------------------------------------------------------- test

// Debugging a satellite: every session's audio and trace kept, and maybe a
// dry run (nothing is executed; what would be said is said).
function debugSection(s) {
  const wrap = h("div");
  const dry = h("input", { type: "checkbox", checked: s.debug ? s.debug.dry_run : true });
  const set = async (active) => {
    try {
      await api("POST", `satellites/${s.key}/debug`, { active, dry_run: dry.checked });
      toast(active ? (s.debug ? "Updated" : "Debugging") : "Stopped");
      await Promise.all([refreshStatus(), loadSatellites()]);
      // Only this section: listening live goes on.
      wrap.replaceWith(debugSection(state.satellites.find((x) => x.key === s.key) || s));
    } catch (err) { toast(err.message); }
  };
  fill(wrap,
    h("h3", { class: "with-hint" }, "Debugging", hint("Keeps the audio of every session on this satellite, from the pre-roll before the wake word to the end of the command, with every stage of the pipeline. In a dry run nothing is executed: you hear what would have been said."),
      s.debug ? h("span", { class: "badge warn" }, `on${s.debug.dry_run ? " · dry run" : ""}`) : null),
    h("div", { class: "row" },
      h("label", {}, dry, "Dry run"),
      s.debug ? h("button", { onclick: () => set(false) }, "Stop debugging") : h("button", { class: "primary", onclick: () => set(true) }, "Start debugging"),
      s.debug ? h("button", { onclick: () => set(true) }, "Update") : null,
      h("a", { href: `#/sessions?satellite=${s.key}` }, "Its sessions")));
  return wrap;
}

// Listening to a satellite live: its microphone, heard here and drawn as it
// comes -- the level, the last seconds' waveform -- and what its wake word
// models make of it, each against its cutoff. The satellite streams only
// while someone listens, shows it (the Voice PE's ring blinks red), and does
// not answer a wake word meanwhile: a detection is only marked here.
const LIVE_RATE = 16000;
const LIVE_SECONDS = 6;
const WAKE_ROW = 40;

function liveSection(s) {
  const button = h("button", { class: "primary" }, "Listen");
  const play = h("input", { type: "checkbox", checked: true });
  const note = h("span", { class: "muted small" });
  const level = h("span", { class: "badge", title: "loudness of the last moment: RMS / peak, dB below full scale" }, "— dB");
  const inCall = h("span", { class: "badge accent hidden" }, "in a call");
  const heardNote = h("span", { class: "small", "aria-live": "polite" });
  const wave = h("canvas", { class: "live-wave" });
  const models = h("canvas", { class: "live-wake" });
  const view = h("div", { class: "live hidden" },
    h("div", { class: "row small" }, level, inCall, heardNote, h("span", { class: "grow" }), h("span", { class: "muted" }, `last ${LIVE_SECONDS} s`)),
    wave, models,
    h("div", { class: "legend" },
      h("span", { style: "--c:var(--accent)" }, "probability over its window, what meets the cutoff"),
      h("span", { style: "--c:var(--muted)" }, "each inference"),
      h("span", { style: "--c:var(--warn)" }, "cutoff"),
      h("span", { style: "--c:var(--bad)" }, "detected (not answered)")));
  let socket = null, audio = null, gain = null, drawing = 0, nextTime = 0;
  const recent = new Float32Array(LIVE_RATE * LIVE_SECONDS);
  let written = 0;
  // What each model reported, and when: {id: {name, vad, cutoff, points: [{t, peak, window}]}}.
  let wake = {};
  let detections = [];

  function stop(why) {
    if (socket) { socket.onclose = null; socket.close(); socket = null; }
    if (audio) { audio.close(); audio = null; gain = null; }
    cancelAnimationFrame(drawing);
    button.textContent = "Listen";
    button.className = "primary";
    inCall.classList.add("hidden");
    note.textContent = why || "";
  }
  state.leaving.push(() => stop());

  function heard(buffer) {
    const pcm = new Int16Array(buffer);
    const samples = new Float32Array(pcm.length);
    let sum = 0, peak = 0;
    for (let i = 0; i < pcm.length; i++) {
      const v = pcm[i] / 32768;
      samples[i] = v;
      sum += v * v;
      peak = Math.max(peak, Math.abs(v));
      recent[written++ % recent.length] = v;
    }
    const db = (x) => (x > 0 ? Math.max(-96, 20 * Math.log10(x)) : -96).toFixed(0);
    level.textContent = `${db(Math.sqrt(sum / Math.max(1, pcm.length)))} / ${db(peak)} dB`;
    level.className = `badge ${peak > 0.98 ? "bad" : peak > 0.03 ? "ok" : ""}`;
    // Played a little behind, so the network's jitter is not heard; never
    // more than a second behind.
    const chunk = audio.createBuffer(1, samples.length, LIVE_RATE);
    chunk.copyToChannel(samples, 0);
    const source = audio.createBufferSource();
    source.buffer = chunk;
    source.connect(gain);
    const now = audio.currentTime;
    if (nextTime < now + 0.05 || nextTime > now + 1) nextTime = now + 0.2;
    source.start(nextTime);
    nextTime += chunk.duration;
  }

  function reported(message) {
    const t = performance.now();
    for (const m of message.models) {
      const series = wake[m.id] || (wake[m.id] = { points: [] });
      Object.assign(series, { name: m.name, vad: m.vad, cutoff: m.cutoff });
      series.points.push({ t, peak: m.peak, window: m.window });
      while (series.points.length && series.points[0].t < t - LIVE_SECONDS * 1000) series.points.shift();
    }
    for (const id of message.detected) {
      detections.push({ t, id });
      const name = (wake[id] && wake[id].name) || id;
      heardNote.textContent = `Heard ${name} at ${new Date().toLocaleTimeString()}: not answered while you listen.`;
    }
    detections = detections.filter((d) => d.t >= t - LIVE_SECONDS * 1000);
  }

  function draw() {
    drawing = requestAnimationFrame(draw);
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();
    // The waveform: the last seconds, newest on the right.
    const W = wave.width = Math.round(wave.clientWidth * devicePixelRatio);
    const H = wave.height = Math.round(wave.clientHeight * devicePixelRatio);
    const g = wave.getContext("2d");
    g.clearRect(0, 0, W, H);
    g.fillStyle = color("--muted");
    const per = recent.length / W;
    for (let x = 0; x < W; x++) {
      let lo = 0, hi = 0;
      const from = written - recent.length + Math.floor(x * per);
      for (let i = from; i < from + per; i++) {
        if (i < 0) continue;
        const v = recent[i % recent.length];
        if (v < lo) lo = v; if (v > hi) hi = v;
      }
      g.fillRect(x, H / 2 - hi * H / 2, 1, Math.max(1, (hi - lo) * H / 2));
    }
    // The models: voice activity first, then each wake word, over the same
    // seconds as the waveform.
    const rows = Object.entries(wake).sort(([, a], [, b]) => (b.vad - a.vad) || String(a.name).localeCompare(String(b.name)));
    models.style.height = `${Math.max(1, rows.length) * WAKE_ROW}px`;
    const MW = models.width = Math.round(models.clientWidth * devicePixelRatio);
    const MH = models.height = Math.round(rows.length * WAKE_ROW * devicePixelRatio);
    const m = models.getContext("2d");
    m.clearRect(0, 0, MW, MH);
    const now = performance.now();
    const x = (t) => MW - ((now - t) / (LIVE_SECONDS * 1000)) * MW;
    const rowH = WAKE_ROW * devicePixelRatio;
    rows.forEach(([id, series], row) => {
      const top = row * rowH, inner = rowH - 4 * devicePixelRatio;
      const y = (p) => top + 2 * devicePixelRatio + (1 - p) * inner;
      if (row) { m.fillStyle = color("--line"); m.fillRect(0, top, MW, devicePixelRatio); }
      const points = series.points;
      if (points.length) {
        // Over its window: filled; each inference: a thin line.
        m.beginPath();
        m.moveTo(x(points[0].t), y(0));
        for (const p of points) m.lineTo(x(p.t), y(p.window));
        m.lineTo(x(points[points.length - 1].t), y(0));
        m.closePath();
        m.globalAlpha = 0.35;
        m.fillStyle = color("--accent");
        m.fill();
        m.globalAlpha = 1;
        m.strokeStyle = color("--accent");
        m.lineWidth = 1.5 * devicePixelRatio;
        m.beginPath();
        points.forEach((p, i) => (i ? m.lineTo(x(p.t), y(p.window)) : m.moveTo(x(p.t), y(p.window))));
        m.stroke();
        m.strokeStyle = color("--muted");
        m.lineWidth = devicePixelRatio;
        m.beginPath();
        points.forEach((p, i) => (i ? m.lineTo(x(p.t), y(p.peak)) : m.moveTo(x(p.t), y(p.peak))));
        m.stroke();
      }
      m.strokeStyle = color("--warn");
      m.setLineDash([4 * devicePixelRatio, 3 * devicePixelRatio]);
      m.beginPath();
      m.moveTo(0, y(series.cutoff));
      m.lineTo(MW, y(series.cutoff));
      m.stroke();
      m.setLineDash([]);
      for (const d of detections.filter((d) => d.id === id)) {
        m.fillStyle = color("--bad");
        m.fillRect(x(d.t) - devicePixelRatio, top, 2 * devicePixelRatio, rowH);
      }
      const last = points[points.length - 1];
      const label = `${series.name}  ${last ? Math.round(last.window * 100) : 0}% (cutoff ${Math.round(series.cutoff * 100)}%)`;
      m.font = `${12 * devicePixelRatio}px system-ui, sans-serif`;
      m.globalAlpha = 0.85;
      m.fillStyle = color("--code");
      m.fillRect(3 * devicePixelRatio, top + 3 * devicePixelRatio, m.measureText(label).width + 6 * devicePixelRatio, 15 * devicePixelRatio);
      m.globalAlpha = 1;
      m.fillStyle = color("--text");
      m.fillText(label, 6 * devicePixelRatio, top + 14 * devicePixelRatio);
    });
  }

  function start() {
    try {
      audio = new AudioContext();
    } catch (err) { note.textContent = `This browser can't play it: ${err.message}`; return; }
    gain = audio.createGain();
    gain.gain.value = play.checked ? 1 : 0;
    gain.connect(audio.destination);
    nextTime = 0;
    wake = {};
    detections = [];
    heardNote.textContent = "";
    const url = new URL(`api/satellites/${encodeURIComponent(s.key)}/live`, location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.hash = "";
    socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    button.textContent = "Stop listening";
    button.className = "danger";
    note.textContent = "Connecting…";
    view.classList.remove("hidden");
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") { if (audio) heard(event.data); return; }
      const message = JSON.parse(event.data);
      if (message.type === "start") note.textContent = `Listening: the ring of ${s.name} blinks red while you do.`;
      else if (message.type === "wake") reported(message);
      else if (message.type === "call") inCall.classList.toggle("hidden", !message.active);
      else if (message.type === "error" || message.type === "end") stop(message.error || message.why);
    };
    socket.onclose = () => stop("The connection closed.");
    draw();
  }
  button.addEventListener("click", () => (socket ? stop() : start()));
  play.addEventListener("change", () => { if (gain) gain.gain.value = play.checked ? 1 : 0; });
  const why = !s.connected ? "It is offline." : s.can_listen === false ? "Its firmware can't stream outside calls: flash the newer voice_call firmware to listen live." : "";
  if (why) button.disabled = true;
  return h("div", {},
    heading("h3", "Listen live", "Hear what its microphone hears, now, and see what its voice activity and wake word models make of it. It streams only while you listen, its ring blinks red the whole time, and it doesn't answer a wake word meanwhile: a detection is only marked here. Near the satellite, use headphones or untick “Play it here”: it would hear itself."),
    h("div", { class: "row" }, button, h("label", { class: "small" }, play, "Play it here"), why ? h("span", { class: "muted small" }, why) : note),
    view);
}

// ---------------------------------------------------------------- settings

// Everything about how commands are recognized and answered that is not a
// command: the fallback, the conversation agent, responses; and the YAML.
async function pageSettings() {
  await Promise.all([refreshStatus(), loadSatellites()]);
  fill($main,
    h("h1", {}, "Settings"),
    speechPanel(),
    endOfSpeechPanel(),
    fallbackPanel(),
    agentPanel(),
    responsesPanel(),
    h("div", { class: "panel" },
      h("h2", {}, "Advanced"),
      h("div", { class: "item clickable", onclick: () => { location.hash = "#/yaml"; } },
        icon("mdi:code-braces", "big"),
        h("div", { class: "grow" },
          h("div", { class: "title" }, "Edit commands.yaml"),
          h("div", { class: "muted small" }, "Commands, names and value lists, in one file.")))),
  );
}

// -------------------------------------------------------------------- yaml

async function pageYaml() {
  const data = await api("GET", "yaml");
  const area = h("textarea", { class: "code big" });
  area.value = data.text;
  const errors = h("div");
  fill($main, 
    h("div", { class: "row" }, h("a", { href: "#/settings" }, "← Settings"), heading("h1", "commands.yaml", "Everything the UI edits, in one file. Hand edits made on disk are picked up too. Commands written here may use anything adapt-ctc supports, including entities outside the UI's domains.", { class: "grow", style: "margin:0" }),
      h("button", { class: "primary", onclick: async () => {
        fill(errors, );
        try { await api("PUT", "yaml", { text: area.value }); toast("Saved"); refreshStatus(); }
        catch (err) { errors.append(errorBox(err)); }
      } }, "Save")),
    errors,
    h("div", { class: "panel" }, area),
  );
}

// ------------------------------------------------------------------ router

async function route() {
  stopPolling();
  const hash = location.hash || "#/home";
  const [path, query] = hash.slice(2).split("?");
  const [page, ...rest] = path.split("/");
  const arg = rest.join("/");
  for (const link of document.querySelectorAll("#nav a")) {
    const parent = { command: "home", skill: "home", satellite: "home", unknown: "home", fallback: "settings", yaml: "settings", session: "sessions" }[page];
    link.classList.toggle("active", link.dataset.page === page || link.dataset.page === parent);
  }
  try {
    if (page === "home") await pageHome();
    else if (page === "command") await pageCommand(arg || null, null, query);
    else if (page === "skill") await pageSkill(arg, query);
    else if (page === "satellite") await pageSatellite(arg);
    else if (page === "unknown") await pageUnknown();
    else if (page === "fallback") await pageFallback();
    else if (page === "library") await pageLibrary();
    else if (page === "names") await pageNames();
    else if (page === "wake") await pageWakeWords(query);
    else if (page === "speakers") await pageSpeakers();
    else if (page === "sessions") await pageSessions(query);
    else if (page === "session") await pageSession(arg);
    // Old links: the Commands, Skills, Satellites and Test pages (now the
    // Home and each satellite's), the Calls and Try pages.
    else if (page === "test" || page === "try") {
      const key = new URLSearchParams(query || "").get("satellite");
      location.hash = key ? `#/satellite/${key}` : "#/home";
    }
    else if (["commands", "skills", "satellites", "group"].includes(page)) location.hash = "#/home";
    else if (page === "calls") location.hash = `#/sessions${query ? `?${query}` : ""}`;
    else if (page === "call") location.hash = `#/session/${arg}`;
    else if (page === "settings") await pageSettings();
    else if (page === "yaml") await pageYaml();
    else await pageHome();
  } catch (err) {
    fill($main, errorBox(err));
  }
}

window.addEventListener("hashchange", route);
refreshStatus();
setInterval(refreshStatus, 5000);
route();
