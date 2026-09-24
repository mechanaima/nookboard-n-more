// Queries in a note body: finding them, and splicing their answers back in.
//
// The browser does not evaluate a query -- the vault is on the server, so the
// server answers. This module is only the text handling, which is the part worth
// testing on its own: above all, that a fence in another language survives
// untouched, because silently rewriting someone's code sample would be a nasty
// thing for a notes app to do.

const OPEN = /^[ \t]*```[ \t]*nookboard[ \t]*$/i;
const CLOSE = /^[ \t]*```[ \t]*$/;

// Every query fence in a body, in order.
//
// An unclosed fence is not a query: it is one you are midway through typing, and
// treating it as a query would make the rest of the note disappear into a
// result while you were still writing it. Leaving it alone also means there is a
// way to *show* a query in a note rather than run it.
export function splitQueries(md) {
  const lines = String(md ?? "").split("\n");
  const found = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!OPEN.test(lines[i])) continue;
    let close = i + 1;
    while (close < lines.length && !CLOSE.test(lines[close])) close += 1;
    if (close >= lines.length) continue;
    found.push({ open: i, close, text: lines.slice(i + 1, close).join("\n").trim() });
    i = close;
  }
  return found;
}

// The body with each query replaced by `resolve(text)`.
//
// `resolve` returns a markdown string, or undefined to leave the block as
// written -- which is what happens while an answer is still being fetched.
// Blanking it in the meantime would look like the note had lost something.
export function spliceQueries(md, resolve) {
  const source = String(md ?? "");
  const lines = source.split("\n");
  const out = [];
  let cursor = 0;
  for (const block of splitQueries(source)) {
    out.push(...lines.slice(cursor, block.open));
    const answer = resolve(block.text);
    if (answer === undefined) {
      out.push(...lines.slice(block.open, block.close + 1));
    } else {
      out.push(...String(answer).split("\n"));
    }
    cursor = block.close + 1;
  }
  out.push(...lines.slice(cursor));
  return out.join("\n");
}
