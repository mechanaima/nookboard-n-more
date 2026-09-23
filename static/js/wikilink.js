// Pure wikilink helpers — testable without DOM.
const RE = /\[\[([^\]]+)\]\]/g;

export function extractWikilinks(md) {
  const out = [];
  let m;
  RE.lastIndex = 0;
  while ((m = RE.exec(md))) out.push(m[1].trim());
  return out;
}

export function renderWikilinks(md, knownTitles /* Set<string>|string[] */) {
  const known = knownTitles instanceof Set ? knownTitles : new Set(knownTitles);
  return md.replace(RE, (_, title) => {
    const t = title.trim();
    const cls = known.has(t) ? "wikilink exists" : "wikilink missing";
    return `<a class="${cls}" data-title="${escapeAttr(t)}">${escapeHtml(t)}</a>`;
  });
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return s.replace(/&/g, "&amp;")
          .replace(/"/g, "&quot;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
}