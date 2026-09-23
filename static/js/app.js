// static/js/app.js — orchestrates the three views.
import { parseRapidInput } from "./rapid.js";

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
};

const state = {
  notes: [],
  collections: [],
  activeView: "rapid",
  activeId: null,
  rapidFilterCollection: null, // null = show all
};

const $ = (s) => document.querySelector(s);

function showView(name) {
  state.activeView = name;
  for (const v of ["rapid", "collections", "timeline"]) {
    $("#" + v + "-pane").classList.toggle("hidden", v !== name);
  }
  for (const t of document.querySelectorAll(".tab")) {
    t.classList.toggle("active", t.dataset.view === name);
  }
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
}

function renderCollections() {
  const ul = $("#collection-list");
  ul.innerHTML = "";
  // "all" pseudo-collection clears filter
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
    li.className = `sig-${n.signifier} status-${n.status}`;
    li.textContent = n.title;
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
    li.className = `sig-${n.signifier}`;
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
  const sel = $("#note-collection");
  sel.innerHTML = state.collections
    .map((c) => `<option ${c === n.collection ? "selected" : ""}>${c}</option>`)
    .join("");
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
  await api.updateNote(state.activeId, {
    title: $("#note-title").value,
    body: $("#note-body").value,
    signifier: $("#note-signifier").value,
    status: $("#note-status").value,
    collection: $("#note-collection").value,
    dates,
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
  // We need at least one note in the collection for it to be listable,
  // so create a placeholder note, then move on.
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

window.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => showView(t.dataset.view)));
  $("#rapid-form").onsubmit = submitRapid;
  $("#new-collection-form").onsubmit = createCollection;
  $("#note-save").onclick = saveEditor;
  $("#note-delete").onclick = deleteEditor;
  $("#timeline-date").value = new Date().toISOString().slice(0, 10);
  $("#timeline-date").onchange = renderTimeline;
  await refresh();
});