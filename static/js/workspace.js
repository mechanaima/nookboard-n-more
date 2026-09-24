// Display helpers for the Workspaces view.
//
// The *facts* are the server's: `app/workspace.py` decides what a folder is, and
// every count, path and sentence arrives already computed. Nothing here
// re-derives any of it — no counting files, no aging a commit, no rebuilding a
// path from parts. What lives here is only what a browser has to do: pick a
// class, join a list, decide whether a button can work.

//: The one line under a card's title. Never "0 uncommitted" — a thing that is
//: fine says what is fine, not how much of the bad thing it does not have.
export function stateClass(state) {
  if (!state || state.missing || state.problem) return "is-broken";
  if (state.reasons && state.reasons.length) return "is-wanted";
  if (!state.is_repo) return "is-plain";
  return "is-settled";
}

//: Which folder of the code is what, biggest first, and honest about the tail.
export function languageLine(languages, limit = 3) {
  const pairs = Object.entries(languages || {});
  if (!pairs.length) return "";
  const shown = pairs.slice(0, limit).map(([name, count]) => `${name} ${count}`);
  const rest = pairs.length - shown.length;
  return rest > 0 ? `${shown.join(" · ")} +${rest} more` : shown.join(" · ");
}

//: The branch line: the branch, and how far it has drifted from its upstream.
//: "ahead" and "behind" are absent when there is no upstream, and absent is not
//: zero — a branch with nowhere to push to is not level with anything.
export function branchLine(state) {
  if (!state || !state.is_repo) return "";
  const branch = state.detached ? "detached" : (state.branch || "no branch");
  const drift = [];
  if (typeof state.ahead === "number" && state.ahead > 0) drift.push(`↑${state.ahead}`);
  if (typeof state.behind === "number" && state.behind > 0) drift.push(`↓${state.behind}`);
  const upstream = state.upstream ? ` → ${state.upstream}` : "";
  return [`${branch}${upstream}`, ...drift].join(" ");
}

//: What the last commit was, in one line. A repo with no commits says so rather
//: than showing an empty subject beside a dash.
export function commitLine(state) {
  if (!state || !state.is_repo) return "";
  // The *age* is not repeated here: the headline line above already ends with
  // "17 hours ago", and printing it twice makes one fact look like two. An
  // empty tree says nothing -- the headline already says "no commits yet".
  return ((state.last && state.last.subject) || "").trim();
}

//: The changed files as a list, capped, with how many were left out. The names
//: and the count come from the same read, so they cannot disagree -- and the
//: card makes each name a place you can open, which is why this returns the list
//: rather than only the sentence.
export function changedFiles(state, limit = 6) {
  const files = [...(state.changed || []), ...(state.untracked || [])];
  return { files: files.slice(0, limit), rest: Math.max(0, files.length - limit) };
}

//: The three buttons, and whether each can actually do anything here. A button
//: whose tool is missing is disabled AND says which tool — a greyed-out control
//: with no explanation is the app keeping a secret from the person it serves.
export function buttons(state, tools) {
  const found = tools || {};
  const wanted = [
    { what: "editor", label: "Editor" },
    { what: "terminal", label: "Terminal" },
    { what: "files", label: "Files" },
  ];
  return wanted.map(({ what, label }) => {
    const tool = found[what] || "";
    const gone = Boolean(state && (state.missing || state.problem));
    return {
      what,
      label,
      enabled: Boolean(tool) && !gone,
      why: gone ? "the folder is not there"
        : !tool ? `no ${what} installed on this machine`
          : `open it in ${tool.split("/").pop()}`,
    };
  });
}

//: The markers on a card, capped. Each is a place in the code, so it reads
//: `file:line` before it reads as prose — that is the part you can act on.
export function markerLine(marker) {
  if (!marker) return "";
  const where = `${marker.file}:${marker.line}`;
  const said = (marker.text || "").trim();
  return said ? `${where}  ${marker.kind} ${said}` : `${where}  ${marker.kind}`;
}

export function markerCount(markers) {
  const n = (markers || []).length;
  if (!n) return "";
  return n === 1 ? "1 marker" : `${n} markers`;
}
