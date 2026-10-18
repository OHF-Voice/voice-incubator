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

function icon(name, cls) {
  const path = ICONS[name] || ICONS["mdi:message-text-outline"];
  return h("span", { class: `icon ${cls || ""}`, title: name, html: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>` });
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

function field(label, control, help) {
  return h("div", { class: "field" }, h("span", { class: "label" }, label), control, help ? h("span", { class: "help" }, help) : null);
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
  if (Object.keys(s.debug_sessions || {}).length) badges.push(h("a", { class: "badge warn", href: "#/test" }, "debugging a satellite"));
  if (s.settings && s.settings.keep_audio) badges.push(h("span", { class: "badge warn" }, "keeping audio"));
  document.getElementById("status").replaceChildren(...badges);
  const banner = document.getElementById("banner");
  const notes = [];
  if (s.store_error) notes.push(h("div", { class: "notice bad" }, "commands.yaml has an error, so the previous commands are still in use: ", s.store_error));
  if (s.integration && s.integration !== "ready" && s.integration !== "connecting") notes.push(h("div", { class: "notice warn" }, s.integration));
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

// Where a command group is given: "whole home", satellites by name.
function groupUseText(groupId, use, sats) {
  const parts = [];
  if ((use.home || []).includes(groupId)) parts.push("whole home");
  for (const [key, ids] of Object.entries(use.satellites || {})) if (ids.includes(groupId)) parts.push(sats[key] || key);
  return parts.length ? `given to ${parts.join(", ")}` : "not given to anything yet";
}

function groupsPanel(store, sats) {
  const groups = store.groups || {};
  const use = store.group_use || {};
  const ids = Object.keys(groups);
  const members = (g) => Object.values(store.commands || {}).filter((spec) => asList(spec && spec.groups).includes(g)).length;
  return h("div", { class: "panel" },
    h("div", { class: "row" }, h("h2", { class: "grow", style: "margin:0" }, "Command groups"),
      h("button", { onclick: () => { location.hash = "#/group/"; } }, "New group")),
    h("p", { class: "muted small" }, "A group given to the whole home or to a satellite limits it to the commands in the group. A satellite with groups of its own uses only those; without any groups, every command is available everywhere. Give groups on the Satellites page."),
    ids.length ? h("div", { class: "list" }, ids.map((g) => h("div", { class: "item clickable", onclick: () => { location.hash = `#/group/${g}`; } },
      icon("mdi:folder-outline", "big"),
      h("div", { class: "grow" },
        h("div", { class: "title" }, groups[g] && groups[g].name || g),
        h("div", { class: "muted small" }, g, groups[g] && groups[g].description ? ` · ${groups[g].description}` : "", ` · ${groupUseText(g, use, sats)}`)),
      h("span", { class: "badge" }, `${members(g)} command${members(g) === 1 ? "" : "s"}`)))) : null);
}

// What happens when nothing matched: the closest command by meaning, and
// the LLM -- each only for the commands chosen for it.
// How much a command says when it's done (commands.VERBOSITY).
const LEVELS = [
  ["silent", "Silent: nothing, not even a beep"],
  ["beep", "Beep"],
  ["brief", "Brief: a fixed sentence"],
  ["specific", "Specific: what was heard"],
  ["verbose", "Verbose: everything it did"],
];
const levelName = (value) => ({ silent: "silent", beep: "beep", brief: "brief", specific: "specific", verbose: "verbose" }[value] || value);

// What a command that beeps says when it acted out of sight.
function responsesPanel() {
  const verbosity = (state.status && state.status.verbosity) || {};
  const select = h("select", { onchange: async (e) => {
    try { await api("PUT", "settings", { beep_elsewhere: e.target.value }); toast("Saved"); await refreshStatus(); }
    catch (err) { toast(err.message); }
  } }, ...LEVELS.filter(([value]) => value !== "silent").map(([value, label]) => h("option", { value, selected: (verbosity.beep_elsewhere || "specific") === value }, value === "beep" ? "Beep anyway" : label)));
  return h("div", { class: "panel" },
    h("h2", {}, "Responses"),
    h("p", { class: "muted small" }, "Each command says as much as its own level when it's done: a beep, a brief fixed sentence (“Turned on the lights.”), a specific one (“Turned on the lights in the Kitchen.”) or a verbose one (“Turned on Ceiling and Lamp in the Kitchen.”). Lights beep; timers, locks and lists say what they did. Set it on the command's page."),
    field("A command that beeps, when it acted somewhere else", select, "A beep is enough where you can see it happen: in the satellite's own area. Somewhere else — another area, a floor, a device in no area — it says this instead. Home Assistant's own says the brief one."));
}

function fallbackPanel() {
  const status = (state.status && state.status.fallback) || {};
  const models = status.models || {};
  const meaning = h("input", { type: "checkbox", checked: status.meaning !== false });
  const llm = h("input", { type: "checkbox", checked: status.llm !== false });
  const model = h("select", {}, Object.entries(models).map(([id, m]) => h("option", { value: id, selected: status.model === id },
    `${m.name} (${m.mb} MB, ${m.licence})${m.installed === false ? (m.kind === "gliner" ? " — not exported" : " — llama.cpp missing") : ""}`)));
  const topK = h("input", { type: "number", min: 1, max: 64, value: status.top_k || 8, style: "width:70px" });
  const threshold = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: status.threshold ?? 0.3, style: "width:80px" });
  const isGliner = () => (models[model.value] || {}).kind === "gliner";
  const thresholdField = field("Least sure it may be", threshold, "GLiNER's probability for the command it picks, which must also beat “not a command”. Higher refuses more.");
  const showThreshold = () => { thresholdField.style.display = isGliner() ? "" : "none"; };
  model.addEventListener("change", showThreshold);
  showThreshold();
  const count = (n) => `${n || 0} command${n === 1 ? "" : "s"} chosen`;
  async function save() {
    try {
      await api("PUT", "settings", { fallback_meaning: meaning.checked, fallback_llm: llm.checked, fallback_model: model.value,
        fallback_top_k: Number(topK.value), fallback_threshold: Number(threshold.value) });
      toast("Saved"); await refreshStatus(); pageSettings();
    } catch (err) { toast(err.message); }
  }
  return h("div", { class: "panel" },
    h("div", { class: "row" }, h("h2", { class: "grow", style: "margin:0" }, "When nothing matches"),
      h("button", { onclick: () => { location.hash = "#/fallback"; } }, "Choose commands")),
    h("p", { class: "muted small" }, "Keywords and example sentences come first. When they find nothing, these can run a command — only one chosen for it, here or on the command's page:"),
    h("label", {}, meaning, "Run the command closest in meaning, when it is very close (“kill the lights in the office”); the model hears the room",
      h("span", { class: "badge" }, count(status.meaning_commands)),
      status.meaning_available === false ? h("span", { class: "badge" }, "needs the embedding model") : null),
    h("label", {}, llm, "Let a small model pick a command and fill in what it needs (“I'm in the mood for jazz”)",
      h("span", { class: "badge" }, count(status.llm_commands)),
      h("span", { class: "badge" }, status.llm_status || "off")),
    h("div", { class: "row" },
      field("Model", model, "GLiNER hears what was said in the command meaning picked, and otherwise picks a command itself, or “not a command”, in about a quarter of a second on a Raspberry Pi 5. A command's versions (on this satellite, a device, an area or floor) are one choice to it: what it hears picks the version. An LLM is slower and wrong more often; Hammer is licensed for non-commercial use only."),
      field("Commands it chooses from", topK, "The closest in meaning. GLiNER is given commands that are not chosen too, to tell the chosen ones from, and runs only a chosen one."),
      thresholdField,
      h("button", { class: "primary", onclick: save }, "Save")));
}

// Which commands the steps after keywords and examples may run.
async function pageFallback() {
  const store = await loadStore();
  const fallback = (state.status && state.status.fallback) || {};
  const glinerModel = ((fallback.models || {})[fallback.model] || {}).kind === "gliner";
  const errors = h("div");
  const filter = h("input", { type: "search", placeholder: "Filter commands", style: "max-width:260px" });
  const rows = Object.entries(store.commands || {}).map(([cid, spec]) => {
    const steps = asList(spec && spec.fallback);
    const needsSaid = !!(store.details[cid] || {}).needs_said;
    const meaning = h("input", { type: "checkbox", checked: steps.includes("meaning"),
      title: needsSaid ? "Meaning picks it; the model hears what was said in it (a name, number, value or text)" : "" });
    // GLiNER picks only commands that hear something: meaning takes the rest.
    const glinerSkips = glinerModel && !needsSaid;
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
      h("div", { class: "row" }, h("h2", { class: "grow", style: "margin:0" }, "Commands"), filter,
        h("button", { onclick: () => all("meaning", true) }, "All by meaning"), h("button", { onclick: () => all("llm", true) }, "All by model"),
        h("button", { onclick: () => { all("meaning", false); all("llm", false); } }, "None")),
      h("p", { class: "muted small" }, "When keywords and example sentences find nothing, “meaning” may run a command a sentence is very close to (the model hears the names, numbers or text said in it), and “model” lets a small model pick it and fill in what it needs (GLiNER picks only commands that hear something). A command not chosen is never run by them."),
      h("div", { class: "list" }, rows.map((r) => r.row))),
    h("div", { class: "row" }, h("span", { class: "grow" }), h("button", { class: "primary", onclick: save }, "Save")),
  );
}

// What nothing here understood: a Home Assistant conversation agent, in a
// conversation kept on each satellite.
function agentPanel() {
  const status = (state.status && state.status.agent) || {};
  const agent = h("select", {}, h("option", { value: "" }, "None"),
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
    h("h2", {}, "Conversation agent"),
    h("p", { class: "muted small" }, "What no command, ", h("a", { href: "#/skills" }, "skill"), " or step under “When nothing matches” took can go to one of Home Assistant's conversation agents — its own, or an LLM's (OpenAI, Google, Ollama …) — and its answer is said. “Can't look it up” waits for it: the agent may know the weather. What it doesn't understand either is the ", h("a", { href: "#/unknown" }, "Unknown command"), "'s."),
    h("div", { class: "row" },
      field("Agent", agent, note),
      field("Keep a conversation for", h("label", {}, keep, "minutes"), "After its last answer on a satellite, the next sentence there continues the conversation (“how tall is it?”), as on the Voice PE. 0: every sentence starts a new one."),
      h("button", { class: "primary", onclick: save }, "Save")),
    kept.length ? h("p", { class: "small" }, "Conversations kept: ", kept.map(([key, seconds]) => `${sats[key] || key || "the Test page"} (${Math.round(seconds)} s more)`).join(", ")) : null,
    h("p", { class: "muted small" }, "A dry run never asks it, since an agent can act in your home: tick “Really run” on the Test page to try it. An LLM agent hears what the speech model wrote, mishearings and all."));
}

// Skills: questions answered without an LLM, each on or off for the whole
// home, and per satellite.
async function pageSkills() {
  const [data] = await Promise.all([api("GET", "skills"), loadSatellites()]);
  const sats = state.satellites || [];
  async function put(id, body) {
    try { await api("PUT", `skills/${id}`, body); toast("Saved"); await pageSkills(); }
    catch (err) { toast(err.message); }
  }
  const card = (skill) => {
    const homeOn = (data.home || {})[skill.id] !== false;
    const home = h("input", { type: "checkbox", checked: homeOn, onchange: (e) => put(skill.id, { home: e.target.checked }) });
    const perSatellite = sats.map((sat) => {
      const own = ((data.satellites || {})[sat.key] || {})[skill.id];
      const choice = h("select", { onchange: (e) => put(skill.id, { satellites: { [sat.key]: e.target.value === "" ? null : e.target.value === "on" } }) },
        h("option", { value: "", selected: own === undefined }, `Default (${homeOn ? "on" : "off"})`),
        h("option", { value: "on", selected: own === true }, "On"),
        h("option", { value: "off", selected: own === false }, "Off"));
      return h("label", { class: "skill-satellite" }, h("span", { class: "grow" }, sat.name), choice);
    });
    return h("div", { class: "panel" },
      h("div", { class: "row", style: "align-items:flex-start" },
        icon(skill.icon, "big"),
        h("div", { class: "grow" },
          h("h2", { style: "margin:0" }, skill.name),
          h("div", { class: "muted small" }, skill.description),
          h("div", { class: "small" }, skill.examples.map((e) => `“${e}”`).join(" · "))),
        h("label", {}, home, "Whole home")),
      sats.length ? h("div", { class: "skill-satellites" }, perSatellite) : null);
  };
  fill($main,
    h("h1", {}, "Skills"),
    h("p", { class: "muted" }, "Questions no command takes, answered exactly and at once, without an LLM: a skill parses the whole question and computes the answer, or leaves it to the next step. Turn each on or off for the whole home, and give a satellite its own setting where it should differ."),
    h("p", { class: "muted small" }, "They answer before the steps after keywords and examples (meaning, the model) — except “Can't look it up”, which comes after them, so “do I need an umbrella” can still be your weather command, and after the conversation agent when there is one (Settings page)."),
    ...data.skills.map(card));
}

// Commands as the library has them -- Basics, Covers and locks, Music,
// Timers -- under your own (Custom), which come first.
async function pageCommands() {
  const [store] = await Promise.all([loadStore(), loadSatellites()]);
  const sats = satelliteLabels();
  const ids = Object.keys(store.commands || {});
  const groupNames = Object.fromEntries(Object.entries(store.groups || {}).map(([g, spec]) => [g, (spec && spec.name) || g]));
  const bySection = {};
  for (const id of ids) {
    const section = (store.details[id] || {}).library || "";
    (bySection[section] = bySection[section] || []).push(id);
  }
  const item = (id) => {
    const spec = store.commands[id] || {};
    const detail = store.details[id] || {};
    return h("div", { class: "item clickable", onclick: () => { location.hash = `#/command/${id}`; } },
      icon(iconFor(spec), "big"),
      h("div", { class: "grow" },
        h("div", { class: "title" }, spec.title || spec.description || id, spec.disabled ? h("span", { class: "badge" }, " off") : null),
        h("div", { class: "muted small" }, id, " · ", spec.description || "", listensText(spec) ? ` · ${listensText(spec)}` : "")),
      ...asList(spec.groups).map((g) => h("span", { class: "badge", title: "command group" }, groupNames[g] || g)),
      asList(spec.fallback).length ? h("span", { class: "badge", title: "may be run when nothing else matches" }, asList(spec.fallback).map((f) => (f === "llm" ? "model" : f)).join(" + ")) : null,
      detail.error ? h("span", { class: "badge bad", title: detail.error }, "error") : h("span", { class: "badge accent" }, detail.does || ""));
  };
  const section = (title, iconName, members, empty, first) => h("div", { class: "panel" },
    h("h2", { class: "row" }, icon(iconName), title, h("span", { class: "badge" }, String(members.length))),
    h("div", { class: "list" }, first, members.map(item)), members.length ? null : empty);
  // Always there: what is said when nothing understood a sentence.
  const unknown = (store.unknown || {}).response;
  const unknownItem = h("div", { class: "item clickable", onclick: () => { location.hash = "#/unknown"; } },
    icon("mdi:help-circle-outline", "big"),
    h("div", { class: "grow" },
      h("div", { class: "title" }, "Unknown command"),
      h("div", { class: "muted small" }, unknown ? `Says “${unknown}”` : "What is said when nothing understood a sentence. Default: the closest command (“It looks like you're trying to …”), or “Sorry, I didn't understand that.”")),
    h("span", { class: "badge accent" }, unknown ? "respond" : "default"));
  fill($main,
    h("div", { class: "row" }, h("h1", { class: "grow" }, "Commands"),
      h("button", { class: "primary", onclick: () => newCommandDialog() }, "New command")),
    store.error ? h("div", { class: "notice bad" }, store.error) : null,
    ids.length ? groupsPanel(store, sats) : null,
    section("Custom", "mdi:pencil-outline", bySection[""] || [],
      h("p", { class: "muted small" }, ids.length
        ? "Commands you make from scratch (New command, then Custom command) are listed here; the library's are below, as the library has them."
        : "No voice commands yet. Press New command: start from one in the library, or make your own — pick what it targets, give example sentences or keywords, and choose what happens."),
      unknownItem),
    ...(store.sections || []).filter((s) => (bySection[s.file] || []).length)
      .map((s) => section(s.title, s.icon || "mdi:folder-outline", bySection[s.file])),
  );
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
      h("a", { href: "#/commands" }, "← Commands"),
      h("h1", { class: "grow", style: "margin:0" }, "Unknown command"),
      h("button", { class: "primary", onclick: save }, "Save")),
    errors,
    h("div", { class: "panel" },
      h("p", { class: "muted" }, "When nothing understood a sentence — no command, no skill, nothing that runs when nothing matches, nor the conversation agent — the satellite says this. Left empty, it says the closest command (“It looks like you're trying to …”), or “Sorry, I didn't understand that.” Something understood but impossible as said (“the fan in the office” where there is none) still says why."),
      field("Response", area, "A Home Assistant template. Something that renders to nothing says the default."),
      h("div", { class: "row small" }, h("span", { class: "muted" }, "Start from:"),
        ...UNKNOWN_EXAMPLES.map((example) => h("button", { type: "button", class: "link", onclick: () => { area.value = example; area.focus(); } }, h("code", {}, example)))),
      h("h3", {}, "Variables"),
      h("div", { class: "kv" }, ...vars.flatMap(([name, text]) => [h("div", {}, h("code", {}, name)), h("div", {}, text)])),
      h("p", { class: "muted small" }, "Type a sentence no command takes on the Test page to hear it.")),
  );
}

// A command group: its name, and which commands are in it.
async function pageGroup(id) {
  const [store] = await Promise.all([loadStore(), loadSatellites()]);
  const sats = satelliteLabels();
  const isNew = !id;
  const info = isNew ? { name: "", description: "" } : (store.groups || {})[id];
  if (!info && !isNew) { fill($main, errorBox(`No command group ${id}`)); return; }
  let groupId = id || "";
  const name = h("input", { type: "text", value: (info && info.name) || "", placeholder: "Kids' room", oninput: (e) => { if (isNew && !idInput.dataset.touched) { groupId = slug(e.target.value); idInput.value = groupId; } } });
  const idInput = h("input", { type: "text", value: groupId, disabled: !isNew, oninput: (e) => { idInput.dataset.touched = "1"; groupId = slug(e.target.value); } });
  const description = h("input", { type: "text", value: (info && info.description) || "", style: "width:100%", placeholder: "what the kids may ask for" });
  const errors = h("div");
  const filter = h("input", { type: "search", placeholder: "Filter commands", style: "max-width:260px" });
  const rows = Object.entries(store.commands || {}).map(([cid, spec]) => {
    const box = h("input", { type: "checkbox", value: cid, checked: !isNew && asList(spec && spec.groups).includes(id) });
    const row = h("label", { class: "item" }, box, icon(iconFor(spec || {}), "big"),
      h("div", { class: "grow" }, h("div", { class: "title" }, (spec && (spec.title || spec.description)) || cid), h("div", { class: "muted small" }, cid)));
    return { cid, spec, box, row };
  });
  filter.addEventListener("input", () => {
    const text = filter.value.trim().toLowerCase();
    for (const r of rows) r.row.style.display = !text || r.cid.includes(text) || String((r.spec && (r.spec.title || r.spec.description)) || "").toLowerCase().includes(text) ? "" : "none";
  });
  const all = (on) => rows.forEach((r) => { if (r.row.style.display !== "none") r.box.checked = on; });
  async function save() {
    fill(errors);
    if (!groupId) { errors.append(errorBox("Give the group an id")); return; }
    try {
      await api("PUT", `groups/${groupId}`, { name: name.value, description: description.value, commands: rows.filter((r) => r.box.checked).map((r) => r.cid) });
      toast("Saved"); refreshStatus();
      if (isNew) location.hash = `#/group/${groupId}`; else pageGroup(groupId);
    } catch (err) { errors.append(errorBox(err)); }
  }
  async function remove() {
    if (!confirm(`Delete the command group ${name.value || id}? Its commands stay, in no group.`)) return;
    try { await api("DELETE", `groups/${id}`); toast("Deleted"); location.hash = "#/commands"; }
    catch (err) { errors.append(errorBox(err)); }
  }
  fill($main,
    h("div", { class: "row" },
      h("a", { href: "#/commands" }, "← Commands"),
      h("h1", { class: "grow", style: "margin:0" }, isNew ? "New command group" : (info.name || id)),
      isNew ? null : h("button", { class: "danger", onclick: remove }, "Delete"),
      h("button", { class: "primary", onclick: save }, "Save")),
    errors,
    h("div", { class: "panel" },
      h("div", { class: "grid2" }, field("Name", name), field("Id", idInput, "Lowercase letters, digits and underscores.")),
      field("Description", description),
      isNew ? null : h("p", { class: "muted small" }, (([first, ...rest]) => first.toUpperCase() + rest.join(""))(groupUseText(id, store.group_use || {}, sats)), ". Give it on the Satellites page.")),
    h("div", { class: "panel" },
      h("div", { class: "row" }, h("h2", { class: "grow", style: "margin:0" }, "Commands in it"), filter,
        h("button", { onclick: () => all(true) }, "All"), h("button", { onclick: () => all(false) }, "None")),
      h("p", { class: "muted small" }, "A command can be in any number of groups."),
      h("div", { class: "list" }, rows.map((r) => r.row))),
    h("div", { class: "row" }, h("span", { class: "grow" }), h("button", { class: "primary", onclick: save }, "Save")),
  );
}

// The command editor. `spec` is edited in place; fields it does not know
// about are kept as loaded.
async function pageCommand(id, prefill) {
  await loadStore();
  const areas = await areaLabels();
  const isNew = !id;
  let spec = isNew ? (prefill || { description: "", examples: [] }) : clone(state.store.commands[id]);
  if (!spec) { fill($main, errorBox(`No command ${id}`)); return; }
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
    location.hash = "#/commands";
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
    const current = () => (target.script ? "script" : target.scene ? "scene" : target.timer ? "timer" : target.music ? "music"
      : (yamlBox.value || "").trim() ? "action" : respondsWhenDone(target.response) ? "respond" : "nothing");
    let mode = current();
    function render() {
      const choices = [["respond", "Only respond"], ["script", "Run a script"], ["scene", "Activate a scene"], ["action", "Run an action"], ["timer", "A timer"], ["music", "Music"], ["nothing", "Nothing"]];
      const radios = h("div", { class: "row" }, ...choices.map(([value, label]) => h("label", {},
        h("input", { type: "radio", name: `do-${Math.random()}`, checked: mode === value, onchange: () => {
          // Responses for outcomes only the old kind had go (a script's own stay with an action).
          const scripted = (m) => m === "script" || m === "action";
          if (!(scripted(mode) && scripted(value))) keepOutcomes(value === "nothing" ? [] : value === "respond" ? ["done"] : ["done", "failed"]);
          mode = value;
          delete target.script; delete target.scene; delete target.timer; delete target.music;
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
        const ops = { start: "Start a timer", cancel: "Cancel a timer", cancel_all: "Cancel every timer", pause: "Pause a timer", resume: "Resume a timer", add: "Add time", subtract: "Take time off", status: "Say how long is left" };
        control = field("Timer", h("select", { onchange: (e) => { target.timer = e.target.value; keepOutcomes([...Object.keys(timerOutcomes()), "failed"]); render(); } },
          ...Object.entries(ops).map(([op, label]) => h("option", { value: op, selected: target.timer === op }, label))),
          "On the satellite's own timers (its voice_timer_list in Home Assistant). Which timer: slots.timer_name (a running timer's name, from the running_timers list), slots.timer (free text, matched loosely), slots.duration, or the only one.");
      } else if (mode === "music") {
        const ops = { play: "Play what was asked for", add: "Add it to the queue", radio: "Play music like it (or like what's playing)", pause: "Pause", resume: "Resume", stop: "Stop", next: "Next track", previous: "Previous track", volume_up: "Volume up", volume_down: "Volume down", volume_set: "Set the volume", mute: "Mute", unmute: "Unmute", shuffle_on: "Shuffle on", shuffle_off: "Shuffle off", repeat_all: "Repeat all", repeat_one: "Repeat this track", repeat_off: "Repeat off", status: "Say what's playing", move: "Move it to the player named (area, floor or speaker)" };
        control = field("Music", h("select", { onchange: (e) => { target.music = e.target.value; keepOutcomes([...Object.keys(musicOutcomes()), "failed"]); render(); } },
          ...Object.entries(ops).map(([op, label]) => h("option", { value: op, selected: target.music === op }, label))),
          "On a Music Assistant player: one the command names (Target, or slots.area), else the satellite's own (its Sendspin player), or, to pause, skip or ask what's playing, whichever plays in the satellite's area. Playing searches Music Assistant for slots.query (free text: “play …”); slots.media_type (artist, album, track, playlist, radio) narrows it. Set the volume takes a percentage.");
      } else if (mode === "action") {
        const area = h("textarea", { class: "code", oninput: (e) => { yamlBox.value = e.target.value; } });
        area.value = yamlBox.value;
        control = field("Action (Home Assistant script syntax; one action or a list)", area,
          "Templates see slots, satellite, speaker, target and command. target: \"{{ target }}\" is what was named, or else the satellite's area.");
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
    function doneEditor(placeholder, levelDefaults, big) {
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
        return field(h("span", {}, "When it's done ", toggle), area);
      }
      const boxes = LEVELS.filter(([level]) => level !== "beep" && level !== "silent").map(([level, label]) => {
        const area = h("textarea", { class: "short", placeholder: (levelDefaults && levelDefaults[level]) || (level === "brief" ? "A fixed sentence, e.g. Turned on the lights." : ""), oninput: (e) => {
          const next = responses();
          const d = next.done && typeof next.done === "object" ? { ...next.done } : {};
          if (e.target.value) d[level] = e.target.value; else delete d[level];
          next.done = d;
          setResponses(next);
        } });
        area.value = done && typeof done === "object" ? done[level] || "" : "";
        return field(label, area);
      });
      return h("div", {}, h("div", { class: "label" }, "When it's done ", toggle),
        h("div", { class: "grid2" }, ...boxes),
        h("span", { class: "help" }, "A level without one says the closest written (less first). Keep the brief one fixed — no templates — so text-to-speech can cache it."));
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
        return h("p", { class: "muted small" }, "It ends the conversation and does nothing else (“never mind”). To say something, choose Only respond; for the acknowledgement sound, set How much it says to Beep.");
      }
      if (mode === "respond") {
        return h("div", {}, doneEditor("Spoken response template, e.g. It's {{ now().strftime('%H:%M') }}", null, true), h("span", { class: "help" }, vars));
      }
      const rows = [];
      if (mode === "music") {
        const said = musicOutcomes();
        const levels = (info.music_levels || {})[target.music];
        const labels = { no_player: "No music player", nothing_playing: "Nothing playing", not_found: "Nothing found", missing_query: "Nothing asked for", missing_volume: "No volume heard", already: "Already playing" };
        for (const [key, example] of Object.entries(said)) {
          if (key !== "done") rows.push(field(labels[key] || key, box(key, example || "(a short sound)", false)));
        }
        rows.push(field("If it fails", box("failed", info.failed, false), "Home Assistant or Music Assistant refused."));
        return h("div", {}, h("div", { class: "label" }, "Responses"),
          h("p", { class: "muted small" }, "Each outcome says its own sentence (shown) unless you give one here. Templates also see player (the one it acted on), players and, when it searched, media (name, artist, media_type, uri).", " ", vars),
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
        return h("div", {}, h("div", { class: "label" }, "Responses"),
          h("p", { class: "muted small" }, "Each outcome says its own sentence (shown) unless you give one here. Templates also see timer (the timer it was about) and timers.", " ", vars),
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
          h("span", { class: "help" }, "A script picks one by returning outcome (stop with response_variable: {outcome: nobody_home})."));
      })() : null;
      return h("div", {}, doneEditor(mode === "script" ? "Spoken response template; without one, the script's speech is said" : "Spoken response template, e.g. Turned on the lights {{ place.said }}", null, false),
        h("div", { class: "grid2" }, ...rows), extra,
        h("p", { class: "muted small" }, "Optional. ", vars, " place says where it acted: place.said (“in the Kitchen”), place.here, place.areas. Nothing to say plays a short sound."));
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
              h("div", { class: "small muted" }, `No ${plural} have names yet — add them on the Names page. Names are never taken from Home Assistant automatically.`),
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
      fill(wrap, h("div", { class: "row" }, select, ...extra),
        preset === "duration" ? h("p", { class: "small muted" }, "Heard as “ten minutes”, “an hour and a half”: slots.duration has hours, minutes, seconds, total_seconds and text (HH:MM:SS).") : null,
        preset === "percentage" ? h("p", { class: "small muted" }, "0–100, multiples of 10 preferred where the audio is unsure (fifteen vs fifty).") : null);
    }
    render();
    return wrap;
  }

  function listsEditor() {
    const wrap = h("div");
    const dynamicLists = (state.status && state.status.dynamic_lists) || {};
    const sharedLists = [...Object.keys(state.store.lists || {}), ...Object.keys(dynamicLists)];
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
        h("p", { class: "small muted" }, "Free text is re-read from the TDT transcript for spelling. Keep it bounded: the fewer words it may take, the fewer mishearings it swallows."),
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
    if (!slots.length) { fill(combosWrap, h("p", { class: "muted small" }, "It listens for no names, number, values or text: there is one way to say it.")); return; }
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
        Object.keys(state.store.groups || {}).length ? field("Command groups", h("div", { class: "wake-list" },
          Object.entries(state.store.groups || {}).map(([g, info]) => h("label", {},
            h("input", { type: "checkbox", checked: asList(spec.groups).includes(g), onchange: (e) => {
              const now = asList(spec.groups).filter((x) => x !== g);
              if (e.target.checked) now.push(g);
              if (now.length) spec.groups = now; else delete spec.groups;
            } }), (info && info.name) || g))),
          "A satellite limited to command groups runs only the commands in them (Satellites page).") : null,
        field("When nothing else matches", h("div", { class: "wake-list" },
          [["meaning", "Run it when a sentence is very close in meaning"], ["llm", "Let a small model pick it"]].map(([step, label]) => h("label", {},
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
      h("div", { class: "panel" }, h("h2", {}, "Target"),
        h("p", { class: "muted small" }, "What the command acts on, passed to Home Assistant as slots as if it had been heard. The phrase can then be anything."),
        targetEditor(spec)),
      h("div", { class: "panel" }, h("h2", {}, "Listen for"),
        ((info) => field("Example sentences", h("div", {},
          chipsInput(spec.examples || [], (v) => { spec.examples = v; info.show(v); }, "type a whole sentence and press Enter"), info.box),
          "Said word for word, an example always matches, ahead of keywords — when it's all that was said: “pause” isn't “pause the timer”. Write (a|b) for either and [a] for what may be left out, inside a word too: “(turn|switch) on the light[s]” is four sentences. No placeholders: slots come from keywords below."))(examplesInfo(spec.examples || [])),
        h("h3", {}, "Keywords"),
        h("p", { class: "muted small" }, "Every required group must be heard, by any one of its alternatives, in any order. Write “turn … on” for words that may be split (“turn the lights on”)."),
        field("Required", groupsEditor(spec.require, (v) => { spec.require = v; }, "Add a required group")),
        field("Optional", groupsEditor(spec.optional, (v) => { spec.optional = v; }, "Add an optional group"), "Words that may be said and should not count against the match."),
        field("Must not hear", chipsInput(spec.exclude || [], (v) => { spec.exclude = v; }, "is, are"), "A statement is not a command: “the door is locked”."),
        h("h3", {}, "Names"), namesEditor(),
        h("h3", {}, "Number"), numberEditor(),
        h("h3", {}, "Value lists"), listsEditor(),
        h("h3", {}, "Free text"), wildcardEditor(),
        h("h3", {}, "Ways to say it"),
        h("p", { class: "muted small" }, "What it hears is never optional: each way below is said whole, and a command never runs with something said left out. “Turn off the lights” could be said with nothing named (the satellite's area), with an area, or with a floor. The first way gets the example sentences."),
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
      h("a", { href: "#/commands" }, "← Commands"),
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

// Duplicate a command, as saved: a copy to change, put in a command group to
// do something else on the satellites the group is given to.
async function duplicateDialog(id) {
  const store = await loadStore();
  const spec = store.commands[id] || {};
  const groups = store.groups || {};
  const own = asList(spec.groups);
  const use = store.group_use || {};
  const given = (use.home || []).length > 0 || Object.keys(use.satellites || {}).length > 0;
  let copyId = `${id}_copy`;
  for (let n = 2; store.commands[copyId]; n++) copyId = `${id}_copy_${n}`;
  const idInput = h("input", { type: "text", value: copyId, oninput: (e) => { idInput.dataset.touched = "1"; } });
  const title = h("input", { type: "text", value: `${spec.title || spec.description || id} (copy)`, style: "width:100%" });
  const NEW = "+new"; // never a group id
  const group = h("select", {},
    h("option", { value: "" }, own.length ? `Its groups (${own.map((g) => (groups[g] && groups[g].name) || g).join(", ")})` : "No group"),
    ...Object.entries(groups).map(([g, info]) => h("option", { value: g }, `Only ${(info && info.name) || g}`)),
    h("option", { value: NEW }, "Only a new group…"));
  const groupName = h("input", { type: "text", placeholder: "Kids' room" });
  const replace = h("input", { type: "checkbox", checked: true });
  const replaceRow = h("label", { class: "small" }, replace, "");
  const errors = h("div");
  function update() {
    groupName.parentElement.style.display = group.value === NEW ? "" : "none";
    const g = group.value;
    // The copy takes the original's place in a group they would share.
    const shared = g && g !== NEW && own.includes(g);
    replaceRow.style.display = shared ? "" : "none";
    replaceRow.lastChild.textContent = shared ? ` Take ${spec.title || id}'s place in ${(groups[g] && groups[g].name) || g}: leave the original out of it` : "";
  }
  group.addEventListener("change", update);
  async function create() {
    fill(errors);
    const body = { new_id: slug(idInput.value), title: title.value.trim() };
    if (group.value === NEW) {
      if (!groupName.value.trim()) { errors.append(errorBox("Name the new group")); return; }
      body.new_group = groupName.value.trim();
    } else if (group.value) {
      body.group = group.value;
      body.replace = replace.checked && own.includes(group.value);
    }
    try {
      const result = await api("POST", `commands/${id}/duplicate`, body);
      close();
      toast(`Made ${result.id}`);
      refreshStatus();
      location.hash = `#/command/${result.id}`;
    } catch (err) { errors.append(errorBox(err)); }
  }
  const close = openDialog(`Duplicate ${spec.title || spec.description || id}`, h("div", { class: "stack" },
    h("p", { class: "muted small" }, "A copy of the command as it is saved, to change. To have it do something else on some satellites, put the copy in a command group given to them (Satellites page) and leave the original out of it."),
    h("div", { class: "grid2" }, field("Id", idInput, "Lowercase letters, digits and underscores."), field("Title", title)),
    field("Command groups", group),
    field("New group's name", groupName),
    replaceRow,
    given ? null : h("p", { class: "notice warn small" }, "No groups are given to the home or a satellite yet, so every satellite runs every command — this copy and the original both — until they are."),
    errors,
    h("div", { class: "row" }, h("span", { class: "grow" }), h("button", { type: "button", onclick: () => close() }, "Cancel"), h("button", { class: "primary", onclick: create }, "Duplicate"))), "compact");
  update();
  idInput.focus();
}

// ------------------------------------------------------------- new command

// New command: the library's commands to start from -- searched, filtered by
// group -- and, always there, a custom one. A command with versions is one
// row that opens to them: on this satellite (nothing named), on a device by
// name, in an area or floor by name. Each version is added on its own.
const VARIANT_ICONS = { here: "mdi:crosshairs-gps", device: "mdi:devices", place: "mdi:home-map-marker" };

async function newCommandDialog() {
  let data, store;
  try {
    [data, store] = await Promise.all([api("GET", "library"), loadStore()]);
  } catch (err) { toast(err.message); return; }
  // Added: one of your commands has its id. A command whose id you changed is
  // yours, and the library's can be added again.
  const yours = new Set(Object.keys(store.commands || {}));
  const groups = [...new Map(data.entries.map((e) => [e.group, e.group_icon])).entries()];
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
      const result = await api("POST", "library/add", { file: entry.file, id: entry.id });
      toast(`Added ${result.id}`);
      close();
      refreshStatus();
      location.hash = `#/command/${result.id}`;
    } catch (err) { toast(err.message); }
  };
  const custom = h("button", { type: "button", class: "custom-command", onclick: () => { close(); location.hash = "#/command/"; } },
    icon("mdi:pencil-plus-outline", "big"),
    h("span", { class: "grow" }, h("span", { class: "title" }, "Custom command"),
      h("span", { class: "muted small" }, "Start from scratch: what it acts on, what to listen for, what happens.")));
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
        h("div", { class: "title" }, entry.title, added ? h("span", { class: "badge ok", style: "margin-left:8px" }, "added") : null),
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
  function render() {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const shown = families.filter((f) => !group || f.group === group)
      .map((f) => [f, f.versions.filter((e) => matches(e, words))]).filter(([, versions]) => versions.length);
    fill(chips, ...[["", null, "All"], ...groups.map(([g, i]) => [g, i, g])].map(([value, ic, label]) => {
      const count = families.filter((f) => (!value || f.group === value) && f.versions.some((e) => matches(e, words))).length;
      return h("button", { type: "button", class: `filter-chip ${group === value ? "on" : ""}`, "aria-pressed": String(group === value),
        onclick: () => { group = value; render(); } }, ic ? icon(ic) : null, `${label} (${count})`);
    }));
    fill(list, ...shown.map(([family, versions]) => (family.versions.length === 1 && !family.versions[0].variant
      ? row(versions[0], iconFor(versions[0].spec))
      : familyRow(family, versions, words.length > 0))),
    shown.length ? null : h("p", { class: "muted" }, "No library command matches. Start a custom one above."));
  }
  search.addEventListener("input", render);
  const close = openDialog("New command", [
    custom,
    h("p", { class: "muted small", style: "margin:10px 0 4px" }, "Or start from the library: adding one copies it into your commands, where you can change anything about it. Most come in versions — on this satellite, on a device by name, in an area or floor by name — each added on its own."),
    search, chips,
    h("div", { class: "dialog-list" }, list)]);
  render();
  search.focus();
}

async function pageLibrary() {
  location.hash = "#/commands";
  newCommandDialog();
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
      field(`Add a name for a Home Assistant ${kind}`, hassPicker(kind, (item) => edit(item.id, null, item))),
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
    h("h1", {}, "Names"),
    h("p", { class: "muted" }, "What things are called when spoken. Each name points at one Home Assistant id. Nothing is taken from Home Assistant automatically: its names and aliases are offered as suggestions."),
    ...sections,
    h("div", { class: "panel" }, h("h2", {}, "Shared value lists"),
      h("p", { class: "muted small" }, "Lists a command's value slots can use: colors, modes, presets."),
      Object.keys(store.lists).length ? h("div", { class: "list" }, ...Object.entries(store.lists).map(([name, values]) => h("div", { class: "item" },
        h("div", { class: "grow" }, h("b", {}, name), h("div", { class: "muted small" }, valuesToText(values).split("\n").join(" · "))),
        h("button", { class: "link", onclick: () => editList(name, values) }, "edit"),
        h("button", { class: "link", onclick: async () => { try { await api("DELETE", `lists/${encodeURIComponent(name)}`); pageNames(); } catch (err) { toast(err.message); } } }, "remove")))) : h("p", { class: "muted" }, "None yet."),
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
    options.push(h("option", { value: "auto", selected: inherit === undefined ? !value : value === "auto" }, entity && entity.english ? `Automatic (${entity.english})` : "Automatic"));
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

// Wake words to tick, at most `slots` of them. With `inherit` (a satellite),
// unticking "its own" follows every satellite's pick. `.picked()` is the ids
// ticked, or null for the default (every satellite's).
function wakeChooser(words, picked, slots, inherit) {
  const own = h("input", { type: "checkbox", checked: !inherit || !!(picked && picked.length) });
  const boxes = words.map((w) => {
    const box = h("input", { type: "checkbox", value: w.id, checked: (picked && picked.length ? picked : inherit || []).includes(w.id), disabled: !w.runs });
    return { word: w, box };
  });
  const note = h("span", { class: "help" });
  // Hundreds of custom words: a filter, and the picked ones always shown.
  const filter = words.length > 12 ? h("input", { type: "search", placeholder: `Filter ${words.length} wake words`, style: "max-width:260px" }) : null;
  function shown() {
    const text = filter ? filter.value.trim().toLowerCase() : "";
    let room = 60;
    for (const { word, box } of boxes) {
      const match = !text || word.name.toLowerCase().includes(text) || word.id.includes(text);
      const show = box.checked || !word.custom || (match && (text || room > 0));
      if (show && word.custom && !box.checked) room--;
      box.parentElement.style.display = show ? "" : "none";
    }
  }
  if (filter) filter.addEventListener("input", shown);
  function update() {
    const following = inherit && !own.checked;
    for (const { word, box } of boxes) {
      if (following) box.checked = inherit.includes(word.id);
      box.disabled = following || !word.runs;
    }
    const n = boxes.filter((b) => b.box.checked).length;
    note.textContent = n > slots ? `Only ${slots} fit on a satellite: the first ${slots} ticked are used.` : n ? "" : "Pick at least one.";
    if (boxes.length && boxes[0].box.parentElement) shown();
  }
  own.addEventListener("change", update);
  boxes.forEach((b) => b.box.addEventListener("change", update));
  update();
  const el = h("div", { class: "wake-words" },
    inherit ? h("label", { class: "small" }, own, "Its own wake words (otherwise the default)") : null,
    filter,
    h("div", { class: "wake-list" }, boxes.map(({ word, box }) => h("label", { title: word.error || "" }, box, word.name,
      word.verified ? h("span", { class: "badge ok", title: `Checked by the verifier; the detector runs at ${word.cutoff}` }, "verified")
        : h("span", { class: "badge", title: "No verifier: runs at its own manifest's cutoff" }, `custom · ${word.cutoff}`),
      word.runs ? null : h("span", { class: "badge bad" }, "can't run")))),
    note);
  el.picked = () => (inherit && !own.checked ? null : boxes.filter((b) => b.box.checked).map((b) => b.word.id));
  shown();
  return el;
}

// Command groups to tick. With `inherit` (a satellite), unticking "its own"
// follows the default (every satellite's). `.picked()` is the ids ticked, or null.
function groupChooser(groups, picked, inherit) {
  const ids = Object.keys(groups);
  const own = h("input", { type: "checkbox", checked: !inherit || !!(picked && picked.length) });
  const boxes = ids.map((g) => ({ g, box: h("input", { type: "checkbox", value: g, checked: (picked && picked.length ? picked : inherit || []).includes(g) }) }));
  function update() {
    const following = inherit && !own.checked;
    for (const { g, box } of boxes) {
      if (following) box.checked = (inherit || []).includes(g);
      box.disabled = following;
    }
  }
  own.addEventListener("change", update);
  update();
  const el = h("div", { class: "wake-words" },
    inherit ? h("label", { class: "small" }, own, "Its own command groups (otherwise the default)") : null,
    h("div", { class: "wake-list" }, boxes.map(({ g, box }) => h("label", {}, box, (groups[g] && groups[g].name) || g))));
  el.picked = () => (inherit && !own.checked ? null : boxes.filter((b) => b.box.checked).map((b) => b.g));
  return el;
}

async function pageSatellites() {
  const [sats, voices, wake] = await Promise.all([
    loadSatellites(),
    api("GET", "voices").catch(() => ({ stt: [], tts: [], media_players: [] })),
    api("GET", "wake_words").catch(() => ({ wake_words: [], default: [], slots: 2 })),
  ]);
  const areas = await areaLabels();
  const settings = (state.status && state.status.settings) || {};
  const globalStt = voiceSelect("stt", voices.stt, settings.stt);
  const globalTts = voiceSelect("tts", voices.tts, settings.tts);
  const globalSttLanguage = languagePicker(voices.stt, globalStt, settings.stt_language);
  const globalTtsLanguage = languagePicker(voices.tts, globalTts, settings.tts_language);
  const globalVoice = voicePicker(globalTts, settings.tts_voice, undefined, globalTtsLanguage);
  const models = (state.status && state.status.tdt_models) || [];
  const tdtModel = h("select", {}, models.map((m) => h("option", { value: m.id, selected: (settings.tdt_model || "en") === m.id, disabled: !m.available },
    `${m.name} — ${m.detail}${m.available ? "" : " (not installed)"}`)));
  const label = (value, kind) => value || (kind === "stt" ? "built-in" : "built-in");
  const store = await loadStore();
  const groups = store.groups || {};
  const hasGroups = Object.keys(groups).length > 0;
  const homeGroups = (store.group_use && store.group_use.home) || [];
  const globalGroups = groupChooser(groups, homeGroups);
  const globalWake = wakeChooser(wake.wake_words, wake.default, wake.slots);
  const upload = h("input", { type: "file", multiple: true, accept: ".json,.tflite" });
  const custom = wake.wake_words.filter((w) => w.custom);
  fill($main, 
    h("h1", {}, "Satellites"),
    h("p", { class: "muted" }, "Each satellite's area is what “the lights” means there, and what commands scoped to an area follow. With the Voice Call integration it is the area of the satellite's own device in Home Assistant."),
    state.integration && state.integration !== "ready" ? h("div", { class: "notice warn" }, state.integration) : null,
    hasGroups ? h("div", { class: "panel" },
      h("h2", {}, "Command groups"),
      h("p", { class: "muted small" }, "Given to the whole home, satellites without groups of their own run only the commands in these. None ticked: every command. ", h("a", { href: "#/commands" }, "Edit the groups"), "."),
      globalGroups,
      h("div", { class: "row" }, h("button", { class: "primary", onclick: async () => {
        try { await api("PUT", "settings", { command_groups: globalGroups.picked() }); await refreshStatus(); toast("Saved"); pageSatellites(); }
        catch (err) { toast(err.message); }
      } }, "Save"))) : null,
    h("div", { class: "panel" },
      h("h2", {}, "Wake words"),
      h("p", { class: "muted small" }, "What every satellite listens for, unless it has its own below. ",
        h("b", {}, "Verified"), " wake words are checked a second time here, against words that sound like them, so the satellite can listen for them more eagerly. ",
        h("b", {}, "Custom"), " ones — a microWakeWord manifest (.json) and model (.tflite), as ESPHome and Tater share them — are not: they run at the cutoff they were trained for.",
        wake.custom_dir ? [" They can also be put in ", h("code", {}, wake.custom_dir), "."] : null),
      globalWake,
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: async () => {
          try { await api("PUT", "settings", { wake_words: globalWake.picked() }); await refreshStatus(); toast("Saved"); pageSatellites(); }
          catch (err) { toast(err.message); }
        } }, "Save"),
        h("span", { class: "grow" }),
        field("Add a custom wake word", upload, "Pick its .json and .tflite together."),
        h("button", { onclick: async () => {
          if (!upload.files.length) return toast("Pick the .json and the .tflite");
          const form = new FormData();
          for (const file of upload.files) form.append(file.name.endsWith(".json") ? "manifest" : "model", file, file.name);
          try {
            const response = await fetch("api/wake_words", { method: "POST", body: form });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
            toast(`Added ${data.id}`); pageSatellites();
          } catch (err) { toast(err.message); }
        } }, "Upload")),
      custom.length ? h("details", { class: "small" }, h("summary", {}, `Delete custom wake words (${custom.length})`), custom.map((w) => h("span", { class: "chip" }, w.name,
        h("button", { class: "link", title: `Delete ${w.id}`, onclick: async () => {
          if (!confirm(`Delete the wake word ${w.name}?`)) return;
          try { await api("DELETE", `wake_words/${encodeURIComponent(w.id)}`); toast("Deleted"); pageSatellites(); }
          catch (err) { toast(err.message); }
        } }, "×")))) : null),
    h("div", { class: "panel" },
      h("h2", {}, "Listening and speaking"),
      h("p", { class: "muted small" }, "Home Assistant's speech-to-text and text-to-speech, in place of the built-in models, for every satellite (each can choose its own below). Commands are always matched on the device's audio first; the speech-to-text entity writes what is otherwise read by the TDT model: example sentences, free text (“play …”), and hints. A text-to-speech entity speaks on the satellite's own media player, or on another one picked per satellite."),
      voices.error ? h("div", { class: "notice warn" }, voices.error) : null,
      field("Built-in speech model", tdtModel, "What writes example sentences, free text and hints when no speech-to-text entity is picked. The multilingual model understands 25 European languages and finds the language itself, at about three times the time and 600 MB more memory. Commands are matched first by the English model either way."),
      state.status && state.status.tdt_status ? h("div", { class: "notice info" }, state.status.tdt_status) : null,
      h("div", { class: "row" },
        field("Speech-to-text", globalStt), field("Language", globalSttLanguage),
        field("Text-to-speech", globalTts), field("Language", globalTtsLanguage), field("Voice", globalVoice),
        h("button", { class: "primary", onclick: async () => {
          try {
            await api("PUT", "settings", {
              stt: voiceSelectValue(globalStt), tts: voiceSelectValue(globalTts), tts_voice: globalVoice.value === "default" ? null : globalVoice.value || null,
              stt_language: languageValue(globalSttLanguage), tts_language: languageValue(globalTtsLanguage), tdt_model: tdtModel.value,
            });
            await refreshStatus(); toast("Saved"); pageSatellites();
          } catch (err) { toast(err.message); }
        } }, "Save"))),
    sats.length ? null : h("div", { class: "panel muted" }, "No satellite has connected yet."),
    ...sats.map((s) => {
      const name = h("input", { type: "text", value: s.name });
      const area = h("select", {}, h("option", { value: "" }, "No area"), ...Object.entries(areas).map(([id, label]) => h("option", { value: id, selected: s.area === id }, label)));
      const own = s.voice || {};
      const used = s.voice_used || {};
      const stt = voiceSelect("stt", voices.stt, own.stt, label(settings.stt));
      const tts = voiceSelect("tts", voices.tts, own.tts, label(settings.tts));
      const sttLanguage = languagePicker(voices.stt, stt, own.stt_language, settings.stt_language || "automatic", settings.stt);
      const ttsLanguage = languagePicker(voices.tts, tts, own.tts_language, settings.tts_language || "automatic", settings.tts);
      const voice = voicePicker(tts, own.tts_voice, settings.tts_voice || "", ttsLanguage);
      const speakOn = h("select", {},
        h("option", { value: "", selected: !own.speak_on }, s.device_media_player ? `This satellite (${s.device_media_player})` : "This satellite (its media player isn't in Home Assistant)"),
        ...voices.media_players.filter((m) => m.entity_id !== s.device_media_player).map((m) => h("option", { value: m.entity_id, selected: own.speak_on === m.entity_id }, `${m.name} (${m.entity_id})`)));
      const wakeWords = wakeChooser(wake.wake_words, s.wake_words, s.model_slots ? s.model_slots - 2 : wake.slots, wake.default);
      const commandGroups = hasGroups ? groupChooser(groups, s.command_groups, homeGroups) : null;
      const found = s.media_player_picked ? null : s.media_player;
      const player = h("select", {},
        h("option", { value: "" }, found ? `Its Sendspin player (${found})` : "Its Sendspin player (not found)"),
        ...(s.music_players || []).map((m) => h("option", { value: m.entity_id, selected: s.media_player_picked && s.media_player === m.entity_id }, `${m.name} (${m.entity_id})`)));
      return h("div", { class: "panel" },
        h("div", { class: "row" },
          h("h2", { class: "grow", style: "margin:0" }, s.name),
          h("span", { class: `badge ${s.connected ? "ok" : ""}` }, s.connected ? (s.in_call ? "in a call" : "connected") : "offline"),
          s.debug ? h("a", { class: "badge warn", href: `#/test?satellite=${s.key}` }, `debugging${s.debug.dry_run ? " · dry run" : ""}`) : null,
          h("a", { href: `#/test?satellite=${s.key}` }, "Test it")),
        h("div", { class: "kv small", style: "margin:8px 0" },
          h("div", {}, "Id"), h("div", {}, h("code", {}, s.key)),
          h("div", {}, "Satellite"), h("div", {}, s.esphome_name || "—", s.host ? ` at ${s.host}` : ""),
          h("div", {}, "In Home Assistant"), h("div", {}, s.device_id ? ["device ", h("code", {}, s.device_id), s.timer_list ? [" · timers ", h("code", {}, s.timer_list)] : null] : "not registered (needs the Voice Call integration)"),
          h("div", {}, "Floor"), h("div", {}, s.floor_name || "—"),
          h("div", {}, "Wake words"), h("div", {}, (s.wake_words_used || []).map((id) => (wake.wake_words.find((w) => w.id === id) || { name: id }).name).join(", ") || "none", s.wake_words ? " (its own)" : ""),
          h("div", {}, "Commands"), h("div", {}, s.command_groups_used ? ["only those in ", s.command_groups_used.map((g) => (groups[g] && groups[g].name) || g).join(", "), s.command_groups ? " (its own)" : ""] : "every command"),
          h("div", {}, "Music"), h("div", {}, s.media_player ? [h("code", {}, s.media_player), s.media_player_picked ? " (picked here)" : " (its Sendspin player in Music Assistant)"] : "no Music Assistant player found for it"),
          h("div", {}, "Listens with"), h("div", {}, used.stt ? [h("code", {}, used.stt), used.stt_language ? ` (${used.stt_language})` : ""] : "the built-in TDT model", " after the built-in matcher"),
          h("div", {}, "Speaks with"), h("div", {}, used.tts ? [h("code", {}, used.tts), used.tts_voice || used.tts_language ? ` (${[used.tts_language, used.tts_voice].filter(Boolean).join(", ")})` : "", " on ", h("code", {}, used.speak_on || s.device_media_player || "?"), used.speak_on ? "; the acknowledgement sound stays here" : ""] : "Piper, built in")),
        s.timers && s.timers.length ? h("div", { class: "small" }, h("b", {}, "Timers: "),
          s.timers.map((t) => `${t.name || "unnamed"} (${t.status}, ${Math.round(t.remaining)} s left)`).join(" · ")) : null,
        s.ringing ? h("div", { class: "notice warn" }, "A timer is ringing.") : null,
        h("div", { class: "row" },
          field("Name", name), field("Area", area), field("Music player", player)),
        field("Wake words", wakeWords),
        commandGroups ? field("Command groups", commandGroups) : null,
        h("div", { class: "row" },
          field("Speech-to-text", stt), field("Language", sttLanguage),
          field("Text-to-speech", tts), field("Language", ttsLanguage), field("Voice", voice),
          field("Speak responses on", speakOn, "With a text-to-speech entity: this satellite, or another media player.")),
        h("div", { class: "row" },
          h("button", { class: "primary", onclick: async () => {
            try {
              await api("PUT", `satellites/${s.key}`, {
                name: name.value, area: area.value || null, media_player: player.value || null,
                stt: stt.value || null, tts: tts.value || null, tts_voice: voice.value || null, speak_on: speakOn.value || null,
                stt_language: languageValue(sttLanguage, true), tts_language: languageValue(ttsLanguage, true),
                wake_words: wakeWords.picked(),
                ...(commandGroups ? { command_groups: commandGroups.picked() } : {}),
              });
              toast("Saved"); pageSatellites();
            }
            catch (err) { toast(err.message); }
          } }, "Save")),
      );
    }),
  );
}

// ---------------------------------------------------------------- sessions

const OUTCOME_CLASS = { answered: "ok", done: "ok", "dry run": "accent", "not recognized": "warn", "wake rejected": "", error: "bad", timeout: "bad", "in progress": "accent", "timer stopped": "ok", "timer rang out": "warn" };

// What a session was: a call on a satellite, a test, a replay.
function sessionKind(record, sats) {
  if (record.kind === "call") return (sats && sats[record.satellite.id]) || record.satellite.name || "Session";
  return record.kind === "try" ? "test" : record.kind;
}

async function pageSessions(query) {
  const satellite = new URLSearchParams(query || "").get("satellite");
  await loadSatellites();
  const settings = await api("GET", "settings");
  const list = h("div", { class: "list" });
  const sats = satelliteLabels();
  const session = satellite && state.satellites.find((s) => s.key === satellite);

  async function refresh() {
    const data = await api("GET", `calls${satellite ? `?satellite=${satellite}` : ""}`);
    fill(list, ...(data.calls.length ? data.calls.map((c) => h("div", { class: "item clickable", onclick: () => { location.hash = `#/session/${c.id}`; } },
      h("div", { style: "min-width:150px" }, h("div", {}, time(c.started)), h("div", { class: "muted small" }, sessionKind(c, sats))),
      h("div", { class: "grow" }, c.summary || h("span", { class: "muted" }, "…")),
      c.dry_run && c.outcome !== "dry run" ? h("span", { class: "badge" }, "dry run") : null,
      c.kept ? h("span", { class: "badge", title: "audio kept" }, "♪ audio") : null,
      h("span", { class: `badge ${OUTCOME_CLASS[c.outcome] || ""}` }, c.outcome))) : [h("p", { class: "muted" }, "No sessions yet.")]));
  }

  const keep = h("input", { type: "checkbox", checked: settings.keep_audio, onchange: async (e) => { await api("PUT", "settings", { keep_audio: e.target.checked }); refreshStatus(); toast(e.target.checked ? "Keeping audio of every session" : "Not keeping audio"); } });
  const max = h("input", { type: "number", min: 1, value: settings.max_saved_calls, style: "width:80px", onchange: async (e) => { await api("PUT", "settings", { max_saved_calls: Number(e.target.value) }); toast("Saved"); } });
  fill($main, 
    h("div", { class: "row" }, h("h1", { class: "grow" }, satellite ? `Sessions on ${sats[satellite] || satellite}` : "Sessions"), satellite ? h("a", { href: "#/sessions" }, "All satellites") : null),
    session && session.debug ? h("div", { class: "notice warn" }, `Debugging ${session.name}${session.debug.dry_run ? " (dry run: nothing is executed)" : ""}. New sessions appear here as they happen.`) : null,
    h("div", { class: "panel" },
      h("div", { class: "row" },
        h("label", {}, keep, "Debugging: keep the audio and full trace of every session"),
        h("label", { class: "right" }, "keep at most", max, "sessions")),
      h("p", { class: "muted small" }, "A session is everything from the wake word to the answer. Without this, the last sessions' traces are kept in memory only. Debugging one satellite (", h("a", { href: "#/test" }, "Test page"), ") keeps its audio.")),
    h("div", { class: "panel" }, list),
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

function renderOutcome(record) {
  const rec = stageOf(record, "recognition");
  const action = stageOf(record, "action");
  const speech = stageOf(record, "speech");
  if (!rec) return h("p", { class: "muted" }, record.summary || record.outcome);
  return h("div", { class: "stack" },
    h("div", { class: "row" },
      h("span", { class: `badge ${OUTCOME_CLASS[record.outcome] || ""}` }, record.outcome),
      rec.command ? h("span", {}, "Matched ", h("a", { href: `#/command/${rec.command}` }, rec.command), rec.decided_by && rec.decided_by !== "command" ? ` (${rec.decided_by})` : "") : h("span", { class: "muted" }, "No command matched")),
    h("div", { class: "kv" },
      h("div", {}, rec.text_source === "typed" ? "Read as" : "Heard"), h("div", {}, rec.recognition.transcript || "—"),
      rec.recognition.tdt_transcript ? [h("div", {}, heardLabels(rec).text), h("div", {}, rec.recognition.tdt_transcript)] : null,
      rec.slots && Object.keys(rec.slots).length ? [h("div", {}, "Slots"), h("div", {}, h("code", {}, JSON.stringify(rec.slots)))] : null,
      !rec.command && llmNote(rec.fallback) ? [h("div", {}, rec.fallback.llm.by || "Model"), h("div", {}, llmNote(rec.fallback))] : null,
      speech ? [h("div", {}, "Says"), h("div", {}, speech.text || h("span", { class: "muted" }, speech.silent ? "(nothing: it's silent)" : "(a short sound)"))] : null,
      action && action.error ? [h("div", {}, "Error"), h("div", { class: "notice bad" }, action.error)] : null));
}

// What the fallback's model step (GLiNER or an LLM) came to when it ran no
// command, in a line (as engine.llm_note); null when it was not asked.
function llmNote(fallback) {
  const llm = fallback && fallback.llm;
  if (!llm) return null;
  if (llm.skipped) return `skipped: ${llm.skipped}`;
  if (llm.refused && llm.refused.similarity !== undefined) return `picked ${llm.refused.command}, not close enough in meaning to run (${llm.refused.similarity.toFixed(2)}, needs ${llm.refused.floor.toFixed(2)})`;
  if (llm.refused) return `picked ${llm.refused.command}, not run: ${llm.refused.why}`;
  if (llm.problems) return `${llm.command ? `picked ${llm.command}` : "its pick"}, not run: ${llm.problems.join("; ")}`;
  return llm.said || "not a command";
}

// What the transcripts are called: heard by the CTC and TDT heads (or an STT
// entity), or typed on the Test page and read as spoken words.
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
      r.unspellable ? [h("div", {}, "Not in the model's pieces"), h("div", {}, r.unspellable.join(", "))] : null,
      r.lead_in ? [h("div", {}, "Lead-in ignored"), h("div", {}, r.lead_in)] : null,
      h("div", {}, "Timing"), h("div", { class: "small" }, Object.entries(rec.timings || {}).map(([k, v]) => `${k} ${Math.round(v * 1000)} ms`).join(" · "))),
    rec.skipped && rec.skipped.length ? h("div", { class: "notice warn" }, "Passed over: ", rec.skipped.map((s) => `${s.command} (${s.why})`).join(", ")) : null,
    rec.text_from_tdt && Object.keys(rec.text_from_tdt).length ? h("div", { class: "small muted" }, "Free text re-read from TDT; the CTC head heard ", JSON.stringify(rec.text_from_tdt)) : null,
    rec.answer ? h("div", { class: "notice info" }, rec.answer.text || "(no answer)",
      h("div", { class: "small muted" }, rec.answer.kind === "exact" ? `answered exactly (${rec.answer.skill || rec.answer.solver})` : rec.answer.kind === "live" ? "needs live information" : `answered (${rec.answer.kind})`)) : null,
    rec.matched_by === "transcript" ? h("div", { class: "notice info" }, `Found by keywords in the ${rec.text_source && rec.text_source !== "TDT" ? `transcript from ${rec.text_source}` : "TDT transcript"}: the audio's own evidence wasn't enough.`) : null,
    !rec.command && rec.transcript_pass ? h("div", { class: "small muted" }, `Keywords on the transcript (“${rec.transcript_pass.text}”): `,
      rec.transcript_pass.matches && rec.transcript_pass.matches.length ? `${rec.transcript_pass.matches[0].intent} (cost ${rec.transcript_pass.matches[0].cost}), not runnable here` : "nothing matched") : null,
    rec.matched_by && rec.matched_by !== "ctc" && rec.matched_by !== "transcript" ? h("div", { class: "notice info" }, rec.matched_by === "llm" ? "Found by the LLM: nothing matched on keywords or examples." : rec.matched_by === "gliner" ? "Found by GLiNER: nothing matched on keywords or examples." : `Found by meaning: nothing matched on keywords or examples, and this command was very close${rec.fallback && rec.fallback.meaning && rec.fallback.meaning.heard_by ? `; ${rec.fallback.meaning.heard_by} heard what was said in it` : ""}.`) : null,
    rec.fallback ? h("details", { open: !!(rec.fallback.llm && !rec.fallback.llm.skipped && !rec.command) }, h("summary", {}, "When nothing matched"),
      rec.fallback.meaning && rec.fallback.meaning.skipped ? h("div", { class: "small" }, `By meaning: skipped (${rec.fallback.meaning.skipped})`) : null,
      rec.fallback.meaning && !rec.fallback.meaning.skipped ? h("div", { class: "small" }, "By meaning: ", rec.fallback.meaning.command || "nothing", rec.fallback.meaning.similarity !== undefined ? ` (similarity ${rec.fallback.meaning.similarity}, ahead by ${rec.fallback.meaning.margin})` : "", rec.fallback.meaning.why ? ` — ${rec.fallback.meaning.why}` : "",
        rec.fallback.meaning.heard_by ? h("div", { class: "muted" }, `${rec.fallback.meaning.heard_by} heard `, h("code", {}, JSON.stringify(rec.fallback.meaning.arguments || {})),
          rec.fallback.meaning.combination ? ` — said with ${rec.fallback.meaning.combination.length ? rec.fallback.meaning.combination.join(" and ") : "nothing"}` : "",
          rec.fallback.meaning.problems ? ` — not run: ${rec.fallback.meaning.problems.join("; ")}` : "") : null,
        !rec.fallback.meaning.heard_by && rec.fallback.meaning.problems ? h("div", { class: "muted" }, `not run: ${rec.fallback.meaning.problems.join("; ")}`) : null) : null,
      rec.fallback.llm ? h("div", { class: "small" }, `${rec.fallback.llm.by || "LLM"}: `,
        rec.fallback.llm.skipped ? `skipped (${rec.fallback.llm.skipped})` : [
          h("code", {}, rec.fallback.llm.raw || "nothing"), ` in ${Math.round((rec.fallback.llm.seconds || 0) * 1000)} ms`,
          rec.fallback.llm.candidates ? h("div", { class: "muted" }, "chose from ", rec.fallback.llm.candidates.join(", ")) : null,
          rec.fallback.llm.combination ? h("div", { class: "muted" }, `said with ${rec.fallback.llm.combination.length ? rec.fallback.llm.combination.join(" and ") : "nothing"}`) : null,
          rec.fallback.llm.problems ? h("div", { class: "muted" }, "refused: ", rec.fallback.llm.problems.join("; ")) : null,
          rec.fallback.llm.refused ? h("div", { class: "muted" }, rec.fallback.llm.refused.similarity !== undefined
            ? `not run: ${rec.fallback.llm.refused.command} is not close enough in meaning to what was said (similarity ${rec.fallback.llm.refused.similarity}, needs ${rec.fallback.llm.refused.floor}). The sentence is compared with the command's description and examples: a fuller description helps.`
            : `not run: ${rec.fallback.llm.refused.command} is ${rec.fallback.llm.refused.why}`) : null,
          rec.fallback.llm.said ? h("div", { class: "muted" }, `it said: ${rec.fallback.llm.said}`) : null,
        ]) : null) : null,
    rec.hint ? h("div", { class: "notice info" }, rec.hint.text, h("div", { class: "small muted" }, `hint from ${rec.hint.source || "nothing"}`, rec.hint.similarity !== undefined ? ` · similarity ${rec.hint.similarity} vs ${rec.hint.next_similarity} (${rec.hint.next})` : "")) : null,
    h("h3", {}, `Matched (${(r.matches || []).length})`),
    matchRows.length ? matchRows : h("p", { class: "muted" }, "Nothing matched."),
    h("details", {}, h("summary", {}, `Why the other ${rejected.length} didn't match`),
      ...rejected.map((x) => h("div", { class: "stage" },
        h("div", { class: "head" }, h("span", { class: "name" }, x.intent), x.heard_groups ? h("span", { class: "muted small" }, `${x.heard_groups} required group(s) heard`) : null),
        ...(x.reasons || []).map((reason) => h("div", { class: "small muted" }, "– ", reason))))),
  );
}

function renderStage(stage) {
  const { stage: name, at, ...data } = stage;
  let cls = "";
  let body = null;
  let head = name;
  if (name === "wake_kaldi") {
    cls = data.accepted ? "ok" : "bad";
    head = `Kaldi wake verification${data.offset ? ` (+${data.offset}s retry)` : ""}`;
    body = h("div", { class: "small" }, data.accepted ? "accepted" : `rejected by ${data.rejected_by}`,
      ` · phrase ${data.phrase || "—"} · wake ${num(data.wake_start)}–${num(data.split)}s · lr ${num(data.lr)} (prefix ${num(data.lr_prefix)}, last ${num(data.lr_last)}) · confusable ${data.confusable_words || "—"} by ${num(data.confusable_margin)} · ${num(data.decode_ms, 0)} ms`);
  } else if (name === "wake_ratio") {
    cls = data.overturned ? "ok" : "bad";
    head = "Second opinion on the wake word";
    body = h("div", { class: "small" }, data.overturned ? "overturned" : "agreed with", ` the ${data.rejected_by} rejection · score ${num(data.score)}`);
  } else if (name === "split") {
    head = "Where the command starts";
    body = h("div", { class: "small" }, data.phrase ? `wake phrase “${data.phrase}” at ${data.wake_start}–${data.wake_end}s (CTC)` : "wake phrase not found again; Kaldi's end used",
      ` · Kaldi ended it at ${data.kaldi_split}s · command from ${data.cut}s`);
  } else if (name === "recognition") {
    cls = data.command ? "ok" : "warn";
    head = "Recognition";
    body = renderRecognition(data);
  } else if (name === "action") {
    cls = data.error ? "bad" : "ok";
    head = data.dry_run ? "Action (dry run: not executed)" : "Action";
    body = h("div", {},
      data.outcome ? h("div", { class: "small" }, "Outcome: ", h("code", {}, data.outcome)) : null,
      data.verbosity ? h("div", { class: "small" }, "Said: ", h("code", {}, data.verbosity.level), ` (${data.verbosity.why}`,
        data.verbosity.place ? `; ${data.verbosity.place.here ? "here" : "elsewhere"}${data.verbosity.place.said ? ", " + data.verbosity.place.said : ""}` : "", ")") : null,
      data.timer ? h("div", { class: "small" }, "Timer: ", data.timer.timer ? JSON.stringify(data.timer.timer) : "none matched", " → ", data.timer.speech) : null,
      data.music ? h("div", { class: "small" }, "Music: ", data.music.operation, " · own player ", data.music.own || "none",
        data.music.found ? [" · found ", h("code", {}, data.music.found.uri), ` (${data.music.found.media_type}, score ${data.music.found.score})`] : null) : null,
      data.music && data.music.players ? h("details", {}, h("summary", {}, "Players"), h("pre", {}, JSON.stringify(data.music.players, null, 2))) : null,
      data.sequence && data.sequence.length ? h("div", {}, h("div", { class: "small muted" }, "Sequence"), h("pre", {}, JSON.stringify(data.sequence, null, 2))) : h("div", { class: "small muted" }, data.timer ? "Nothing to call." : "Only a response."),
      h("details", {}, h("summary", {}, "Variables sent to Home Assistant"), h("pre", {}, JSON.stringify(data.variables, null, 2))),
      data.response !== null && data.response !== undefined ? h("div", {}, h("div", { class: "small muted" }, "Returned"), h("pre", {}, JSON.stringify(data.response, null, 2))) : null,
      data.error ? h("div", { class: "notice bad" }, data.error) : null);
  } else if (name === "timer_ringing") {
    cls = "warn";
    head = "Timer ringing";
    body = h("div", { class: "small" }, (data.timers || []).map((t) => t.name || `${Math.round(t.created_duration)} s`).join(", "));
  } else if (name === "timer_stopped") {
    head = "Timer stopped";
    body = h("div", { class: "small" }, data.by === "stop" ? "by “stop” or a call" : data.by === "button" ? "by the center button" : data.by === "timeout" ? "rang out" : data.by, data.heard ? ` · heard ${data.heard}` : "");
  } else if (name === "agent") {
    cls = !data.asked ? "" : data.understood && data.text ? "ok" : data.error ? "bad" : "warn";
    head = `Conversation agent${data.agent ? ` (${data.agent})` : ""}`;
    body = h("div", { class: "small" }, !data.asked ? "Not asked: a dry run, and an agent can act in the home." : data.error ? ["Could not be asked: ", data.error] : [
      data.understood ? ["Said “", data.text || "", "”"] : ["Understood nothing (", data.code || data.response_type, "): said the default."],
      h("div", { class: "muted" }, `${data.response_type || ""} in ${Math.round((data.seconds || 0) * 1000)} ms · ${data.continued ? "conversation kept from before" : "new conversation"}${data.conversation_id ? ` ${data.conversation_id}` : ""}${data.wants_answer ? " · it asked something back" : ""}`)]);
  } else if (name === "unknown") {
    cls = data.error ? "bad" : "ok";
    head = "Unknown command";
    body = h("div", { class: "small" }, data.error ? ["Could not render it (", data.error, "): said the default."] : data.said ? ["Says “", data.said, "”"] : "Rendered to nothing: said the default.");
  } else if (name === "speech") {
    head = "Said";
    body = h("div", {}, data.text || h("span", { class: "muted" }, data.silent ? "(nothing: it's silent)" : "(a short sound)"));
  } else if (name === "speaker") {
    head = "Speaker";
    body = h("div", { class: "small" }, data.name || "not recognized", data.score !== null && data.score !== undefined ? ` · score ${num(data.score)}` : "", data.best ? ` · closest ${data.best}` : "");
  } else {
    body = Object.keys(data).length ? h("div", { class: "small muted" }, Object.entries(data).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")) : null;
  }
  return h("div", { class: `stage ${cls}` },
    h("div", { class: "head" }, h("span", { class: "name" }, head), h("span", { class: "muted small" }, `+${at}s`)),
    body,
    h("details", {}, h("summary", {}, "raw"), h("pre", {}, JSON.stringify(stage, null, 2))));
}

async function pageSession(id) {
  const record = await api("GET", `calls/${id}`);
  const split = stageOf(record, "split");
  const offset = split ? split.cut : 0;
  const out = h("div");
  async function replay(runActions) {
    fill(out, h("p", { class: "muted" }, "Replaying…"));
    try {
      const result = await api("POST", `calls/${id}/replay`, { run_actions: runActions });
      location.hash = `#/session/${result.id}`;
    } catch (err) { fill(out, errorBox(err)); }
  }
  const rec = stageOf(record, "recognition");
  const heard = rec && rec.recognition ? (rec.recognition.tdt_transcript || rec.recognition.transcript || "") : "";
  // A satellite's audio (not a typed or synthesized test): whose voice it was.
  const voice = record.audio && record.audio.call && record.kind !== "try" ? await voicePanel(record) : null;
  fill($main, 
    h("div", { class: "row" },
      h("a", { href: "#/sessions" }, "← Sessions"),
      h("h1", { class: "grow", style: "margin:0" }, `${sessionKind(record)} · ${time(record.started)}`),
      h("span", { class: `badge ${OUTCOME_CLASS[record.outcome] || ""}` }, record.outcome),
      record.dry_run && record.outcome !== "dry run" ? h("span", { class: "badge" }, "dry run") : null),
    h("p", {}, record.summary),
    h("div", { class: "row" },
      record.audio && record.audio.call ? h("button", { onclick: () => replay(false) }, "Replay with today's commands (dry run)") : null,
      heard ? h("button", { onclick: () => { pageCommand(null, { description: "", examples: [heard.replace(/[.?!]+$/, "")] }); } }, "Make a command from this") : null,
      record.kept ? h("button", { class: "danger", onclick: async () => { if (confirm("Delete this session and its audio?")) { await api("DELETE", `calls/${id}`); location.hash = "#/sessions"; } } }, "Delete") : null),
    out,
    record.audio && Object.keys(record.audio).length ? h("div", { class: "panel" }, h("h2", {}, "Audio"),
      record.audio.call ? [h("h3", {}, "Call (AGC): pre-roll, wake word, command"), waveform(record, "call", recognitionSpans(record, offset))] : null,
      record.audio["call-aec"] ? [h("h3", {}, "Call (AEC)"), waveform(record, "call-aec", [])] : null,
      record.audio.command ? [h("h3", {}, record.kind === "try" ? "Synthesized command" : "Command (endpointing cut)"), waveform(record, "command", record.kind === "try" ? recognitionSpans(record, 0) : [])] : null,
      h("div", { class: "legend" }, ...Object.entries(SPAN_COLORS).map(([role, color]) => h("span", { style: `--c:${color}` }, role)))) :
      h("div", { class: "panel muted small" }, "No audio kept for this session. Turn on debugging (Sessions page), or debug its satellite (Test page), to keep it."),
    voice,
    h("div", { class: "panel" }, h("h2", {}, "Pipeline"), ...(record.stages || []).map(renderStage)),
  );
  if (record.outcome === "in progress") poll(async () => { const next = await api("GET", `calls/${id}`); if (next.outcome !== "in progress") { stopPolling(); pageSession(id); } }, 1000);
}

// Who spoke in a session, and adding its voice to a speaker's: one already
// known, or a new one.
async function voicePanel(record) {
  let data;
  try { data = await api("GET", "speakers"); } catch (_) { return null; }
  const found = stageOf(record, "speaker");
  const said = !found ? "Not identified when it was heard."
    : found.name ? `Recognized as ${found.name} (score ${num(found.score, 2)}).`
      : found.best ? `Not recognized: closest ${found.best} (score ${num(found.score, 2)}).` : "Not recognized.";
  if (!data.enabled) {
    return h("div", { class: "panel" }, h("h2", {}, "Who spoke"), h("p", { class: "small" }, said),
      h("p", { class: "muted small" }, "Speaker identification is turned off, so no voices are learned."));
  }
  const NEW = "+new"; // never a speaker's name
  const pick = found && (found.name || found.best);
  const who = h("select", {},
    ...data.speakers.map((sp) => h("option", { value: sp.name, selected: sp.name === pick }, sp.name)),
    h("option", { value: NEW, selected: !data.speakers.length }, "A new speaker…"));
  const name = h("input", { type: "text", placeholder: "Name" });
  const nameField = h("span", {}, name);
  const out = h("div");
  const update = () => { nameField.style.display = who.value === NEW ? "" : "none"; };
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
  return h("div", { class: "panel" }, h("h2", {}, "Who spoke"),
    h("p", { class: "small" }, said),
    h("div", { class: "row" }, h("span", {}, "This is"), who, nameField, h("button", { class: "primary", onclick: add }, "Add this voice")),
    h("p", { class: "muted small" }, "Adds the wake word and the command, as the satellite heard them, to that person's voice: they are recognized better from then on. Only add a session you know is theirs."),
    out);
}

// -------------------------------------------------------------------- test

async function pageTest(query) {
  await loadSatellites();
  const satOptions = (blank) => [blank ? h("option", { value: "" }, blank) : null,
    ...state.satellites.map((s) => h("option", { value: s.key }, `${s.name}${s.area_name ? ` (${s.area_name})` : ""}${s.connected ? "" : " — offline"}`))];
  const input = h("input", { type: "text", class: "grow", placeholder: "turn on the kitchen lights" });
  const sat = h("select", {}, satOptions("no satellite"));
  const run = h("input", { type: "checkbox" });
  const speak = h("input", { type: "checkbox" });
  const out = h("div");
  async function go() {
    fill(out, h("p", { class: "muted" }, speak.checked ? "Synthesizing and recognizing…" : "Recognizing…"));
    try {
      const record = await api("POST", "try", { text: input.value, satellite: sat.value || null, run_actions: run.checked, speak: speak.checked });
      fill(out, h("div", { class: "panel" }, renderOutcome(record), h("p", {}, h("a", { href: `#/session/${record.id}` }, "Full trace"))),
        h("div", { class: "panel" }, ...(record.stages || []).filter((s) => ["recognition", "agent", "unknown", "action"].includes(s.stage)).map(renderStage)));
    } catch (err) { fill(out, errorBox(err)); }
  }
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });

  // One satellite: debugging it, and listening to it live.
  const wanted = new URLSearchParams(query || "").get("satellite");
  const picked = state.satellites.find((s) => s.key === wanted) || state.satellites.find((s) => s.connected) || state.satellites[0];
  const satellite = h("select", { onchange: () => { location.hash = `#/test?satellite=${satellite.value}`; } }, satOptions(null));
  if (picked) satellite.value = picked.key;
  if (picked) sat.value = picked.key;
  fill($main,
    h("h1", {}, "Test"),
    h("div", { class: "panel" },
      h("h2", {}, "A sentence"),
      h("p", { class: "muted small" }, "What you type is recognized like a command said on that satellite, after the wake word, as if every word had been heard exactly as typed: no speech recognition. Commands, skills and questions all read your text as it is."),
      h("div", { class: "row" }, input, sat, h("button", { class: "primary", onclick: go }, "Test")),
      h("label", { class: "small" }, run, "Really run the command in Home Assistant (otherwise a dry run, which never asks the conversation agent)"),
      h("label", { class: "small" }, speak, "Say it first: spoken by the app's voice and recognized from the audio, to test speech recognition too. Synthesized speech is cleaner than a room, so treat a match then as necessary, not sufficient.")),
    out,
    picked ? h("div", { class: "panel" },
      h("div", { class: "row" }, h("h2", { class: "grow", style: "margin:0" }, "A satellite"), satellite),
      liveSection(picked),
      debugSection(picked)) : h("div", { class: "panel muted" }, "No satellite has connected yet: debugging one, and listening to it, wait for one."),
  );
  input.focus();
}

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
    h("h3", {}, "Debugging", s.debug ? h("span", { class: "badge warn", style: "margin-left:8px" }, `on${s.debug.dry_run ? " · dry run" : ""}`) : null),
    h("p", { class: "muted small" }, "Keeps the audio of every session on this satellite, from the pre-roll before the wake word to the end of the command, with every stage of the pipeline. In a dry run nothing is executed: you hear what would have been said."),
    h("div", { class: "row" },
      h("label", {}, dry, "Dry run (don't run commands)"),
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
    h("h3", {}, "Listen live"),
    h("p", { class: "muted small" }, "Hear what its microphone hears, now, and see what its voice activity and wake word models make of it. It streams only while you listen, its ring blinks red the whole time, and it doesn't answer a wake word meanwhile: a detection is only marked here. Near the satellite, use headphones or untick “Play it here”: it would hear itself."),
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
    fallbackPanel(),
    agentPanel(),
    responsesPanel(),
    h("div", { class: "panel" },
      h("h2", {}, "Advanced"),
      h("div", { class: "item clickable", onclick: () => { location.hash = "#/yaml"; } },
        icon("mdi:code-braces", "big"),
        h("div", { class: "grow" },
          h("div", { class: "title" }, "Edit commands.yaml"),
          h("div", { class: "muted small" }, "Everything the Commands and Names pages edit, in one file: commands, groups, names and value lists. Commands written here may use anything adapt-ctc supports.")))),
  );
}

// -------------------------------------------------------------------- yaml

async function pageYaml() {
  const data = await api("GET", "yaml");
  const area = h("textarea", { class: "code big" });
  area.value = data.text;
  const errors = h("div");
  fill($main, 
    h("div", { class: "row" }, h("a", { href: "#/settings" }, "← Settings"), h("h1", { class: "grow", style: "margin:0" }, "commands.yaml"),
      h("button", { class: "primary", onclick: async () => {
        fill(errors, );
        try { await api("PUT", "yaml", { text: area.value }); toast("Saved"); refreshStatus(); }
        catch (err) { errors.append(errorBox(err)); }
      } }, "Save")),
    h("p", { class: "muted small" }, "Everything the UI edits, in one file. Hand edits made on disk are picked up too. Commands written here may use anything adapt-ctc supports, including entities outside the UI's domains."),
    errors,
    h("div", { class: "panel" }, area),
  );
}

// ------------------------------------------------------------------ router

async function route() {
  stopPolling();
  const hash = location.hash || "#/commands";
  const [path, query] = hash.slice(2).split("?");
  const [page, ...rest] = path.split("/");
  const arg = rest.join("/");
  for (const link of document.querySelectorAll("#nav a")) {
    const parent = { command: "commands", group: "commands", unknown: "commands", fallback: "settings", yaml: "settings", session: "sessions" }[page];
    link.classList.toggle("active", link.dataset.page === page || link.dataset.page === parent);
  }
  try {
    if (page === "commands") await pageCommands();
    else if (page === "command") await pageCommand(arg || null);
    else if (page === "group") await pageGroup(arg || null);
    else if (page === "unknown") await pageUnknown();
    else if (page === "fallback") await pageFallback();
    else if (page === "skills") await pageSkills();
    else if (page === "library") await pageLibrary();
    else if (page === "names") await pageNames();
    else if (page === "satellites") await pageSatellites();
    else if (page === "sessions") await pageSessions(query);
    else if (page === "session") await pageSession(arg);
    else if (page === "test") await pageTest(query);
    // Old links: the Calls and Try pages.
    else if (page === "calls") location.hash = `#/sessions${query ? `?${query}` : ""}`;
    else if (page === "call") location.hash = `#/session/${arg}`;
    else if (page === "try") location.hash = "#/test";
    else if (page === "settings") await pageSettings();
    else if (page === "yaml") await pageYaml();
    else await pageCommands();
  } catch (err) {
    fill($main, errorBox(err));
  }
}

window.addEventListener("hashchange", route);
refreshStatus();
setInterval(refreshStatus, 5000);
route();
