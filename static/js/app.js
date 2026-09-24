// static/js/app.js — orchestrates the four views.
import { parseRapidInput } from "./rapid.js";
import { monthGrid, shiftMonth } from "./calendar.js";
import { extractWikilinks, renderWikilinks } from "./wikilink.js";
import { timeHint } from "./schedule.js";
import { weekKey } from "./week.js";
import {
  BOARD_TILES, clockTime, greeting, longDate, statTiles, todayAction, todayLine,
  trimmedMonth,
} from "./home.js";
import { splitQueries, spliceQueries } from "./query.js";
import {
  durationText, elapsedText, engineWords, extensionFor, headline, micAvailable,
  noteHref, percent, pickRecorderMime, recordingName, sourceLabel,
} from "./transcribe.js";
import {
  STAGES, blockedLabel, blocksLabel, completionWarning, depCandidates,
  dropBeforeId, isOpenTask, resolveTaskRef, shiftStage, stageIndex, summaryText,
} from "./board.js";
import {
  signifierGlyph, moodEmoji, statusLabel, escapeHtml,
  highlight, heatLevel, friendlyDate, localIsoDate,
} from "./entry.js";
import {
  buildGrid, dayLabel, distribution, findCheckin, painText, recentDays, statChips,
} from "./mood.js";

// Parse a JSON response, turning FastAPI's `detail` into a real Error so a
// refused move (a dependency cycle) can be shown instead of swallowed.
const JSON_HEADERS = { "Content-Type": "application/json" };

async function jsonOrThrow(resp) {
  // Read the body as text first. `resp.json()` on a body that is not JSON
  // throws "Unexpected token 'I', "Internal Server Error" is not valid JSON" —
  // which reaches the person as a save that failed for no stated reason. A
  // server that has just restarted answers exactly that, so this is a real
  // failure mode and not a hypothetical one.
  const text = await resp.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      throw new Error(
        `the server answered ${resp.status} with something that is not JSON ` +
        `(${text.trim().slice(0, 80)}) — if it just restarted, try again`
      );
    }
  }
  if (!resp.ok) {
    const detail = body && body.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg || String(d)).join("; ")
      : (detail || `HTTP ${resp.status}`);
    throw new Error(message);
  }
  return body;
}

const api = {
  // Every one of these goes through jsonOrThrow. Saving a note touches
  // `updateNote` and then `listNotes`, so a server that answers with anything
  // but JSON has to produce a sentence about the *server*, not about a
  // character it did not expect.
  async listNotes()      { return jsonOrThrow(await fetch("/api/notes")); },
  async listCollections(){ return jsonOrThrow(await fetch("/api/collections")); },
  async createNote(n)    {
    return jsonOrThrow(await fetch("/api/notes", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(n),
    }));
  },
  async updateNote(id, p) {
    return jsonOrThrow(await fetch(`/api/notes/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(p),
    }));
  },
  async deleteNote(id)   { return await fetch(`/api/notes/${encodeURIComponent(id)}`, { method: "DELETE" }); },
  async search(q)        { return jsonOrThrow(await fetch(`/api/search?q=${encodeURIComponent(q)}`)); },
  async calendar(y, m)   { return jsonOrThrow(await fetch(`/api/calendar/${y}/${m}`)); },
  async backlinks(id)    { return jsonOrThrow(await fetch(`/api/notes/${encodeURIComponent(id)}/backlinks`)); },

  // -- the dashboard
  async home(month) {
    const qs = month ? `?month=${encodeURIComponent(month)}` : "";
    return jsonOrThrow(await fetch(`/api/home${qs}`));
  },

  // -- transcription
  async transcribe() { return jsonOrThrow(await fetch("/api/transcribe")); },
  async transcribeStart(payload) {
    return jsonOrThrow(await fetch("/api/transcribe", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    }));
  },
  // The body is the file itself, so the browser posts the blob it already has.
  // Nothing has to take it apart and put it back together on the way.
  async transcribeUpload(blob, { name, model, collection, summarize }) {
    const qs = new URLSearchParams({
      name, model, collection, summarize: String(Boolean(summarize)),
    });
    return jsonOrThrow(await fetch(`/api/transcribe/upload?${qs}`, {
      method: "POST",
      headers: { "Content-Type": blob.type || "application/octet-stream" },
      body: blob,
    }));
  },
  async transcribeResummarise(id) {
    return jsonOrThrow(await fetch("/api/transcribe/summarize", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ id }),
    }));
  },

  // Writing a day's note is the same request the daily scheduler makes: the
  // note is written by the run that summarises it.
  async writeDay(day) {
    return jsonOrThrow(await fetch("/api/daily/summary", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ date: day, refresh: true }),
    }));
  },

  // -- queries in notes
  // jsonOrThrow so a query the server refused says so in the note, rather than
  // rendering as an empty block you would read as "nothing was done".
  async query(q, on) {
    const params = new URLSearchParams({ q, on });
    return jsonOrThrow(await fetch(`/api/query?${params}`));
  },

  // -- templates
  async templates()      { return (await fetch("/api/templates")).json(); },
  // jsonOrThrow, unlike the plain reads above: a refused template should say
  // which one and why, not fail silently under a click that did nothing.
  async applyTemplate(p) {
    return jsonOrThrow(await fetch("/api/templates/apply", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(p),
    }));
  },

  // -- board + dependencies
  async board(params = {}) {
    const q = new URLSearchParams(params).toString();
    return jsonOrThrow(await fetch(`/api/board${q ? "?" + q : ""}`));
  },
  async moveCard(id, stage, beforeId = null) {
    return jsonOrThrow(await fetch("/api/board/move", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ id, stage, before_id: beforeId }),
    }));
  },
  async deps(id)         { return jsonOrThrow(await fetch(`/api/notes/${encodeURIComponent(id)}/deps`)); },
  async addDep(id, blockerId) {
    return jsonOrThrow(await fetch(`/api/notes/${encodeURIComponent(id)}/deps`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ blocker_id: blockerId }),
    }));
  },
  async removeDep(id, blockerId) {
    return jsonOrThrow(await fetch(
      `/api/notes/${encodeURIComponent(id)}/deps/${encodeURIComponent(blockerId)}`,
      { method: "DELETE" },
    ));
  },
  async tasks(params = {}) {
    const q = new URLSearchParams(params).toString();
    return jsonOrThrow(await fetch(`/api/tasks${q ? "?" + q : ""}`));
  },

  // -- mood + pain
  async mood(days = 365)     { return jsonOrThrow(await fetch(`/api/mood?days=${days}`)); },
  async insight()            { return jsonOrThrow(await fetch("/api/insight")); },
  async notesOn(isoDate)     { return jsonOrThrow(await fetch(`/api/notes?date=${encodeURIComponent(isoDate)}`)); },
};

const state = {
  notes: [],
  collections: [],
  activeView: "rapid",
  activeId: null,
  activeMood: null,
  activePain: null,
  rapidFilterCollection: null, // null = show all
  templates: [],               // shapes a note can be made from
  editorMode: "split",
  calYear: new Date().getFullYear(),
  calMonth: new Date().getMonth() + 1,
  flashId: null,   // note id to play the completion burst on, once
  query: "",       // active search query, for highlighting
  activeTags: [],  // committed tags for the open note (chip input)
  ready: false,    // true once the initial render has happened (gates hash sync)
  // -- board
  board: null,             // last /api/board payload
  boardCollection: "",     // "" = all collections
  hideDone: false,
  blockedOnly: false,
  draggingId: null,
  tasks: [],               // open tasks, for the dependency picker
  // -- transcribe
  transcribeStatus: null,  // last /api/transcribe payload
  transcribeFile: null,    // a File picked in this tab, waiting to be uploaded
  transcribeTimer: null,   // the poll while a job is running
  deps: null,              // dependency payload for the open note
  boardError: null,
  // -- mood
  mood: null,              // last /api/mood payload
  moodDays: 365,           // range in days
  moodPain: null,          // pain staged in the today-log row (null = not set)
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

// Local calendar date, NOT toISOString() — that would report tomorrow for
// western timezones late in the day, which silently misdates new notes and
// puts the calendar's "today" ring on the wrong cell.
const todayIso = () => localIsoDate(new Date());

/* ---------------------------------------------------------------- views -- */

const SIDEBAR_VIEWS = ["rapid", "collections", "timeline", "calendar"];

//: Views that need the full width and therefore trade away the sidebar: the
//: board (five columns) and mood (a year of weeks). They keep the editor as a
//: second column so a note can be read or fixed without leaving the view.
const WIDE_VIEWS = ["home", "board", "mood", "transcribe"];

function showView(name) {
  state.activeView = name;
  for (const v of SIDEBAR_VIEWS) {
    $("#" + v + "-pane").classList.toggle("hidden", v !== name);
  }
  for (const v of WIDE_VIEWS) {
    $("#" + v + "-view").classList.toggle("hidden", v !== name);
  }
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  moveInk();
  if (name === "calendar") renderCalendar();
  render();
  if (name === "home") renderHome();
  if (name === "board") renderBoard();
  if (name === "mood") renderMood();
  if (name === "transcribe") renderTranscribe();
  // Leaving the view with the microphone open would keep the light on, and there
  // is no visible control left to stop it.
  if (name !== "transcribe") stopRecording();
}

async function refresh() {
  [state.notes, state.collections, state.templates] = await Promise.all([
    api.listNotes(),
    api.listCollections(),
    // A picker that cannot load hides itself rather than offering a stale list
    // you might act on, so a failure here is an empty list, not an exception.
    api.templates().then((out) => out.templates || []).catch(() => []),
  ]);
  // Query answers depend on the vault, and the vault is what just changed.
  queryResults.clear();
  render();
  // The board is derived server-side (positions, blocked-ness), so it is
  // re-fetched rather than recomputed from a possibly-stale client copy.
  if (state.activeView === "board") await renderBoard();
  if (state.activeView === "mood") await renderMood();
  // The dashboard is derived server-side too, and every number on it can move
  // when a note changes, so it is asked again rather than patched.
  if (state.activeView === "home") await renderHome();
  if (state.activeView === "transcribe") await renderTranscribe();
}

// A wide view trades the sidebar for itself and keeps the editor alongside, so
// a note can be opened without leaving the view. Both classes are derived from
// `activeView`, so no view can hide the editor by forgetting to say so.
function syncLayoutMode() {
  const layout = document.querySelector(".layout");
  const isWide = WIDE_VIEWS.includes(state.activeView);
  layout.classList.toggle("is-wide", isWide);
  layout.classList.toggle("is-wide-empty", isWide && !state.activeId);
}

function render() {
  syncLayoutMode();
  renderCollections();
  renderTemplatePicker();
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
  if (opts.showDate !== false && note.time_label) {
    const tc = document.createElement("span");
    tc.className = "date-chip date-chip--time";
    tc.textContent = note.time_label;
    meta.appendChild(tc);
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
  $("#note-stage").value = n.stage || "todo";
  $("#note-dates").value = (n.dates || []).join(", ");
  // `type="time"` speaks exactly our format: `HH:MM`, 24-hour, or empty.
  $("#note-at").value = n.at || "";
  $("#note-until").value = n.until || "";
  renderTimeHint();
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
  // Pain is a slider, so an unset value has no handle position to show — the
  // label carries that ("not logged") while the handle rests at 0.
  state.activePain = n.pain === undefined ? null : n.pain;
  renderPainRow();
  renderTagsPreview();

  setEditorMode(state.editorMode);
  moveInk();
}

// Query answers, keyed by the query and the day it was resolved against.
//
// Kept between renders so typing in a note does not re-ask the server for what
// it already knows. Cleared by refresh(), because the answer depends on the
// vault and the vault is what changed.
const queryResults = new Map();

function queryKey(text, on) {
  return `${on}\u0000${text}`;
}

// The day a note is about. A query resolves relative to this rather than to the
// clock, so a note written in week 38 says week 38 when you open it in March.
function noteDate(note) {
  if (note && note.dates && note.dates.length) return note.dates[0];
  if (note && note.created) return note.created;
  return todayIso();
}

function unansweredQueries(md, on) {
  const seen = new Set();
  for (const block of splitQueries(md)) {
    if (block.text && !seen.has(block.text)) seen.add(block.text);
  }
  return [...seen].filter((text) => !queryResults.has(queryKey(text, on)));
}

async function renderPreview() {
  if (!state.activeId) return;
  // Captured because the answer being fetched belongs to *this* note: if another
  // one is opened before it lands, rendering it there would attribute the work
  // to the wrong day.
  const resolvesFor = state.activeId;
  const md = $("#note-body").value || "";
  const target = $("#note-preview");
  if (!md.trim()) {
    target.innerHTML = '<p class="preview-empty">Nothing to preview yet — start writing on the left.</p>';
    return;
  }

  const note = state.notes.find((n) => n.id === state.activeId);
  const on = noteDate(note);
  const pending = unansweredQueries(md, on);

  // Known answers are spliced in before rendering; unknown ones are left as the
  // query, so the note never shows a gap where its content should be.
  const titles = new Set(state.notes.map((n) => n.title));
  const withAnswers = spliceQueries(md, (q) => queryResults.get(queryKey(q, on)));
  // wikilinks must render first so marked doesn't mangle the HTML.
  const pre = renderWikilinks(withAnswers, titles);
  target.innerHTML = window.marked ? window.marked.parse(pre) : escapeHtml(pre);

  if (!pending.length) return;

  await Promise.all(
    pending.map(async (text) => {
      const key = queryKey(text, on);
      try {
        const out = await api.query(text, on);
        queryResults.set(key, out.markdown ?? "");
      } catch (err) {
        // Cached either way: a failing query must not be retried on every
        // keystroke, and the note still has to say something about it.
        queryResults.set(key, `> **Query failed** — ${err.message}`);
      }
    })
  );

  if (state.activeId === resolvesFor) renderPreview();
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
  // On the board the editor is a sibling column that CSS hides until a card is
  // open, so opening a note has to re-sync the layout mode or nothing appears.
  syncLayoutMode();
  renderEditor();
  renderRapid();
  renderTimeline();
  await renderBacklinks();
  await renderDeps(id);
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

/* ------------------------------------------------------------ templates -- */

// Fill the picker from the templates the server reports.
//
// The list is rebuilt only when the set of templates has actually changed, so a
// refresh cannot yank the option you were reaching for out from under you.
function renderTemplatePicker() {
  const form = $("#template-form");
  const sel = $("#template-pick");
  const list = state.templates || [];
  form.classList.toggle("hidden", list.length === 0);
  if (!list.length) return;

  const signature = list.map((t) => t.id).join("|");
  if (sel.dataset.signature === signature) return;

  const wanted = sel.value;
  sel.innerHTML = "";
  for (const t of list) {
    const opt = document.createElement("option");
    opt.value = t.id;
    // Say which templates make tasks: the picker is the only place that shows
    // you what you are about to get before you get it.
    opt.textContent = t.signifier === "task" ? `${t.title} (task)` : t.title;
    sel.appendChild(opt);
  }
  sel.dataset.signature = signature;
  if (wanted && list.some((t) => t.id === wanted)) sel.value = wanted;
}

// Make a note from the chosen template, then open it.
//
// It lands in the collection you are looking at, or the inbox when you are
// looking at everything: a template for a journal entry should not put the
// entry somewhere other than the journal you have open.
async function submitTemplate(e) {
  e.preventDefault();
  const sel = $("#template-pick");
  const id = sel.value;
  if (!id) return;
  sel.classList.remove("is-error");
  try {
    const made = await api.applyTemplate({
      template: id,
      collection: state.rapidFilterCollection || "inbox",
    });
    await refresh();
    await openEditor(made.id);
  } catch (err) {
    // The control that caused the failure carries the reason, rather than the
    // click looking like it did nothing.
    sel.classList.add("is-error");
    sel.title = err.message;
  }
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
  const nextStage = $("#note-stage").value;

  try {
    await api.updateNote(state.activeId, {
      title: $("#note-title").value,
      body: $("#note-body").value,
      signifier: $("#note-signifier").value,
      status: nextStatus,
      stage: nextStage,
      collection: $("#note-collection").value,
      dates,
      // Empty string means "no time": the server takes null as a clear and
      // refuses anything it cannot read, so a typo cannot be swallowed.
      at: $("#note-at").value || null,
      until: $("#note-until").value || null,
      tags,
      mood: state.activeMood || null,
      // Explicit null when unset, so clearing a reading actually clears it
      // rather than leaving the old value in place.
      pain: state.activePain === undefined ? null : state.activePain,
      recurrence: $("#note-recurrence").value || null,
    });
  } catch (err) {
    // A refused save (e.g. a cycle via the raw dependency list) must be visible,
    // not silently dropped.
    const flash = $("#save-flash");
    flash.textContent = err.message;
    flash.classList.add("show", "save-flash--error");
    setTimeout(() => flash.classList.remove("show", "save-flash--error"), 4000);
    return;
  }

  // Moving a task to Done from the editor is the same promise as dragging it,
  // so it gets the same completion burst.
  if (prevStatus !== "complete" && (nextStatus === "complete" || nextStage === "done")) {
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
  clearDeps();
  render();
  if (state.activeView === "board") renderBoard();
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

function renderPainRow() {
  const slider = $("#note-pain");
  const out = $("#note-pain-out");
  if (!slider || !out) return;
  const unset = state.activePain === null || state.activePain === undefined;
  slider.value = unset ? 0 : state.activePain;
  out.textContent = unset ? "not logged" : `${state.activePain}/10`;
  out.classList.toggle("is-unset", unset);
}

function pickPain(raw) {
  // "" is the clear button; Number("") is 0, which is a real reading, so the
  // empty string has to be handled before any numeric coercion.
  state.activePain = raw === "" ? null : Number(raw);
  renderPainRow();
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

/* ------------------------------------------------------------------ board -- */
/* The board is painted from the server's /api/board payload. Column order is
   never recomputed here: the server owns it, so a reload can't shuffle cards. */

// Persisted so the filters you chose survive a reload.
const BOARD_PREFS_KEY = "nookboard.board.prefs";

function loadBoardPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(BOARD_PREFS_KEY) || "{}");
    state.hideDone = !!raw.hideDone;
    state.blockedOnly = !!raw.blockedOnly;
    state.boardCollection = raw.collection || "";
  } catch { /* a corrupt pref is not worth failing a boot over */ }
}

function saveBoardPrefs() {
  try {
    localStorage.setItem(BOARD_PREFS_KEY, JSON.stringify({
      hideDone: state.hideDone,
      blockedOnly: state.blockedOnly,
      collection: state.boardCollection,
    }));
  } catch { /* private mode / quota — the filters just won't persist */ }
}

async function renderBoard() {
  const params = {};
  if (state.boardCollection) params.collection = state.boardCollection;

  let data;
  try {
    data = await api.board(params);
    state.boardError = null;
  } catch (err) {
    state.boardError = err.message;
    return;
  }
  state.board = data;

  $("#board-summary").textContent = summaryText(data.summary);
  renderBoardCollectionFilter();
  syncBoardToggles();

  const wrap = $("#board-columns");
  wrap.innerHTML = "";

  const columns = data.columns.filter((c) => !(state.hideDone && c.id === "done"));
  wrap.style.setProperty("--cols", String(Math.max(columns.length, 1)));
  for (const col of columns) wrap.appendChild(buildColumn(col));

  if (state.boardError) showBoardError(state.boardError);
}

function syncBoardToggles() {
  const done = $("#board-hide-done");
  const blocked = $("#board-blocked-only");
  done.setAttribute("aria-pressed", String(state.hideDone));
  blocked.setAttribute("aria-pressed", String(state.blockedOnly));
  $("#board-collection").value = state.boardCollection;
}

function renderBoardCollectionFilter() {
  const sel = $("#board-collection");
  const current = state.boardCollection;
  const options = ['<option value="">all collections</option>']
    .concat(state.collections.map(
      (c) => `<option value="${escapeHtml(c)}"${c === current ? " selected" : ""}>${escapeHtml(c)}</option>`,
    ));
  sel.innerHTML = options.join("");
}

function chip(text, extraClass) {
  const span = document.createElement("span");
  span.className = extraClass ? `card__chip ${extraClass}` : "card__chip";
  span.textContent = text;
  return span;
}

// The time as the server spelled it, beside the date it belongs to. The client
// never reassembles a range: that is `time_label`'s job, and one layer deciding
// is how `9:05` in a file stays `09:05` here.
function timeChip(note) {
  if (!note || !note.time_label) return null;
  return chip(note.time_label, "card__chip--time");
}

// Say what the time will do, including the case where it will do nothing.
function renderTimeHint() {
  const hint = $("#note-time-hint");
  if (!hint) return;
  const days = splitList($("#note-dates").value).length;
  hint.textContent = timeHint($("#note-at").value, days);
  hint.classList.toggle("field-hint--warn", Boolean($("#note-at").value) && !days);
}

function buildColumn(col) {
  const section = document.createElement("section");
  section.className = "board-col";
  section.dataset.stage = col.id;

  const head = document.createElement("div");
  head.className = "board-col__head";
  const dot = document.createElement("span");
  dot.className = "board-col__dot";
  dot.setAttribute("aria-hidden", "true");
  const name = document.createElement("h3");
  name.textContent = col.label;
  const count = document.createElement("span");
  count.className = "board-col__count";
  count.textContent = String(col.count);
  count.title = `${col.count} card${col.count === 1 ? "" : "s"}`;
  head.append(dot, name, count);
  section.appendChild(head);

  const list = document.createElement("ul");
  list.className = "board-col__cards";
  list.dataset.stage = col.id;

  const visible = col.cards.filter((c) => !(state.blockedOnly && !c.blocked));
  if (!visible.length) {
    const empty = document.createElement("li");
    empty.className = "board-col__empty";
    empty.textContent = state.blockedOnly
      ? "nothing blocked"
      : (col.id === "done" ? "nothing finished yet" : "drop a card here");
    list.appendChild(empty);
  } else {
    for (const card of visible) list.appendChild(buildCard(card, col.id));
  }

  wireDropTarget(list);
  section.appendChild(list);
  return section;
}

function buildCard(card, stage) {
  const li = document.createElement("li");
  li.className = "card";
  li.dataset.id = card.id;
  li.dataset.stage = stage;
  if (card.blocked) li.classList.add("is-blocked");
  if (stage === "done") li.classList.add("is-done");
  if (card.id === state.activeId) li.classList.add("is-active");
  li.draggable = true;

  const title = document.createElement("p");
  title.className = "card__title" + (card.title ? "" : " card__title--empty");
  title.textContent = card.title || "untitled";
  title.title = "open this task";
  title.addEventListener("click", () => openEditor(card.id));
  li.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "card__meta";
  if (card.collection && card.collection !== "inbox") meta.appendChild(chip(card.collection));
  for (const t of (card.tags || []).slice(0, 3)) meta.appendChild(chip("#" + t));
  if (card.dates?.length) meta.appendChild(chip(friendlyDate(card.dates[0], todayIso())));
  const when = timeChip(card);
  if (when) meta.appendChild(when);
  if (meta.childElementCount) li.appendChild(meta);

  // Why it is stuck. The label is built with textContent — note titles are the
  // user's own text and must never be parsed as markup.
  const blockedText = blockedLabel(card);
  if (blockedText) {
    const box = document.createElement("div");
    box.className = "card__blocked";
    const glyph = document.createElement("span");
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "\u{1F512}";
    const label = document.createElement("span");
    label.textContent = blockedText;
    box.append(glyph, label);
    li.appendChild(box);
  }

  const blocks = blocksLabel(card.blocking);
  if (blocks) {
    const p = document.createElement("p");
    p.className = "card__blocks";
    p.textContent = "\u26D3 " + blocks;
    li.appendChild(p);
  }

  li.appendChild(buildCardControls(card, stage));

  li.addEventListener("dragstart", (e) => {
    state.draggingId = card.id;
    li.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    // Firefox refuses to start a drag unless some data is set.
    e.dataTransfer.setData("text/plain", card.id);
  });
  li.addEventListener("dragend", () => {
    state.draggingId = null;
    li.classList.remove("dragging");
  });

  return li;
}

// Every drag has a tap equivalent: dragging is fine-motor work, which is a poor
// fit for a hand with arthritis, and it is invisible to a keyboard.
function buildCardControls(card, stage) {
  const row = document.createElement("div");
  row.className = "card__move";
  row.addEventListener("click", (e) => e.stopPropagation());

  const left = shiftStage(stage, -1);
  const right = shiftStage(stage, 1);

  row.appendChild(moveButton("\u2039", left, left && `move to ${labelOf(left)}`,
    () => moveCard(card.id, left)));
  row.appendChild(moveButton("\u203A", right, right && `move to ${labelOf(right)}`,
    () => moveCard(card.id, right)));

  if (stage === "done") {
    row.appendChild(moveButton("\u21BA", "todo", "reopen this task",
      () => moveCard(card.id, "todo"), "card__bitem--done"));
  } else {
    row.appendChild(moveButton("\u2713", "done", "mark done",
      () => moveCard(card.id, "done"), "card__bitem--done"));
  }
  return row;
}

function moveButton(glyph, target, label, onClick, extraClass = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "card__bitem " + extraClass;
  b.textContent = glyph;
  if (!target) {
    b.disabled = true;
    b.title = "already at the end";
    b.setAttribute("aria-label", label || "no further column");
  } else {
    b.title = label;
    b.setAttribute("aria-label", label);
    b.addEventListener("click", onClick);
  }
  return b;
}

function labelOf(stageId) {
  const hit = STAGES[stageIndex(stageId)];
  return hit ? hit.label : stageId;
}

function wireDropTarget(list) {
  list.addEventListener("dragover", (e) => {
    if (!state.draggingId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    list.classList.add("is-over");
  });
  list.addEventListener("dragleave", () => list.classList.remove("is-over"));
  list.addEventListener("drop", async (e) => {
    e.preventDefault();
    list.classList.remove("is-over");
    const id = state.draggingId;
    if (!id) return;

    // Where the pointer landed, ignoring the card being dragged.
    const mids = Array.from(list.querySelectorAll(".card"))
      .filter((el) => el.dataset.id !== id)
      .map((el) => {
        const r = el.getBoundingClientRect();
        return { id: el.dataset.id, mid: r.top + r.height / 2 };
      });
    await moveCard(id, list.dataset.stage, dropBeforeId(mids, e.clientY));
  });
}

function findCard(id) {
  for (const col of state.board?.columns || []) {
    const hit = col.cards.find((c) => c.id === id);
    if (hit) return hit;
  }
  return null;
}

async function moveCard(id, stage, beforeId = null) {
  if (!stage) return;

  // Finishing something whose blocker is still open is usually a mistake, so
  // ask once — but never forbid it, since sometimes it really is done.
  if (stage === "done") {
    const warn = completionWarning(findCard(id));
    if (warn && !confirm(warn)) return;
  }

  try {
    await api.moveCard(id, stage, beforeId);
  } catch (err) {
    showBoardError(err.message);
    return;
  }
  await refresh();
}

function showBoardError(message) {
  const el = $("#board-summary");
  if (!el) return;
  el.textContent = message;
  el.classList.add("board-summary--error");
  setTimeout(() => el.classList.remove("board-summary--error"), 4000);
}

/* ------------------------------------------------------------ dependencies -- */

async function renderDeps(noteId) {
  if (!noteId) return;
  let payload;
  try {
    payload = await api.deps(noteId);
  } catch {
    return;
  }
  // The user may have opened another note while this was in flight.
  if (state.activeId !== noteId) return;
  state.deps = payload;

  const note = state.notes.find((n) => n.id === noteId);
  const blockedBy = (note?.blocked_by || []).slice();

  // Fetch every task, not just open ones: finished tasks are not *offered* as
  // blockers, but they must still be recognisable so the error can say "that
  // one is already finished" instead of pretending it does not exist.
  try {
    state.tasks = await api.tasks({ include_done: true });
  } catch { state.tasks = []; }
  if (state.activeId !== noteId) return;

  paintDeps(payload, blockedBy);
}

function paintDeps(payload, blockedBy) {
  const badge = $("#deps-state");
  const blockers = payload.blocked_by || [];
  if (payload.blocked) {
    badge.className = "deps-state deps-state--blocked";
    badge.textContent = "blocked";
  } else if (blockers.length) {
    badge.className = "deps-state deps-state--ready";
    badge.textContent = "unblocked";
  } else {
    badge.className = "deps-state";
    badge.textContent = "no blockers";
  }

  const list = $("#blocked-by-list");
  list.innerHTML = "";
  if (!blockers.length) {
    const li = document.createElement("li");
    li.className = "deps-empty";
    li.textContent = "Nothing gates this — it can be started.";
    list.appendChild(li);
  } else {
    for (const b of blockers) list.appendChild(depChip(b, true));
  }

  const blocking = payload.blocking || [];
  const wrap = $("#deps-blocking-wrap");
  wrap.classList.toggle("hidden", !blocking.length);
  const bl = $("#deps-blocking");
  bl.innerHTML = "";
  for (const n of blocking) {
    bl.appendChild(depChip({
      id: n.id, title: n.title, status: n.status, closed: false, missing: false,
    }, false));
  }

  // Offer only what makes sense: not itself, not something already waiting, and
  // nothing that is already finished (a finished task gates nothing).
  const dl = $("#dep-candidates");
  dl.innerHTML = "";
  for (const t of depCandidates(state.tasks.filter(isOpenTask), state.activeId, blockedBy)) {
    const opt = document.createElement("option");
    opt.value = t.title || t.id;
    dl.appendChild(opt);
  }
}

function depChip(dep, removable) {
  const li = document.createElement("li");
  li.className = "dep-chip";
  if (dep.missing) li.classList.add("dep-chip--missing");
  else if (dep.closed) li.classList.add("dep-chip--closed");
  if (!removable) li.classList.add("dep-chip--passive");

  const glyph = document.createElement("span");
  glyph.className = "dep-chip__glyph";
  glyph.setAttribute("aria-hidden", "true");
  glyph.textContent = dep.missing ? "\u26A0" : (dep.closed ? "\u2713" : "\u{1F512}");

  const label = document.createElement("span");
  label.className = "dep-chip__label";
  label.textContent = dep.missing ? `${dep.id} (deleted)` : (dep.title || dep.id);
  if (!dep.missing) {
    label.classList.add("dep-chip__label--link");
    label.title = "open this task";
    label.addEventListener("click", () => openEditor(dep.id));
  }

  li.append(glyph, label);

  if (removable) {
    const x = document.createElement("button");
    x.type = "button";
    x.className = "dep-chip__x";
    x.textContent = "\u00d7";
    x.title = `stop waiting on ${dep.title || dep.id}`;
    x.setAttribute("aria-label", `remove dependency on ${dep.title || dep.id}`);
    x.addEventListener("click", async (e) => {
      e.preventDefault();
      await removeDependency(dep.id);
    });
    li.appendChild(x);
  }
  return li;
}

async function addDependency(rawValue) {
  if (!state.activeId) return;
  const noteId = state.activeId;
  const badge = $("#deps-state");

  const existing = (state.deps?.blocked_by || []).map((d) => d.id);
  const pickable = depCandidates(
    state.tasks.filter(isOpenTask), noteId, existing,
  );
  const blockerId = resolveTaskRef(pickable, rawValue);

  if (!blockerId) {
    // Be specific: a task that exists but is finished is not "no match", and
    // saying so is the difference between a puzzle and a usable message.
    const anywhere = resolveTaskRef(
      state.tasks.filter((t) => t.id !== noteId && !existing.includes(t.id)),
      rawValue,
    );
    badge.className = "deps-state deps-state--blocked";
    badge.textContent = anywhere
      ? "that task is already finished"
      : "no task matches that";
    return;
  }

  try {
    const payload = await api.addDep(noteId, blockerId);
    if (state.activeId !== noteId) return;
    $("#dep-input").value = "";
    paintDeps(payload, (state.notes.find((n) => n.id === noteId)?.blocked_by || []));
  } catch (err) {
    badge.className = "deps-state deps-state--blocked";
    badge.textContent = err.message;
    return;
  }
  // The graph changed: refresh so the board's blocked flags catch up.
  await refresh();
}

async function removeDependency(blockerId) {
  if (!state.activeId) return;
  const noteId = state.activeId;
  try {
    const payload = await api.removeDep(noteId, blockerId);
    if (state.activeId !== noteId) return;
    paintDeps(payload, (state.notes.find((n) => n.id === noteId)?.blocked_by || []));
  } catch (err) {
    const badge = $("#deps-state");
    badge.className = "deps-state deps-state--blocked";
    badge.textContent = err.message;
    return;
  }
  await refresh();
}

async function clearDeps() {
  state.deps = null;
  const badge = $("#deps-state");
  if (badge) { badge.className = "deps-state"; badge.textContent = ""; }
  const list = $("#blocked-by-list");
  if (list) list.innerHTML = "";
  const blocking = $("#deps-blocking");
  if (blocking) blocking.innerHTML = "";
  $("#deps-blocking-wrap")?.classList.add("hidden");
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

/* ----------------------------------------------------------------- mood -- */

//: A day at or above this is ringed in the grid. 5/10 is where pain stops
//: being background noise, and the legend says so in words.
const PAIN_FLAG = 5;

function moodToday() {
  const iso = todayIso();
  return (state.mood?.days || []).find((d) => d.date === iso) || null;
}

// The note a reading is written to. Only a note already tagged #mood counts:
// attaching a mood to whatever note happens to be dated today would rewrite a
// journal entry the user wrote themselves. The id is derived from the date, so
// there can only ever be one check-in per day.
async function moodCheckinNote() {
  const today = todayIso();
  const candidates = await api.notesOn(today);
  const existing = findCheckin(candidates);
  if (existing) return existing;
  return await api.createNote({
    id: `mood-${today}`,
    collection: "inbox",
    title: "Mood check-in",
    body: "",
    signifier: "note",
    dates: [today],
    tags: ["mood"],
  });
}

// `mood === undefined` leaves the mood alone; `null` clears it. Same for pain.
async function logMood(mood, pain) {
  const note = await moodCheckinNote();
  const patch = {};
  if (mood !== undefined) patch.mood = mood;
  if (pain !== undefined) patch.pain = pain;
  if (!Object.keys(patch).length) return;
  try {
    await api.updateNote(note.id, patch);
  } catch (err) {
    $("#mood-stats").innerHTML =
      `<p class="mood-stats-empty">could not save: ${escapeHtml(err.message)}</p>`;
    return;
  }
  // refresh() re-reads the vault and repaints whichever view is active; the
  // series is server-derived, so it is re-fetched rather than patched locally.
  await refresh();
}

function paintPainOut(el, value) {
  const unset = value === null || value === undefined;
  el.textContent = unset ? "\u2013" : `${value}/10`;
  el.classList.toggle("is-unset", unset);
}

async function renderMood() {
  let payload;
  try {
    payload = await api.mood(state.moodDays);
  } catch (err) {
    $("#mood-stats").innerHTML =
      `<p class="mood-stats-empty">could not load mood: ${escapeHtml(err.message)}</p>`;
    return;
  }
  state.mood = payload;
  paintMoodLog(payload);
  paintMoodStats(payload.summary);
  paintMoodLegend(payload);
  paintMoodHeatmap(payload);
  paintMoodDistribution(payload);
  paintMoodRecent(payload.days);
  await paintInsight();
}

// Pain against finished work. Deliberately reads as its own sentence rather
// than a number: the payload refuses to claim a correlation below its minimum
// sample, and the honest rendering of that is the sentence, not a blank.
async function paintInsight() {
  const reading = $("#insight-reading");
  const host = $("#insight-bands");
  const caveat = $("#insight-caveat");
  let got;
  try {
    got = (await api.insight()).pain_vs_output;
  } catch (err) {
    reading.textContent = `could not work this out yet: ${err.message}`;
    host.innerHTML = "";
    caveat.textContent = "";
    return;
  }

  reading.textContent = got.reading.text;
  caveat.textContent = got.caveat;

  host.innerHTML = "";
  if (!got.bands.length) {
    const li = document.createElement("li");
    li.className = "insight-empty";
    li.textContent = "No day has both a pain reading and anything finished yet.";
    host.appendChild(li);
    return;
  }

  // Bars are relative to the best band, so the shape is readable without
  // implying a scale the numbers do not have.
  const widest = Math.max(...got.bands.map((b) => b.mean_completed), 1);
  for (const row of got.bands) {
    const li = document.createElement("li");
    li.className = "insight-band";

    const label = document.createElement("span");
    label.className = "insight-band-label";
    label.textContent = `${row.low}\u2013${row.high}`;

    const track = document.createElement("span");
    track.className = "insight-band-bar";
    const fill = document.createElement("i");
    fill.style.width = `${Math.round((row.mean_completed / widest) * 100)}%`;
    track.appendChild(fill);

    const value = document.createElement("span");
    value.className = "insight-band-value";
    value.textContent = row.mean_completed;

    const days = document.createElement("span");
    days.className = "insight-band-days";
    days.textContent = `${row.days}d`;

    li.append(label, track, value, days);
    li.title =
      `pain ${row.low}\u2013${row.high} (${row.band}): ` +
      `${row.mean_completed} finished on an average day, across ${row.days} days`;
    host.appendChild(li);
  }
}

function paintMoodLog(payload) {
  const today = moodToday();
  const picks = $("#mood-log-picks");
  picks.innerHTML = "";
  for (const level of payload.levels) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mood-pick";
    btn.dataset.mood = level;
    btn.textContent = moodEmoji(level);
    btn.title = level;
    btn.setAttribute("aria-label", `log today's mood as ${level}`);
    const on = today?.mood === level;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
    picks.appendChild(btn);
  }

  const value = today ? today.pain : null;
  const slider = $("#mood-log-pain");
  // An unset day leaves the handle at 0 but the label reads "–": the position
  // is a starting point to drag from, not a claim that pain was zero.
  slider.value = value === null || value === undefined ? 0 : value;
  state.moodPain = value === null || value === undefined ? null : value;
  paintPainOut($("#mood-log-pain-out"), value);
}

function paintMoodStats(summary) {
  const host = $("#mood-stats");
  host.innerHTML = "";
  const chips = statChips(summary);
  if (!chips.length) {
    const p = document.createElement("p");
    p.className = "mood-stats-empty";
    p.textContent = "Nothing logged yet — the grid fills in as you record days.";
    host.appendChild(p);
    return;
  }
  for (const chip of chips) {
    const box = document.createElement("div");
    box.className = "mood-stat";
    const value = document.createElement("span");
    value.className = "mood-stat-value";
    value.textContent = chip.value;
    const label = document.createElement("span");
    label.className = "mood-stat-label";
    label.textContent = chip.label;
    box.append(value, label);
    host.appendChild(box);
  }
}

// Swatches take their colour from the same tokens the cells use, so the key
// cannot drift from what the grid paints.
function paintMoodLegend(payload) {
  const ramp = $("#mood-legend-ramp");
  ramp.innerHTML = "";
  for (const level of payload.levels) {
    const swatch = document.createElement("i");
    swatch.style.background = `var(--mood-${level})`;
    swatch.title = level;
    ramp.appendChild(swatch);
  }
}

function paintMoodHeatmap(payload) {
  const host = $("#mood-heatmap");
  host.innerHTML = "";
  const weeks = buildGrid(payload.days, payload.from, payload.to);

  const daycol = document.createElement("div");
  daycol.className = "mood-daycol";
  for (const letter of ["M", "T", "W", "T", "F", "S", "S"]) {
    const s = document.createElement("span");
    s.textContent = letter;
    daycol.appendChild(s);
  }

  const months = document.createElement("div");
  months.className = "mood-months";
  const grid = document.createElement("div");
  grid.className = "mood-grid";

  for (const week of weeks) {
    const label = document.createElement("span");
    label.className = "mood-month";
    label.textContent = week.month || "";
    months.appendChild(label);

    for (const cell of week.cells) {
      const el = document.createElement("div");
      el.className = "mood-cell";
      if (!cell.inRange) {
        el.classList.add("mood-cell--out");
        el.setAttribute("aria-hidden", "true");
      } else {
        const day = cell.day;
        const flagged = day && day.pain !== null && day.pain !== undefined && day.pain >= PAIN_FLAG;
        el.classList.add(day && day.mood ? `mood-cell--${day.mood}` : "mood-cell--none");
        if (flagged) el.classList.add("mood-cell--pain");
        const text = day ? dayLabel(day) : "not logged";
        el.title = `${friendlyDate(cell.date, payload.today)} \u00b7 ${text}`;
        el.setAttribute("aria-label", `${cell.date}: ${text}`);
        el.setAttribute("role", "button");
        el.tabIndex = 0;
      }
      grid.appendChild(el);
    }
  }

  // The month axis and the grid must share column geometry, so they stack in
  // one flex child against the weekday labels.
  const right = document.createElement("div");
  right.append(months, grid);
  host.append(daycol, right);

  // At narrow widths a year of weeks does not fit and the scroller cuts off the
  // newest weeks. Say so, rather than letting them look truncated. Measured
  // after layout, so it reflects the real overflow instead of a guess.
  requestAnimationFrame(() => {
    const scroller = document.querySelector(".mood-scroll");
    const hint = $("#mood-scroll-hint");
    if (!scroller || !hint) return;
    hint.classList.toggle("hidden", scroller.scrollWidth <= scroller.clientWidth + 4);
  });

  // Clicking a day opens it, which is how you get from "that was a bad week"
  // to the notes that made it one. The timeline is driven by its own date
  // input, so set that rather than inventing a second source of truth.
  grid.addEventListener("click", (e) => {
    const cell = e.target.closest(".mood-cell");
    if (!cell || !cell.getAttribute("aria-label")) return;
    const iso = cell.getAttribute("aria-label").split(":")[0];
    $("#timeline-date").value = iso;
    showView("timeline");
    renderTimeline();
  });
  grid.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const cell = e.target.closest(".mood-cell");
    if (!cell) return;
    e.preventDefault();
    cell.click();
  });
}

function paintMoodDistribution(payload) {
  const host = $("#mood-distribution");
  host.innerHTML = "";
  const rows = distribution(
    payload.summary.counts, payload.levels, payload.summary.days_logged,
  );
  for (const row of rows) {
    const li = document.createElement("li");
    const level = document.createElement("span");
    level.className = "mood-dist-level";
    level.textContent = row.level;

    const bar = document.createElement("span");
    bar.className = "mood-dist-bar";
    const fill = document.createElement("span");
    fill.className = "mood-dist-fill";
    fill.style.width = `${row.pct}%`;
    fill.style.background = `var(--mood-${row.level})`;
    bar.appendChild(fill);

    const count = document.createElement("span");
    count.className = "mood-dist-count";
    count.textContent = String(row.count);

    li.append(level, bar, count);
    host.appendChild(li);
  }
}

function paintMoodRecent(days) {
  const host = $("#mood-recent");
  host.innerHTML = "";
  const recent = recentDays(days, 14);
  if (!recent.length) {
    const li = document.createElement("li");
    li.className = "mood-empty";
    li.textContent = "No days logged in this range yet.";
    host.appendChild(li);
    return;
  }
  for (const day of recent) {
    const li = document.createElement("li");
    const date = document.createElement("span");
    date.className = "mood-recent-date";
    date.textContent = friendlyDate(day.date, todayIso());

    const mood = document.createElement("span");
    mood.className = "mood-recent-mood";
    mood.textContent = moodEmoji(day.mood) || "\u2013";

    const pain = document.createElement("span");
    pain.className = "mood-recent-pain";
    pain.textContent = `pain ${painText(day.pain)}`;

    const notes = document.createElement("span");
    notes.className = "mood-recent-notes";
    notes.textContent = (day.notes || []).map((n) => n.title).join(", ");

    li.append(date, mood, pain, notes);
    host.appendChild(li);
  }
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
  ["#ai-summarize", "#ai-tags", "#ai-links", "#ai-day-recap", "#ai-week-recap"].forEach((sel) => {
    $(sel).disabled = busy;
  });
  $("#ai-stop").classList.toggle("hidden", !busy);
  $("#ai-ask-input").disabled = busy;
}

// Write the day's recap and jump to the day's note.
//
// Deliberately not a stream: the recap is written into a file and the response
// is a small result, so the model's progress is not what the user waits on --
// the note appearing is. The day comes from the open note, so "recap this day"
// means the day that note belongs to, not necessarily today.
async function runDayRecap() {
  const note = state.notes.find((n) => n.id === state.activeId);
  if (!note) return aiSetStatus("open a note first", "error");
  const day = (note.dates && note.dates[0]) || note.created;
  if (!day) return aiSetStatus("that note has no date to recap", "error");

  aiAbort();
  aiReset();
  aiSetBusy(true);
  aiSetStatus(`writing the recap for ${day}…`, "busy");
  try {
    const res = await fetch("/api/daily/summary", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ date: day, refresh: true }),
    });
    if (!res.ok) throw new Error(`recap failed (${res.status})`);
    const out = await res.json();
    if (!out.wrote) {
      aiSetStatus(`nothing was completed on ${day}`, "");
      return;
    }
    await refresh();
    await openEditor(out.note_id);
    aiSetStatus(
      out.recap_error
        ? `wrote ${out.completed} task(s) for ${day}; no recap — the model was unavailable`
        : `recapped ${out.completed} task(s) for ${day}`,
      out.recap_error ? "error" : ""
    );
  } catch (err) {
    aiSetStatus(err.message, "error");
  } finally {
    aiSetBusy(false);
  }
}

// Write the week's rollup and jump to the week's note.
//
// The week is derived from the open note's own date, so "recap this week" means
// the week that note belongs to -- which is what you want when you are reading
// Tuesday's entry and wondering how the week is going.
async function runWeekRecap() {
  const note = state.notes.find((n) => n.id === state.activeId);
  if (!note) return aiSetStatus("open a note first", "error");
  const day = (note.dates && note.dates[0]) || note.created;
  if (!day) return aiSetStatus("that note has no date to place in a week", "error");
  const key = weekKey(day);
  if (!key) return aiSetStatus(`could not place ${day} in a week`, "error");

  aiAbort();
  aiReset();
  aiSetBusy(true);
  aiSetStatus(`writing the rollup for ${key}\u2026`, "busy");
  try {
    const res = await fetch("/api/weekly/summary", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ week: key, refresh: true }),
    });
    if (!res.ok) throw new Error(`rollup failed (${res.status})`);
    const out = await res.json();
    if (!out.wrote) {
      aiSetStatus(`nothing was completed in ${key}`, "");
      return;
    }
    await refresh();
    await openEditor(out.note_id);
    aiSetStatus(
      out.recap_error
        ? `wrote ${out.completed} task(s) for ${key}; no rollup — the model was unavailable`
        : `rolled up ${out.completed} task(s) for ${key}`,
      out.recap_error ? "error" : ""
    );
  } catch (err) {
    aiSetStatus(err.message, "error");
  } finally {
    aiSetBusy(false);
  }
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

//: The views the app has, asked of the tabs themselves.
//:
//: This was a hand-written list, and adding a view without adding it here is a
//: trap: `transcribe` shipped as a tab whose deep link silently redirected home,
//: because the markup and the allowlist are two places and only one of them was
//: obviously "the list of views". The tabs are that list.
function validViews() {
  return $$(".tab[data-view]").map((tab) => tab.dataset.view);
}

function readHash() {
  const { view, note } = parseHash();
  return {
    view: validViews().includes(view) ? view : "home",
    note: note || null,
  };
}

/* -------------------------------------------------------- the dashboard -- */
/* Every number on the dashboard comes from /api/home, so nothing in here
   decides anything -- it paints, and it hands clicks off to the machinery the
   rest of the app already has. */

const MINI_CAL_DAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

//: What a quick-action button puts in the rapid log's box. The glyph is the
//: whole intent -- parseRapidInput() reads it, so the button and a typed "• "
//: cannot disagree about what makes a task.
const ENTRY_GLYPH = { task: "• ", event: "○ ", note: "– " };

function paintClock() {
  const host = $("#home-greeting");
  if (!host) return;
  const now = new Date();
  host.textContent = greeting(now.getHours());
  $("#home-time").textContent = clockTime(now);
  $("#home-date").textContent = longDate(now);
}

function paintTiles(host, tiles) {
  host.textContent = "";
  for (const tile of tiles) {
    const li = document.createElement("li");
    li.className = "h-tile";
    const value = document.createElement("span");
    value.className = "h-tile-value";
    value.textContent = String(tile.value);
    const label = document.createElement("span");
    label.className = "h-tile-label";
    label.textContent = tile.label;
    li.append(value, label);
    host.append(li);
  }
}

// Laid out by monthGrid() -- the same function the Calendar view builds its own
// grid with, so the two cannot disagree about which weekday the 1st falls on.
function renderMiniCalendar(card) {
  const host = $("#home-mini-cal");
  host.textContent = "";
  for (const name of MINI_CAL_DAYS) {
    const head = document.createElement("div");
    head.className = "mini-cal-head";
    head.textContent = name;
    host.append(head);
  }
  const counts = card.counts || {};
  for (const cell of trimmedMonth(monthGrid(card.year, card.month))) {
    const day = document.createElement("div");
    day.className = "mini-cal-day";
    if (!cell.inMonth) {
      // Days either side of the month keep their slot so the columns line up,
      // but they are not this month and carry no dot.
      day.classList.add("is-out");
    } else {
      day.textContent = String(cell.day);
      if (counts[cell.iso]) {
        day.classList.add("has-notes");
        day.title = `${counts[cell.iso]} on ${cell.iso}`;
      }
      if (cell.iso === card.today) day.classList.add("is-today");
    }
    host.append(day);
  }
}

function paintHome() {
  const payload = state.home;
  if (!payload) return;

  $("#home-vault").textContent = payload.vault;
  paintClock();
  $("#home-month").textContent = payload.calendar.label;
  renderMiniCalendar(payload.calendar);
  paintTiles($("#home-stats"), statTiles(payload.statistics));
  paintTiles($("#home-board"), statTiles(payload.board, BOARD_TILES));

  $("#home-streak").textContent = String(payload.mood.streak);
  const moodBits = [];
  if (payload.mood.level) moodBits.push(payload.mood.level);
  if (payload.mood.pain !== null && payload.mood.pain !== undefined) {
    moodBits.push(`pain ${payload.mood.pain}`);
  }
  if (!payload.mood.logged_today) moodBits.push("not logged today");
  $("#home-mood-line").textContent = moodBits.join(" · ");

  const today = payload.today_card;
  $("#home-today-line").textContent = todayLine(today);
  const list = $("#home-today-list");
  list.textContent = "";
  for (const title of today.titles.slice(0, 5)) {
    const li = document.createElement("li");
    li.textContent = `• ${title}`; // textContent: a title is user text
    list.append(li);
  }
  const action = todayAction(today);
  const button = $("#home-today-action");
  button.textContent = action.label;
  button.dataset.kind = action.kind;
}

// -- transcription -----------------------------------------------------------
//
// The view is a form and a list. Every number in it is the server's: a job's
// state, its progress, how long the source is. What the browser owns is the
// microphone -- the bytes of a recording do not exist anywhere until you press
// stop -- and the clock you read while waiting, which the server would render
// stale.
//
// A job is minutes long, so the list polls rather than pretending to know. The
// poll stops as soon as nothing is running: one that outlived its work would be
// a request a second, forever.

function transcribeError(message) {
  const box = $("#transcribe-error");
  box.textContent = message || "";
  box.classList.toggle("hidden", !message);
}

function renderEngineLine(engine) {
  const words = engineWords(engine);
  const line = $("#transcribe-engine");
  line.textContent = words.ok ? words.line : `${words.line} — ${words.detail}`;
  line.classList.toggle("is-bad", !words.ok);
}

function fillTranscribeOptions(status) {
  const model = $("#transcribe-model");
  if (!model.options.length) {
    for (const name of status.choices) model.add(new Option(name, name));
    model.value = status.model;
  }
  const collection = $("#transcribe-collection");
  if (!collection.options.length) {
    for (const name of status.collections) collection.add(new Option(name, name));
    collection.value = status.collection;
  }
}

//: One job. The bar is the server's fraction; the sentence is a translation of
//: the same fact into words, because a bar alone cannot say *what* it is doing.
function jobRow(job) {
  const li = document.createElement("li");
  li.className = "tr-job";
  li.dataset.state = job.state;

  const head = document.createElement("div");
  head.className = "tr-job-head";
  const name = document.createElement("span");
  name.className = "tr-job-name";
  name.textContent = sourceLabel(job);
  name.title = job.source || "";
  head.append(name);

  if (job.note_id) {
    const open = document.createElement("a");
    open.className = "tr-job-open";
    open.href = noteHref(job);
    open.textContent = "open note";
    head.append(open);
  }
  if (job.only_summary === false && job.note_id) {
    const again = document.createElement("button");
    again.type = "button";
    again.className = "tr-job-again";
    again.dataset.resummarise = job.note_id;
    again.textContent = "re-summarise";
    again.title = "run the summary again without transcribing the recording";
    head.append(again);
  }

  const bar = document.createElement("div");
  bar.className = "tr-bar";
  const fill = document.createElement("i");
  fill.style.width = `${percent(job)}%`;
  bar.append(fill);

  const line = document.createElement("p");
  line.className = "tr-job-line";
  line.textContent = headline(job);

  const meta = document.createElement("p");
  meta.className = "tr-job-meta";
  meta.textContent = [job.summarize ? "" : "no summary", job.model, durationText(job.duration_s)]
    .filter(Boolean)
    .join(" · ");

  li.append(head, bar, line, meta);
  return li;
}

function renderTranscribeJobs(jobs) {
  $("#transcribe-list").replaceChildren(...jobs.map(jobRow));
  $("#transcribe-count").textContent = String(jobs.length);
  $("#transcribe-empty").classList.toggle("hidden", jobs.length > 0);
}

async function renderTranscribe() {
  let status;
  try {
    status = await api.transcribe();
  } catch (err) {
    transcribeError(`could not ask what is installed: ${err.message}`);
    return;
  }
  state.transcribeStatus = status;
  transcribeError("");
  renderEngineLine(status.engine);
  fillTranscribeOptions(status);
  renderTranscribeJobs(status.jobs);
  scheduleTranscribePoll(status.jobs);
}

function scheduleTranscribePoll(jobs) {
  clearTimeout(state.transcribeTimer);
  state.transcribeTimer = null;
  const running = jobs.some((job) => job.state !== "done" && job.state !== "failed");
  if (!running) return;
  state.transcribeTimer = setTimeout(() => { renderTranscribe(); }, 1500);
}

//: Start something and show it happening. `run` is a thunk so an upload and a
//: path go through the same path -- the difference is what the server receives,
//: not what the view does about it.
async function startTranscribe(run) {
  transcribeError("");
  $("#transcribe-start").disabled = true;
  try {
    await run();
    await renderTranscribe();
  } catch (err) {
    transcribeError(err.message);
  } finally {
    $("#transcribe-start").disabled = false;
  }
}

async function submitTranscribe(event) {
  event.preventDefault();
  const model = $("#transcribe-model").value;
  const collection = $("#transcribe-collection").value;
  const summarize = $("#transcribe-summarize").checked;
  const file = state.transcribeFile;
  const path = $("#transcribe-path").value.trim();
  if (file) {
    await startTranscribe(() => api.transcribeUpload(file, {
      name: file.name, model, collection, summarize,
    }));
    return;
  }
  if (path) {
    await startTranscribe(() => api.transcribeStart({ path, model, collection, summarize }));
    return;
  }
  transcribeError("give it a path, a file, or a recording");
}

function pickTranscribeFile(event) {
  const file = (event.target.files || [])[0] || null;
  state.transcribeFile = file;
  $("#transcribe-picked").textContent = file
    ? `${file.name} · ${Math.round(file.size / 1024)} KB`
    : "nothing chosen yet";
  // One input at a time: a path and a file are two different instructions, and
  // guessing which one was meant is how a form transcribes the wrong thing.
  if (file) $("#transcribe-path").value = "";
}

// -- the microphone ----------------------------------------------------------

let recorder = null;
let recordedChunks = [];
let recordClock = null;
let recordStartedAt = 0;

async function toggleRecord() {
  if (recorder) {
    stopRecording();
    return;
  }
  if (!micAvailable()) {
    transcribeError("this browser cannot record from a microphone");
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    transcribeError(`the microphone was refused: ${err.message}`);
    return;
  }
  const mime = pickRecorderMime((type) => MediaRecorder.isTypeSupported(type));
  recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  recordedChunks = [];
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data && event.data.size) recordedChunks.push(event.data);
  });
  recorder.addEventListener("stop", async () => {
    stream.getTracks().forEach((track) => track.stop());
    const type = recorder.mimeType || mime || "audio/webm";
    const blob = new Blob(recordedChunks, { type });
    recorder = null;
    recordedChunks = [];
    stopRecordClock();
    const name = `${recordingName()}.${extensionFor(type)}`;
    $("#transcribe-picked").textContent =
      `${name} · ${Math.round(blob.size / 1024)} KB`;
    const model = $("#transcribe-model").value;
    const collection = $("#transcribe-collection").value;
    await startTranscribe(() => api.transcribeUpload(blob, {
      name, model, collection, summarize: $("#transcribe-summarize").checked,
    }));
  });
  recorder.start();
  recordStartedAt = Date.now();
  const button = $("#transcribe-record");
  button.classList.add("is-recording");
  button.textContent = "■ Stop and transcribe";
  // A recording has no known length while it is being made, so the clock is the
  // only honest thing to show.
  recordClock = setInterval(() => {
    const seconds = Math.round((Date.now() - recordStartedAt) / 1000);
    $("#transcribe-picked").textContent = `recording · ${elapsedText(seconds)}`;
  }, 500);
}

function stopRecording() {
  if (recorder && recorder.state !== "inactive") recorder.stop();
  stopRecordClock();
}

function stopRecordClock() {
  clearInterval(recordClock);
  recordClock = null;
  const button = $("#transcribe-record");
  if (!button) return;
  button.classList.remove("is-recording");
  button.textContent = "● Record from the mic";
}

async function renderHome() {
  const month = state.homeMonth || new Date();
  const first = localIsoDate(new Date(month.getFullYear(), month.getMonth(), 1));
  try {
    state.home = await api.home(first);
  } catch {
    // Leave the cards standing. An empty dashboard reads as an empty vault,
    // which is a worse thing to say than a slightly stale one.
    return;
  }
  paintHome();
}

function shiftHomeMonth(delta) {
  const shown = state.home?.calendar;
  const base = shown ? new Date(shown.year, shown.month - 1, 1) : new Date();
  const [year, month] = shiftMonth(base.getFullYear(), base.getMonth() + 1, delta);
  state.homeMonth = new Date(year, month - 1, 1);
  return renderHome();
}

/* ------------------------------------------------------------------ boot -- */

window.addEventListener("DOMContentLoaded", async () => {
  $$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
  $("#rapid-form").addEventListener("submit", submitRapid);
  $("#template-form").addEventListener("submit", submitTemplate);
  $("#new-collection-form").addEventListener("submit", createCollection);
  $("#note-save").addEventListener("click", saveEditor);
  $("#note-delete").addEventListener("click", deleteEditor);
  $("#note-close").addEventListener("click", closeEditor);
  $("#new-note-btn").addEventListener("click", createBlankNote);

  // -- board
  $("#board-collection").addEventListener("change", (e) => {
    state.boardCollection = e.target.value;
    saveBoardPrefs();
    renderBoard();
  });
  $("#board-hide-done").addEventListener("click", () => {
    state.hideDone = !state.hideDone;
    saveBoardPrefs();
    renderBoard();
  });
  $("#board-blocked-only").addEventListener("click", () => {
    state.blockedOnly = !state.blockedOnly;
    saveBoardPrefs();
    renderBoard();
  });
  $("#board-new-task").addEventListener("click", createBlankNote);

  // -- the dashboard
  $("#home-new-note").addEventListener("click", createBlankNote);
  $("#home-search").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    // Hand off to the real search rather than growing a second one: whatever
    // is typed here is what the topbar would have searched.
    const box = $("#search-box");
    box.value = e.target.value;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.focus();
    box.select();
  });
  $$("#home-view [data-entry]").forEach((b) => {
    b.addEventListener("click", () => {
      // Through the tab, so the URL and the ink bar move with it.
      showView("rapid");
      const input = $("#rapid-input");
      input.value = ENTRY_GLYPH[b.dataset.entry] || "";
      input.focus();
    });
  });
  $$("#home-view [data-jump]").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelector(`.tab[data-view="${b.dataset.jump}"]`)?.click();
    });
  });
  // -- transcribe
  $("#transcribe-form").addEventListener("submit", submitTranscribe);
  $("#transcribe-file").addEventListener("change", pickTranscribeFile);
  $("#transcribe-record").addEventListener("click", toggleRecord);
  $("#transcribe-path").addEventListener("input", () => {
    // Typing a path is a different instruction from having picked a file, so it
    // replaces the picked one rather than competing with it.
    state.transcribeFile = null;
    $("#transcribe-file").value = "";
    $("#transcribe-picked").textContent = "nothing chosen yet";
  });
  $("#transcribe-list").addEventListener("click", async (event) => {
    const again = event.target.closest("[data-resummarise]");
    if (!again) return;
    await startTranscribe(() => api.transcribeResummarise(again.dataset.resummarise));
  });

  $("#home-today-action").addEventListener("click", async () => {
    const card = state.home?.today_card;
    if (!card) return;
    if ($("#home-today-action").dataset.kind === "open") {
      await openEditor(`daily-${card.date}`);
      return;
    }
    $("#home-today-action").disabled = true;
    $("#home-today-action").textContent = "writing…";
    try {
      await api.writeDay(card.date);
    } catch (err) {
      $("#home-today-action").textContent = "could not write it — try again";
      $("#home-today-action").disabled = false;
      return;
    }
    $("#home-today-action").disabled = false;
    await refresh(); // refresh() re-renders home, which repaints the card
  });
  $("#home-month-prev").addEventListener("click", () => shiftHomeMonth(-1));
  $("#home-month-next").addEventListener("click", () => shiftHomeMonth(1));
  // The clock is the one card that ages on its own.
  setInterval(paintClock, 20000);

  // -- dependencies
  $("#dep-add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    addDependency($("#dep-input").value);
  });

  $("#timeline-date").value = todayIso();
  $("#timeline-date").addEventListener("change", renderTimeline);

  $$(".mood").forEach((b) => b.addEventListener("click", () => pickMood(b.dataset.mood)));
  $(".mood-clear").addEventListener("click", () => pickMood(""));
  // `input` here, not `change`: the editor does not write until Save, so there
  // is no per-pixel vault write to avoid, and the label should track the drag.
  $("#note-pain").addEventListener("input", (e) => pickPain(e.target.value));
  $("#note-pain-clear").addEventListener("click", () => pickPain(""));

  // -- mood view
  $("#mood-log-picks").addEventListener("click", (e) => {
    const btn = e.target.closest(".mood-pick");
    if (btn) logMood(btn.dataset.mood, undefined);
  });
  // `change`, not `input`: input fires on every pixel of the drag, and each one
  // would be a write to the vault.
  $("#mood-log-pain").addEventListener("change", (e) => {
    logMood(undefined, Number(e.target.value));
  });
  $("#mood-log-clear").addEventListener("click", () => logMood(null, null));
  $$("[data-mood-range]").forEach((b) => b.addEventListener("click", () => {
    state.moodDays = Number(b.dataset.moodRange);
    $$("[data-mood-range]").forEach((o) => {
      o.setAttribute("aria-pressed", String(o === b));
    });
    renderMood();
  }));
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

  // The hint follows both halves of the rule, so it listens to the dates too: a
  // time you just typed is fine until the last date is deleted under it.
  $("#note-at").addEventListener("input", renderTimeHint);
  $("#note-until").addEventListener("input", renderTimeHint);
  $("#note-dates").addEventListener("input", renderTimeHint);
  $("#note-time-clear").addEventListener("click", () => {
    $("#note-at").value = "";
    $("#note-until").value = "";
    renderTimeHint();
  });

  // local ai
  $("#ai-summarize").addEventListener("click", () => {
    if (!state.activeId) return aiSetStatus("open a note first", "error");
    aiRun("/api/ai/summarize", { id: state.activeId }, "summarize");
  });
  $("#ai-tags").addEventListener("click", () => {
    if (!state.activeId) return aiSetStatus("open a note first", "error");
    aiRun("/api/ai/tags", { id: state.activeId }, "tags");
  });
  // Not a stream: the recap is written to a file, so it fits none of the
  // streaming handlers above and needs its own path.
  $("#ai-day-recap").addEventListener("click", runDayRecap);
  $("#ai-week-recap").addEventListener("click", runWeekRecap);

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
  loadBoardPrefs();

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
  // A deep link lands on a note the same way opening it does, so it must load
  // the dependency panel too — otherwise a reloaded page silently loses it.
  if (state.activeId) await renderDeps(state.activeId);
  moveInk();
});
