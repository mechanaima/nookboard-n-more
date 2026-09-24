// Lucide icons, as the views use them.
//
// The drawings are vendored in `static/vendor/lucide.js` -- generated, not typed;
// see `tools/build-lucide.py`. This is the thin part: a name becomes an element, and
// a name list becomes a search. There is no second copy of "which icons exist" in
// this app: the browser reads the same generated set the server does, and a test
// asserts the two agree, so a name the picker offers is a name that draws.

import { ICONS, LUCIDE_VERSION } from "../vendor/lucide.js";

export { LUCIDE_VERSION };

/** Every name, which is also the order Lucide ships them in. */
export const NAMES = Object.freeze(Object.keys(ICONS));
export const COUNT = NAMES.length;

/** How many the picker draws before it stops.
 *
 * Two thousand icons is a grid nobody scrolls; narrowing with a search is how you
 * find one. The count is printed beside the results, so a short list never quietly
 * looks like the whole set.
 */
export const LIMIT = 120;

const SVG_NS = "http://www.w3.org/2000/svg";

/** `a-arrow-down` reads as `a arrow down`. The name is the only label there is. */
export function labelFor(name) {
  return String(name || "").replace(/[-_]+/g, " ").trim();
}

/** Whether this is a name that can be drawn. False means a note says something
 *  nothing could draw -- kept, not refused, and worth saying out loud. */
export function has(name) {
  return Object.hasOwn(ICONS, String(name || ""));
}

/** Where a name ranks against a search: fewer is better, `null` is no match.
 *
 * Prefix beats word-start, word-start beats anywhere-in-the-name: someone typing
 * `serv` wants `server`, not `cloud-server-2`. Within a rank, Lucide's own order.
 */
export function rank(name, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return 0;
  const words = q.split(/[\s-]+/).filter(Boolean);
  const lower = name.toLowerCase();
  if (lower.startsWith(q)) return 0;
  if (words.every((w) => lower.split("-").some((part) => part.startsWith(w)))) return 1;
  if (words.every((w) => lower.includes(w))) return 2;
  return null;
}

/** The picker's answer: which names to draw, and how many there were in total. */
export function filter(query, limit = LIMIT) {
  const q = String(query || "").trim();
  const scored = [];
  for (const name of NAMES) {
    const score = rank(name, q);
    if (score !== null) scored.push([score, name]);
  }
  if (q) scored.sort((a, b) => (a[0] - b[0]) || a[1].localeCompare(b[1]));
  const names = scored.map(([, name]) => name);
  return { names: names.slice(0, limit), total: names.length, shown: Math.min(names.length, limit) };
}

/** One icon, as an element that inherits colour and size like any other text.
 *
 * `currentColor` and `1em` are the whole point: a note's icon should sit in a card
 * and take the card's colour, including the colour of whatever state the card is in.
 * Returns null for a name nothing can draw, so callers decide what a missing icon
 * looks like rather than getting an empty box.
 */
export function draw(name, { size = "1em", className = "note-icon" } = {}) {
  if (!has(name)) return null;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  // Vendored markup from the generated file, never anything a person typed.
  svg.innerHTML = ICONS[name];
  return svg;
}

/** The title for a note's icon: what it is called, or why it is not there. */
export function iconTitle(name) {
  if (!name) return "";
  return has(name) ? labelFor(name) : `${labelFor(name)} \u2014 not a Lucide icon`;
}
