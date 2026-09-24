// static/js/app.js — orchestrates the four views.
import { parseRapidInput } from "./rapid.js";
import { monthGrid, shiftMonth } from "./calendar.js";
import { extractWikilinks, renderWikilinks } from "./wikilink.js";
import {
  signifierGlyph, moodEmoji, statusLabel, escapeHtml,
  highlight, heatLevel, friendlyDate, localIsoDate,
} from "./entry.js";

const api = {
  async listNotes()      { return (await fetch("/api/notes")).json(); },
  async listCollections(){ return (await fetch("/api/collections")).json(); },
  async createNote(n)    {
    return (await fetch("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(n),
    })).json();
  },
  async updateNote(id, p) {
    return (await fetch(`/api/notes/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    })).json();
  },
  async deleteNote(id)   { return await fetch(`/api/notes/${encodeURIComponent(id)}`, { method: "DELETE" }); },
  async search(q)        { return (await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json(); },
  async calendar(y, m)   { return (await fetch(`/api/calendar/${y}/${m}`)).json(); },
  async backlinks(id)    { return (await fetch(`/api/notes/${encodeURIComponent(id)}/backlinks`)).json(); },
};

const state = {
  notes: [],
  collections: [],
  activeView: "rapid",
  activeId: null,
  activeMood: null,
  rapidFilterCollection: null, // null = show all
  editorMode: "split",
  calYear: new Date().getFullYear(),
  calMonth: new Date().getMonth() + 1,
  flashId: null,   // note id to play the completion burst on, once
  query: "",       // active search query, for highlighting
  activeTags: [],  // committed tags for the open note (chip input)
  ready: false,    // true once the initial render has happened (gates hash sync)
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

// Local calendar date, NOT toISOString() — that would report tomorrow for
// western timezones late in the day, which silently misdates new notes and
// puts the calendar's "today" ring on the wrong cell.
const todayIso = () => localIsoDate(new Date());

/* ---------------------------------------------------------------- views -- */

function showView(name) {
  state.activeView = name;
  for (const v of ["rapid", "collections", "timeline", "calendar"]) {
    $("#" + v + "-pane").classList.toggle("hidden", v !== name);
  }
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  moveInk();
  if (name === "calendar") renderCalendar();
  render();
}

async function refresh() {
  [state.notes, state.collections] = await Promise.all([
    api.listNotes(),
    api.listCollections(),
  ]);
  render();
}

function render() {
  renderCollections();
  renderRapid();
  renderTimeline();
  renderEditor();
  renderPreview();
  moveInk();
  syncHash();
}

/* -------------------------------------------------------------- entries -- */

// Build one `.entry` row. `opts.animate` staggers the entrance.
function buildEntry(note, opts = {}) {
  const li = document.createElement("li");
  li.className = `entry sig-${note.signifier} status-${note.status}`;
  li.dataset.id = note.id;
  if (opts.animate) li.style.setProperty("--i", String(opts.index ?? 0));
  if (note.id === state.activeId) li.classList.add("is-active");

  const glyph = document.createElement("i");
  glyph.className = "glyph";
  glyph.setAttribute("aria-hidden", "true");
  glyph.textContent = opts.glyph ?? signifierGlyph(note.signifier);
  li.appendChild(glyph);

  const main = document.createElement("div");
  main.className = "entry-main";

  const title = document.createElement("span");
  title.className = "entry-title";
  if (opts.highlightQuery) title.innerHTML = highlight(note.title, opts.highlightQuery);
  else title.textContent = note.title;
  main.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "entry-meta";

  if (note.recurrence) {
    const rec = document.createElement("span");
    rec.className = "tag-chip";
    rec.textContent = "↻ " + note.recurrence;
    meta.appendChild(rec);
  }
  for (const t of note.tags || []) {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    chip.textContent = t;
    meta.appendChild(chip);
  }
  if (note.mood) {
    const pill = document.createElement("span");
    pill.className = `mood-pill mood-${note.mood}`;
    pill.title = note.mood;
    pill.textContent = moodEmoji(note.mood);
    meta.appendChild(pill);
  }
  if (opts.showDate !== false && note.dates?.length) {
    const dc = document.createElement("span");
    dc.className = "date-chip";
    dc.textContent = friendlyDate(note.dates[0], todayIso());
    meta.appendChild(dc);
  }
  if (meta.childElementCount) main.appendChild(meta);
  li.appendChild(main);

  if (opts.count != null) {
    const rc = document.createElement("span");
    rc.className = "row-count";
    rc.textContent = String(opts.count);
    li.appendChild(rc);
  }

  li.addEventListener("click", () => openEditor(note.id));
  return li;
}

/* ------------------------------------------------------------ collections -- */

function renderCollections() {
  const ul = $("#collection-list");
  ul.innerHTML = "";

  const counts = new Map();
  for (const n of state.notes) {
    counts.set(n.collection, (counts.get(n.collection) || 0) + 1);
  }

  const rows = [{ name: null, label: "all notes", count: state.notes.length }];
  for (const c of state.collections) {
    rows.push({ name: c, label: c, count: counts.get(c) || 0 });
  }

  rows.forEach((row, i) => {
    const li = document.createElement("li");
    li.className = "entry";
    li.style.setProperty("--i", String(i));
    const isActive = state.rapidFilterCollection === row.name;
    if (isActive) li.classList.add("is-active");

    const glyph = document.createElement("i");
    glyph.className = "glyph";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = row.name === null ? "◇" : "#";
    li.appendChild(glyph);

    const main = document.createElement("div");
    main.className = "entry-main";
    const title = document.createElement("span");
    title.className = "entry-title";
    title.textContent = row.label;
    main.appendChild(title);
    li.appendChild(main);

    const rc = document.createElement("span");
    rc.className = "row-count";
    rc.textContent = String(row.count);
    li.appendChild(rc);

    li.addEventListener("click", () => {
      state.rapidFilterCollection = row.name;
      showView("rapid");
    });
    ul.appendChild(li);
  });

  $("#collection-count").textContent = String(state.collections.length);
}

/* ------------------------------------------------------------- rapid log -- */

function renderRapid() {
  const ul = $("#rapid-list");
  ul.innerHTML = "";

  let notes = state.notes.slice();
  if (state.rapidFilterCollection) {
    notes = notes.filter((n) => n.collection === state.rapidFilterCollection);
  }
  notes.sort((a, b) => (b.dates?.[0] || "").localeCompare(a.dates?.[0] || ""));

  notes.forEach((n, i) => {
    const li = buildEntry(n, { animate: true, index: i });
    if (n.id === state.flashId) {
      li.classList.add("just-completed");
    }
    ul.appendChild(li);
  });
  state.flashId = null;

  $("#rapid-count").textContent = String(notes.length);
}

/* --------------------------------------------------------------- timeline -- */

function renderTimeline() {
  const date = $("#timeline-date").value || todayIso();
  const ul = $("#timeline-list");
  ul.innerHTML = "";

  const notes = state.notes.filter((n) => (n.dates || []).includes(date));
  notes.forEach((n, i) => {
    ul.appendChild(buildEntry(n, { animate: true, index: i, showDate: false }));
  });
  $("#timeline-count").textContent = String(notes.length);
}

/* ----------------------------------------------------------------- editor -- */

function renderEditor() {
  const ed = $("#note-editor");
  const empty = $("#empty-state");

  if (!state.activeId) {
    ed.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }
  const n = state.notes.find((x) => x.id === state.activeId);
  if (!n) {
    state.activeId = null;
    return renderEditor();
  }
  ed.classList.remove("hidden");
  empty.classList.add("hidden");

  $("#note-title").value = n.title;
  $("#note-body").value = n.body;
  $("#note-signifier").value = n.signifier;
  $("#note-status").value = n.status;
  $("#note-dates").value = (n.dates || []).join(", ");
  state.activeTags = (n.tags || []).slice();
  $("#note-tags-input").value = "";
  $("#note-recurrence").value = n.recurrence || "";

  // status badge — only shown when it says something the select doesn't
  const badge = $("#note-status-badge");
  badge.className = `status-badge status-${n.status}`;
  badge.textContent = statusLabel(n.status);
  badge.style.display = n.status === "open" ? "none" : "";

  // collection select — always include the note's own collection
  const options = state.collections.includes(n.collection)
    ? state.collections.slice()
    : [n.collection, ...state.collections];
  $("#note-collection").innerHTML = options
    .map((c) => `<option value="${escapeHtml(c)}" ${c === n.collection ? "selected" : ""}>${escapeHtml(c)}</option>`)
    .join("");

  // mood picker
  state.activeMood = n.mood || null;
  syncMoodButtons();
  renderTagsPreview();

  setEditorMode(state.editorMode);
  moveInk();
}

function renderPreview() {
  if (!state.activeId) return;
  const md = $("#note-body").value || "";
  const target = $("#note-preview");
  if (!md.trim()) {
    target.innerHTML = '<p class="preview-empty">Nothing to preview yet — start writing on the left.</p>';
    return;
  }
  const titles = new Set(state.notes.map((n) => n.title));
  // wikilinks must render first so marked doesn't mangle the HTML.
  const pre = renderWikilinks(md, titles);
  target.innerHTML = window.marked ? window.marked.parse(pre) : escapeHtml(pre);
}

function setEditorMode(mode) {
  state.editorMode = mode;
  const body = document.querySelector(".editor-body");
  if (body) body.dataset.mode = mode;
  $$(".editor-tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  moveInk();
  renderPreview();
}

async function openEditor(id) {
  // Switching notes invalidates any in-flight or displayed model output.
  aiAbort();
  aiReset();
  aiSetStatus("");
  state.activeId = id;
  renderEditor();
  renderRapid();
  renderTimeline();
  await renderBacklinks();
  moveInk();
}

async function renderBacklinks() {
  const panel = $("#backlinks-panel");
  const list = $("#backlinks-list");
  if (!state.activeId) {
    panel.classList.add("hidden");
    return;
  }
  let hits = [];
  try {
    hits = await api.backlinks(state.activeId);
  } catch {
    panel.classList.add("hidden");
    return;
  }
  list.innerHTML = "";
  if (!hits.length) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  hits.forEach((h, i) => {
    list.appendChild(buildEntry(h, { animate: true, index: i, showDate: true }));
  });
}

async function createFromWikilink(title) {
  const id = "wikilink-" + Date.now().toString(36);
  await api.createNote({
    id,
    collection: "inbox",
    title,
    body: "",
    signifier: "note",
    status: "open",
    dates: [todayIso()],
  });
  await refresh();
  const n = state.notes.find((x) => x.title === title);
  if (n) openEditor(n.id);
}

/* ---------------------------------------------------------------- actions -- */

async function submitRapid(e) {
  e.preventDefault();
  const input = $("#rapid-input");
  const parsed = parseRapidInput(input.value);
  if (!parsed) return;
  await api.createNote(parsed);
  input.value = "";
  await refresh();
}

async function saveEditor() {
  if (!state.activeId) return;
  const prev = state.notes.find((x) => x.id === state.activeId);
  const prevStatus = prev?.status;

  const dates = splitList($("#note-dates").value);
  commitTagInput(); // fold a half-typed tag in before saving
  const tags = state.activeTags.slice();
  const nextStatus = $("#note-status").value;

  await api.updateNote(state.activeId, {
    title: $("#note-title").value,
    body: $("#note-body").value,
    signifier: $("#note-signifier").value,
    status: nextStatus,
    collection: $("#note-collection").value,
    dates,
    tags,
    mood: state.activeMood || null,
    recurrence: $("#note-recurrence").value || null,
  });

  if (prevStatus !== "complete" && nextStatus === "complete") {
    state.flashId = state.activeId;
  }
  await refresh();
  flashSaved();
}

async function deleteEditor() {
  if (!state.activeId) return;
  if (!confirm("Delete this note?")) return;
  await api.deleteNote(state.activeId);
  state.activeId = null;
  await refresh();
}

function closeEditor() {
  if (!state.activeId) return;
  state.activeId = null;
  render();
}

async function createCollection(e) {
  e.preventDefault();
  const el = $("#new-collection-name");
  const name = el.value.trim();
  if (!name) return;
  await api.createNote({
    id: "col-" + Date.now().toString(36),
    collection: name,
    title: "Welcome to " + name,
    body: "",
    signifier: "note",
    status: "open",
    dates: [todayIso()],
  });
  el.value = "";
  await refresh();
}

async function createBlankNote() {
  const id = "note-" + Date.now().toString(36);
  const collection = state.rapidFilterCollection || state.collections[0] || "inbox";
  await api.createNote({
    id,
    collection,
    title: "",
    body: "",
    signifier: "task",
    status: "open",
    dates: [todayIso()],
    tags: [],
    mood: null,
  });
  await refresh();
  openEditor(id);
  setTimeout(() => $("#note-title").focus(), 30);
}

function splitList(v) {
  return String(v || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function pickMood(mood) {
  state.activeMood = mood || null;
  syncMoodButtons();
}

function syncMoodButtons() {
  $$(".mood").forEach((b) => {
    const on = b.dataset.mood === state.activeMood;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", String(on));
  });
  const label = $("#mood-value");
  if (label) label.textContent = state.activeMood ? state.activeMood : "no mood set";
}

function renderTagsPreview() {
  renderTagChips();
}

/* --- tags chip input ----------------------------------------------------- */

function renderTagChips() {
  const wrap = $("#tag-chips");
  if (!wrap) return;
  wrap.innerHTML = "";
  state.activeTags.forEach((t, i) => {
    const chip = document.createElement("span");
    chip.className = "tag-chip tag-chip--removable";
    chip.appendChild(document.createTextNode(t));
    const x = document.createElement("button");
    x.type = "button";
    x.className = "tag-chip__x";
    x.textContent = "\u00d7";
    x.title = `remove ${t}`;
    x.setAttribute("aria-label", `remove tag ${t}`);
    x.addEventListener("click", (e) => {
      e.preventDefault();
      removeTag(i);
    });
    chip.appendChild(x);
    wrap.appendChild(chip);
  });
}

// Turn whatever is typed in the field into committed chips.
// Returns true when something was committed.
function commitTagInput() {
  const input = $("#note-tags-input");
  if (!input) return false;
  const parts = input.value.split(",").map((s) => s.trim()).filter(Boolean);
  let changed = false;
  for (const p of parts) {
    if (!state.activeTags.includes(p)) {
      state.activeTags.push(p);
      changed = true;
    }
  }
  if (parts.length) {
    input.value = "";
    changed = true;
  }
  if (changed) renderTagChips();
  return changed;
}

function removeTag(index) {
  state.activeTags.splice(index, 1);
  renderTagChips();
}

function flashSaved() {
  const el = $("#save-flash");
  if (!el) return;
  el.textContent = "saved";
  el.classList.remove("show");
  void el.offsetWidth; // restart the animation
  el.classList.add("show");
}

/* --------------------------------------------------------------- calendar -- */

async function renderCalendar() {
  const cells = monthGrid(state.calYear, state.calMonth);
  $("#cal-label").textContent = `${MONTHS[state.calMonth - 1]} ${state.calYear}`;

  let counts = {};
  try {
    counts = await api.calendar(state.calYear, state.calMonth);
  } catch { /* leave empty */ }

  const today = todayIso();
  const grid = $("#calendar-grid");
  grid.innerHTML = "";

  for (const c of cells) {
    const div = document.createElement("div");
    const n = counts[c.iso] || 0;
    div.className = "cal-cell" + (c.inMonth ? "" : " out") + (c.iso === today ? " today" : "");
    const heat = heatLevel(n);
    if (heat) div.dataset.heat = String(heat);
    div.title = n ? `${c.iso} — ${n} note${n > 1 ? "s" : ""}` : c.iso;

    const day = document.createElement("span");
    day.className = "day";
    day.textContent = c.day;
    div.appendChild(day);

    if (n) {
      const cnt = document.createElement("span");
      cnt.className = "count";
      cnt.textContent = n;
      div.appendChild(cnt);
    }

    div.addEventListener("click", () => {
      $("#timeline-date").value = c.iso;
      showView("timeline");
    });
    grid.appendChild(div);
  }

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const days = Object.keys(counts).length;
  const summary = $("#cal-summary");
  if (summary) {
    summary.innerHTML = days
      ? `<b>${total}</b> dated note${total === 1 ? "" : "s"} across <b>${days}</b> day${days === 1 ? "" : "s"}`
      : "No dated notes this month.";
  }
}

/* --------------------------------------------------- sliding tab indicator -- */

function positionInk(nav, ink, activeBtn) {
  if (!nav || !ink || !activeBtn) return;
  const nr = nav.getBoundingClientRect();
  const br = activeBtn.getBoundingClientRect();
  ink.style.left = (br.left - nr.left) + "px";
  ink.style.width = br.width + "px";
  ink.classList.add("ready");
}

function moveInk() {
  positionInk(
    document.querySelector(".tabs"),
    document.querySelector(".tabs .tab-ink"),
    document.querySelector(".tab.active"),
  );
  positionInk(
    document.querySelector(".editor-tabs"),
    document.querySelector(".editor-tabs .tab-ink"),
    document.querySelector(".editor-tab.active"),
  );
}

/* ----------------------------------------------------------------- search -- */

let searchTimer = null;

function onSearchInput() {
  clearTimeout(searchTimer);
  const q = $("#search-box").value.trim();
  searchTimer = setTimeout(async () => {
    state.query = q;
    const box = $("#search-results");
    if (!q) {
      box.classList.add("hidden");
      return;
    }
    const hits = await api.search(q);
    box.innerHTML = "";
    if (!hits.length) {
      const div = document.createElement("div");
      div.className = "search-empty";
      div.textContent = "No matches";
      box.appendChild(div);
    } else {
      for (const h of hits.slice(0, 20)) {
        const row = document.createElement("div");
        row.className = "hit-item";
        row.setAttribute("role", "option");

        const title = document.createElement("div");
        title.className = "hit-title";
        title.innerHTML = highlight(h.title, q);

        const snip = document.createElement("div");
        snip.className = "hit-snippet";
        const body = (h.body || "").slice(0, 140);
        snip.textContent = body || `(${h.collection}${h.dates?.[0] ? ", " + h.dates[0] : ""})`;

        row.appendChild(title);
        row.appendChild(snip);
        row.addEventListener("click", () => {
          box.classList.add("hidden");
          $("#search-box").value = "";
          state.query = "";
          openEditor(h.id);
        });
        box.appendChild(row);
      }
    }
    box.classList.remove("hidden");
  }, 200);
}

/* -------------------------------------------------------------- local ai -- */
/* The model takes 20-70s per call. This panel exists to prove it is alive:
   reasoning streams in from ~2s while the answer is still being written, so
   the thinking pane is not decoration, it is the progress indicator. */

const aiState = {
  controller: null,
  timer: null,
  startedAt: 0,
  reasoning: "",
  content: "",
  kind: null,
};

function aiSetStatus(text, cls = "") {
  const el = $("#ai-status");
  el.className = "ai-status " + cls;
  el.textContent = text;
}

function aiElapsed() {
  return Math.floor((Date.now() - aiState.startedAt) / 1000) + "s";
}

function aiStartTimer() {
  aiState.startedAt = Date.now();
  clearInterval(aiState.timer);
  aiState.timer = setInterval(() => {
    aiSetStatus(`${aiState.content ? "writing" : "thinking"} · ${aiElapsed()}`, "busy");
  }, 1000);
}

function aiStopTimer() {
  clearInterval(aiState.timer);
  aiState.timer = null;
}

function aiSetBusy(busy) {
  ["#ai-summarize", "#ai-tags", "#ai-links"].forEach((sel) => { $(sel).disabled = busy; });
  $("#ai-stop").classList.toggle("hidden", !busy);
  $("#ai-ask-input").disabled = busy;
}

function aiReset() {
  aiState.reasoning = "";
  aiState.content = "";
  const thinking = $("#ai-thinking");
  thinking.classList.add("hidden");
  thinking.open = false;
  $("#ai-thinking-text").textContent = "";
  $("#ai-thinking-label").textContent = "thinking";
  const out = $("#ai-output");
  out.classList.add("hidden");
  out.innerHTML = "";
}

function aiAbort() {
  if (aiState.controller) aiState.controller.abort();
}

// Reasoning and, for tags/links, the raw list -- anything not the final answer.
function aiAppendThinking(text) {
  const box = $("#ai-thinking");
  box.classList.remove("hidden");
  const el = $("#ai-thinking-text");
  el.textContent = text;
  el.scrollTop = el.scrollHeight;
}

function aiRenderAnswer(out, final) {
  const titles = new Set(state.notes.map((n) => n.title));
  const pre = renderWikilinks(aiState.content, titles);
  const html = window.marked ? window.marked.parse(pre) : escapeHtml(pre);
  out.classList.remove("hidden");
  out.innerHTML = html + (final ? "" : '<span class="ai-caret"></span>');
}

function aiSuggestionRow(label, items, onPick) {
  const out = $("#ai-output");
  out.classList.remove("hidden");
  out.innerHTML = "";
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "ai-note";
    p.textContent = "Nothing new suggested.";
    out.appendChild(p);
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "ai-suggestions";
  const tag = document.createElement("span");
  tag.className = "ai-suggest-label";
  tag.textContent = label;
  wrap.appendChild(tag);
  for (const item of items) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ai-chip";
    b.textContent = item;
    b.addEventListener("click", () => {
      onPick(item);
      b.classList.add("added");
      b.textContent = item + " \u2713";
    });
    wrap.appendChild(b);
  }
  out.appendChild(wrap);
}

function aiHandleEvent(ev, kind) {
  const out = $("#ai-output");

  if (ev.kind === "reasoning") {
    aiState.reasoning += ev.text;
    aiAppendThinking(aiState.reasoning);
    return;
  }

  if (ev.kind === "content") {
    aiState.content += ev.text;
    if (kind === "tags" || kind === "links") {
      // The raw list is not worth rendering as prose; keep it as evidence.
      $("#ai-thinking-label").textContent = "model output";
      aiAppendThinking(aiState.reasoning + "\n\n---\n\n" + aiState.content);
    } else {
      aiRenderAnswer(out, false);
    }
    return;
  }

  if (ev.kind === "result") {
    if (kind === "tags") {
      aiSuggestionRow("add tag", ev.tags || [], (t) => {
        if (state.activeTags.includes(t)) return;
        state.activeTags.push(t);
        renderTagChips();
      });
    } else if (kind === "links") {
      aiSuggestionRow("insert link", ev.links || [], (title) => {
        const body = $("#note-body");
        const link = `[[${title}]]`;
        if (body.value.includes(link)) return;
        body.value = (body.value.trimEnd() + "\n\n" + link).trim();
        renderPreview();
      });
    } else if (kind === "ask" && ev.notes) {
      aiSuggestionRow("sources", ev.notes.map((n) => n.title), (title) => {
        const n = state.notes.find((x) => x.title === title);
        if (n) openEditor(n.id);
      });
    }
    return;
  }

  if (ev.kind === "error") {
    aiSetStatus("error", "error");
    out.classList.remove("hidden");
    out.innerHTML = `<p class="ai-note">${escapeHtml(ev.message)}</p>`;
    return;
  }

  if (ev.kind === "done") {
    aiRenderAnswer(out, true);
  }
}

async function aiRun(path, payload, kind) {
  aiAbort();
  aiReset();
  aiState.kind = kind;
  aiSetBusy(true);
  aiSetStatus("connecting\u2026", "busy");
  aiStartTimer();
  $("#ai-thinking").open = true;

  const controller = new AbortController();
  aiState.controller = controller;
  const out = $("#ai-output");

  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        try {
          aiHandleEvent(JSON.parse(line), kind);
        } catch {
          /* a malformed line is not worth killing the stream over */
        }
      }
    }

    if (!aiState.reasoning && !aiState.content) {
      aiSetStatus("no output", "error");
      out.classList.remove("hidden");
      out.innerHTML =
        '<p class="ai-note">The model returned nothing. Check llama.cpp is running, ' +
        'and raise NOOKBOARD_LLM_MAX_TOKENS if it is spending the whole budget reasoning.</p>';
    } else {
      aiSetStatus(`done \u00b7 ${aiElapsed()}`, "ok");
    }
  } catch (err) {
    if (err.name === "AbortError") {
      aiSetStatus("stopped", "error");
    } else {
      aiSetStatus("failed", "error");
      out.classList.remove("hidden");
      out.innerHTML = `<p class="ai-note">${escapeHtml(err.message)}</p>`;
    }
  } finally {
    aiStopTimer();
    aiSetBusy(false);
    aiState.controller = null;
  }
}

/* ------------------------------------------------------------------ hash -- */
/* Deep links:  #/view/calendar  |  #/note/<id>  |  #/view/timeline/note/<id> */

function parseHash() {
  const toks = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  const out = { view: null, note: null };
  for (let i = 0; i + 1 < toks.length; i += 2) {
    if (toks[i] === "view") out.view = decodeURIComponent(toks[i + 1]);
    if (toks[i] === "note") out.note = decodeURIComponent(toks[i + 1]);
  }
  return out;
}

function syncHash() {
  if (!state.ready) return; // don't stomp an incoming deep link
  const toks = [];
  if (state.activeView && state.activeView !== "rapid") toks.push("view", state.activeView);
  if (state.activeId) toks.push("note", state.activeId);
  const next = toks.length ? "#/" + toks.join("/") : "#/";
  if (location.hash !== next) history.replaceState(null, "", next);
}

const VALID_VIEWS = ["rapid", "collections", "timeline", "calendar"];

function readHash() {
  const { view, note } = parseHash();
  return {
    view: VALID_VIEWS.includes(view) ? view : "rapid",
    note: note || null,
  };
}

/* ------------------------------------------------------------------ boot -- */

window.addEventListener("DOMContentLoaded", async () => {
  $$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
  $("#rapid-form").addEventListener("submit", submitRapid);
  $("#new-collection-form").addEventListener("submit", createCollection);
  $("#note-save").addEventListener("click", saveEditor);
  $("#note-delete").addEventListener("click", deleteEditor);
  $("#note-close").addEventListener("click", closeEditor);
  $("#new-note-btn").addEventListener("click", createBlankNote);

  $("#timeline-date").value = todayIso();
  $("#timeline-date").addEventListener("change", renderTimeline);

  $$(".mood").forEach((b) => b.addEventListener("click", () => pickMood(b.dataset.mood)));
  $(".mood-clear").addEventListener("click", () => pickMood(""));
  $$(".editor-tab").forEach((t) => t.addEventListener("click", () => setEditorMode(t.dataset.mode)));

  $("#note-tags-input").addEventListener("keydown", (e) => {
    const field = e.target;
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitTagInput();
    } else if (e.key === "Backspace" && field.value === "" && state.activeTags.length) {
      e.preventDefault();
      removeTag(state.activeTags.length - 1);
    }
  });
  $("#note-tags-input").addEventListener("blur", commitTagInput);
  $("#tag-input").addEventListener("click", () => $("#note-tags-input").focus());

  // local ai
  $("#ai-summarize").addEventListener("click", () => {
    if (!state.activeId) return aiSetStatus("open a note first", "error");
    aiRun("/api/ai/summarize", { id: state.activeId }, "summarize");
  });
  $("#ai-tags").addEventListener("click", () => {
    if (!state.activeId) return aiSetStatus("open a note first", "error");
    aiRun("/api/ai/tags", { id: state.activeId }, "tags");
  });
  $("#ai-links").addEventListener("click", () => {
    if (!state.activeId) return aiSetStatus("open a note first", "error");
    aiRun("/api/ai/links", { id: state.activeId }, "links");
  });
  $("#ai-ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#ai-ask-input").value.trim();
    if (!q) return;
    aiRun("/api/ai/ask", { question: q }, "ask");
  });
  $("#ai-stop").addEventListener("click", aiAbort);

  $("#note-body").addEventListener("input", () => {
    clearTimeout(window._pvTimer);
    window._pvTimer = setTimeout(renderPreview, 150);
  });

  $("#cal-prev").addEventListener("click", () => {
    [state.calYear, state.calMonth] = shiftMonth(state.calYear, state.calMonth, -1);
    renderCalendar();
  });
  $("#cal-next").addEventListener("click", () => {
    [state.calYear, state.calMonth] = shiftMonth(state.calYear, state.calMonth, 1);
    renderCalendar();
  });

  const searchBox = $("#search-box");
  searchBox.addEventListener("input", onSearchInput);
  searchBox.addEventListener("blur", () =>
    setTimeout(() => $("#search-results").classList.add("hidden"), 150));
  searchBox.addEventListener("focus", () => {
    if (searchBox.value.trim()) $("#search-results").classList.remove("hidden");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== searchBox) {
      e.preventDefault();
      searchBox.focus();
      return;
    }
    if (e.key === "Escape") {
      if (document.activeElement === searchBox) {
        searchBox.blur();
        $("#search-results").classList.add("hidden");
      } else {
        closeEditor();
      }
      return;
    }
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if (e.key === "n" && !typing && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      createBlankNote();
    }
  });

  // wikilink clicks in preview
  $("#note-preview").addEventListener("click", async (e) => {
    const a = e.target.closest("a.wikilink");
    if (!a) return;
    e.preventDefault();
    const title = a.dataset.title;
    const n = state.notes.find((x) => x.title === title);
    if (n) {
      openEditor(n.id);
    } else if (confirm(`No note titled "${title}". Create it?`)) {
      await createFromWikilink(title);
    }
  });

  window.addEventListener("resize", moveInk);
  if (document.fonts?.ready) document.fonts.ready.then(moveInk);

  // Read the deep link BEFORE the first render, then let hash syncing take over.
  const boot = readHash();

  window.addEventListener("hashchange", async () => {
    const before = state.activeId;
    const next = readHash();
    state.activeView = next.view;
    state.activeId = next.note && state.notes.some((n) => n.id === next.note) ? next.note : null;
    showView(state.activeView);
    if (state.activeId !== before) await renderBacklinks();
  });

  await refresh();

  // Report which model this instance is wired to.
  try {
    const cfg = await (await fetch("/api/config")).json();
    const el = $("#ai-model");
    if (el) el.textContent = cfg.llm_model ? `(${cfg.llm_model})` : "";
  } catch { /* the panel works without it */ }

  state.ready = true;
  state.activeView = boot.view;
  state.activeId = boot.note && state.notes.some((n) => n.id === boot.note) ? boot.note : null;
  showView(state.activeView);
  if (state.activeId) await renderBacklinks();
  moveInk();
});
