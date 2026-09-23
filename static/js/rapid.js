// Pure helpers — testable without DOM.
export function parseRapidInput(text) {
  const trimmed = text.trim();
  if (!trimmed) return null;
  let signifier = "note";
  let body = trimmed;
  if (trimmed.startsWith("•") || trimmed.startsWith("*")) {
    signifier = "task"; body = trimmed.slice(1).trim();
  } else if (trimmed.startsWith("○") || trimmed.startsWith("o ")) {
    signifier = "event"; body = trimmed.slice(1).trim();
  } else if (trimmed.startsWith("–") || trimmed.startsWith("-")) {
    signifier = "note"; body = trimmed.slice(1).trim();
  }
  const title = body.split(/\s+/, 6).join(" ");
  const id = (Date.now().toString(36) + Math.random().toString(36).slice(2,6));
  return {
    id,
    title,
    body,
    signifier,
    status: "open",
    dates: [new Date().toISOString().slice(0,10)],
    collection: "inbox",
  };
}