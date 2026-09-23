// static/js/app.js — orchestrates the four views.
import { parseRapidInput } from "./rapid.js";
import { monthGrid, shiftMonth } from "./calendar.js";

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
    return (await fetch(`/api/notes/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    })).json();
  },
  async deleteNote(id)   { return await fetch(`/api/notes/${id}`, { method: "DELETE" }); },
  async search(q)        { return (await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json(); },
  async calendar(y, m)   { return (await fetch(`/api/calendar/${y}/${m}`)).json(); },
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
};

const $ = (s) => document.querySelector(s);

const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function showView(name) {
  state.activeView = name;
  for (const v of ["rapid","collections","timeline","calendar"]) {
    $("#" + v + "-pane").classList.toggle("hidden", v !== name);
  }
  for (const t of document.querySelectorAll(".tab")) {
    t.classList.toggle("active", t.dataset.view === name);
  }
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
}

function renderCollections() {
  const ul = $("#collection-list");
  ul.innerHTML = "";
  const allLi = document.createElement("li");
  allLi.textContent = "all";
  allLi.onclick = () => { state.rapidFilterCollection = null; showView("rapid"); };
  if (state.rapidFilterCollection === null) allLi.style.fontWeight = "bold";
  ul.appendChild(allLi);
  for (const c of state.collections) {
    const li = document.createElement("li");
    li.textContent = c;
    if (c === state.rapidFilterCollection) li.style.fontWeight = "bold";
    li.onclick = () => { state.rapidFilterCollection = c; showView("rapid"); };
    ul.appendChild(li);
  }
}

function renderRapid() {
  const ul = $("#rapid-list");
  ul.innerHTML = "";
  let notes = state.notes.slice();
  if (state.rapidFilterCollection) {
    notes = notes.filter((n) => n.collection === state.rapidFilterCollection);
  }
  notes.sort((a, b) => (b.dates?.[0] || "").localeCompare(a.dates?.[0] || ""));
  for (const n of notes) {
    const li = document.createElement("li");
    let cls = `sig-${n.signifier} status-${n.status}`;
    if (n.mood) cls += ` mood-${n.mood}`;
    li.className = cls;
    li.textContent = n.title;
    if (n.tags && n.tags.length) {
      const chips = document.createElement("span");
      chips.className = "tags-chips";
      for (const t of n.tags) {
        const c = document.createElement("span");
        c.className = "tag-chip";
        c.textContent = "#" + t;
        chips.appendChild(c);
      }
      li.appendChild(chips);
    }
    li.onclick = () => openEditor(n.id);
    ul.appendChild(li);
  }
}

function renderTimeline() {
  const date = $("#timeline-date").value || new Date().toISOString().slice(0, 10);
  const ul = $("#timeline-list");
  ul.innerHTML = "";
  for (const n of state.notes.filter((n) => (n.dates || []).includes(date))) {
    const li = document.createElement("li");
    let cls = `sig-${n.signifier}`;
    if (n.mood) cls += ` mood-${n.mood}`;
    li.className = cls;
    li.textContent = n.title;
    li.onclick = () => openEditor(n.id);
    ul.appendChild(li);
  }
}

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
  $("#note-tags").value = (n.tags || []).join(", ");
  const sel = $("#note-collection");
  sel.innerHTML = state.collections
    .map((c) => `<option ${c === n.collection ? "selected" : ""}>${c}</option>`)
    .join("");
  // Mood picker
  state.activeMood = n.mood || null;
  document.querySelectorAll(".mood").forEach((b) => {
    b.classList.toggle("active", (b.dataset.mood || "") === (state.activeMood || ""));
  });
  // Editor mode (split/write/preview)
  setEditorMode(state.editorMode);
}

function renderPreview() {
  if (!state.activeId) return;
  const md = $("#note-body").value || "";
  // marked is loaded as a global script (UMD)
  const html = window.marked ? window.marked.parse(md) : escapeHtml(md);
  $("#note-preview").innerHTML = html;
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function setEditorMode(mode) {
  state.editorMode = mode;
  document.querySelector(".editor-body").dataset.mode = mode;
  document.querySelectorAll(".editor-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.mode === mode)
  );
  renderPreview();
}

function openEditor(id) {
  state.activeId = id;
  renderEditor();
}

async function submitRapid(e) {
  e.preventDefault();
  const parsed = parseRapidInput($("#rapid-input").value);
  if (!parsed) return;
  await api.createNote(parsed);
  $("#rapid-input").value = "";
  await refresh();
}

async function saveEditor() {
  if (!state.activeId) return;
  const dates = $("#note-dates").value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const tags = $("#note-tags").value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  await api.updateNote(state.activeId, {
    title: $("#note-title").value,
    body: $("#note-body").value,
    signifier: $("#note-signifier").value,
    status: $("#note-status").value,
    collection: $("#note-collection").value,
    dates,
    tags,
    mood: state.activeMood || null,
  });
  await refresh();
}

async function deleteEditor() {
  if (!state.activeId) return;
  if (!confirm("Delete this note?")) return;
  await api.deleteNote(state.activeId);
  state.activeId = null;
  await refresh();
}

async function createCollection(e) {
  e.preventDefault();
  const name = $("#new-collection-name").value.trim();
  if (!name) return;
  await api.createNote({
    id: "col-" + Date.now().toString(36),
    collection: name,
    title: "Welcome to " + name,
    body: "",
    signifier: "note",
    status: "open",
    dates: [new Date().toISOString().slice(0, 10)],
  });
  $("#new-collection-name").value = "";
  await refresh();
}

function pickMood(mood) {
  // '' = clear
  state.activeMood = mood || null;
  document.querySelectorAll(".mood").forEach((b) => {
    b.classList.toggle("active", (b.dataset.mood || "") === (state.activeMood || ""));
  });
}

// --- Calendar ---
async function renderCalendar() {
  const cells = monthGrid(state.calYear, state.calMonth);
  $("#cal-label").textContent = `${MONTHS[state.calMonth - 1]} ${state.calYear}`;
  const counts = await api.calendar(state.calYear, state.calMonth);
  const today = new Date().toISOString().slice(0, 10);
  const grid = $("#calendar-grid");
  grid.innerHTML = "";
  for (const c of cells) {
    const div = document.createElement("div");
    let cls = "cal-cell";
    if (!c.inMonth) cls += " out";
    if (c.iso === today) cls += " today";
    div.className = cls;
    const day = document.createElement("span");
    day.className = "day";
    day.textContent = c.day;
    div.appendChild(day);
    const n = counts[c.iso];
    if (n) {
      const cnt = document.createElement("span");
      cnt.className = "count";
      cnt.textContent = n;
      div.appendChild(cnt);
    }
    div.onclick = () => {
      $("#timeline-date").value = c.iso;
      showView("timeline");
    };
    grid.appendChild(div);
  }
}

// --- Search ---
let searchTimer = null;
function onSearchInput() {
  clearTimeout(searchTimer);
  const q = $("#search-box").value.trim();
  searchTimer = setTimeout(async () => {
    if (!q) {
      $("#search-results").classList.add("hidden");
      return;
    }
    const hits = await api.search(q);
    const box = $("#search-results");
    box.innerHTML = "";
    if (!hits.length) {
      const div = document.createElement("div");
      div.className = "hit-item";
      div.textContent = "(no matches)";
      box.appendChild(div);
    } else {
      for (const h of hits.slice(0, 20)) {
        const div = document.createElement("div");
        div.className = "hit-item";
        const title = document.createElement("div");
        title.className = "hit-title";
        title.textContent = h.title;
        const snip = document.createElement("div");
        snip.className = "hit-snippet";
        const body = (h.body || "").slice(0, 120);
        snip.textContent = body || `(${h.collection}, ${h.dates?.[0] || "no date"})`;
        div.appendChild(title);
        div.appendChild(snip);
        div.onclick = () => {
          $("#search-results").classList.add("hidden");
          $("#search-box").value = "";
          openEditor(h.id);
        };
        box.appendChild(div);
      }
    }
    box.classList.remove("hidden");
  }, 200);
}

window.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => showView(t.dataset.view)));
  $("#rapid-form").onsubmit = submitRapid;
  $("#new-collection-form").onsubmit = createCollection;
  $("#note-save").onclick = saveEditor;
  $("#note-delete").onclick = deleteEditor;
  $("#timeline-date").value = new Date().toISOString().slice(0, 10);
  $("#timeline-date").onchange = renderTimeline;
  // mood buttons
  document.querySelectorAll(".mood").forEach((b) =>
    b.addEventListener("click", () => pickMood(b.dataset.mood))
  );
  // editor mode tabs
  document.querySelectorAll(".editor-tab").forEach((t) =>
    t.addEventListener("click", () => setEditorMode(t.dataset.mode))
  );
  // live preview update
  $("#note-body").addEventListener("input", () => {
    clearTimeout(window._pvTimer);
    window._pvTimer = setTimeout(renderPreview, 150);
  });
  // calendar nav
  $("#cal-prev").onclick = () => {
    [state.calYear, state.calMonth] = shiftMonth(state.calYear, state.calMonth, -1);
    renderCalendar();
  };
  $("#cal-next").onclick = () => {
    [state.calYear, state.calMonth] = shiftMonth(state.calYear, state.calMonth, 1);
    renderCalendar();
  };
  // search
  $("#search-box").addEventListener("input", onSearchInput);
  $("#search-box").addEventListener("blur", () =>
    setTimeout(() => $("#search-results").classList.add("hidden"), 150)
  );
  $("#search-box").addEventListener("focus", () => {
    if ($("#search-box").value.trim()) $("#search-results").classList.remove("hidden");
  });

  await refresh();
});