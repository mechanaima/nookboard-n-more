"""AI features on top of a local llama.cpp server.

Five things, all local:

  summarize   a short markdown summary of the open note
  tags        tag suggestions, filtered against tags the note already has
  links       wiki link suggestions drawn from titles that already exist
  ask         a question answered from the notes most relevant to it
  daily       a recap of a day's finished work, for the day's own note

The first four stream to the browser. `daily` does not: it is usually run by the
scheduler with nobody watching, and it has to write its result to a file rather
than hand it to a client.

`ask` does NOT use embeddings. Retrieval is term-overlap scoring over the
vault, which is honest about what it is: good at keyword-ish questions,
useless at paraphrase. Wiring a real index in is a separate job.

The model runs at roughly 26 tokens/second locally, so every one of these is
slow enough that the caller has to stream.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable, Sequence

from .models import Note

MAX_TAGS = 6
MAX_LINKS = 5
MAX_CONTEXT_NOTES = 8
CONTEXT_CHARS_PER_NOTE = 1200
#: A recap longer than this stops being a recap and becomes an essay in a note.
MAX_RECAP_CHARS = 600

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "how", "i", "in", "is", "it", "its", "me", "my", "of",
    "on", "or", "that", "the", "their", "them", "there", "these", "they",
    "this", "to", "was", "were", "what", "when", "where", "which", "who",
    "why", "will", "with", "you", "your", "about", "do", "does", "did",
    "all", "any", "can", "just", "not", "so", "than", "then", "too",
}

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")


def _terms(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS]


# --- retrieval ------------------------------------------------------------

def select_relevant(notes: Sequence[Note], question: str, limit: int = MAX_CONTEXT_NOTES) -> list[Note]:
    """Rank notes by plain term overlap with the question.

    Deliberately not embeddings. This is cheap, has no index to build, and is
    easy to reason about when an answer looks wrong.
    """
    q_terms = _terms(question)
    if not q_terms:
        return []
    q_set = set(q_terms)
    scored: list[tuple[float, Note]] = []
    for note in notes:
        title_terms = set(_terms(note.title))
        body_terms = _terms(note.body or "")
        body_set = set(body_terms)
        # Title matches are a stronger signal than body matches.
        score = 3.0 * len(q_set & title_terms)
        score += 1.0 * len(q_set & body_set)
        score += 0.5 * sum(1 for t in body_terms if t in q_set)
        if score > 0:
            scored.append((score, note))
    scored.sort(key=lambda pair: (-pair[0], pair[1].title.lower()))
    return [n for _, n in scored[:limit]]


# --- parsing model output -------------------------------------------------

def parse_tag_suggestions(text: str, existing: Iterable[str]) -> list[str]:
    """Pull usable tags out of whatever the model wrote.

    The model is asked for a bare comma-separated list but often wraps it in a
    label ("Here are some tags: a, b"). So: drop bullets, keep only what
    follows a trailing label, then reject anything tag-shaped-impossible
    (sentences, long phrases, punctuation).
    """
    have = {t.strip().lower() for t in existing}
    out: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lstrip("-*+ ").strip()
        if not line:
            continue
        if ":" in line:
            line = line.rsplit(":", 1)[1]
        for piece in line.split(","):
            candidate = piece.strip().strip(".").lstrip("#").strip()
            if not candidate or len(candidate) > 40:
                continue
            # A tag is one to three words; more than that is prose.
            if len(candidate.split()) > 3:
                continue
            if re.search(r"[.!?;:]", candidate):
                continue
            tag = re.sub(r"\s+", "-", candidate.lower()).strip("-")
            if not tag or tag in have or tag in out:
                continue
            out.append(tag)
    return out[:MAX_TAGS]


def parse_link_suggestions(text: str, known_titles: Iterable[str], *, exclude: str) -> list[str]:
    """Keep only suggestions that match an existing note title, in canonical form."""
    canon = {t.lower(): t for t in known_titles if t}
    canon.pop((exclude or "").lower(), None)
    out: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lstrip("-*+ ").strip()
        if not line:
            continue
        for candidate in (line, *line.split(",")):
            key = candidate.strip().strip(".").strip()
            if key.startswith("[[") and key.endswith("]]"):
                key = key[2:-2]
            key = key.split("|", 1)[0].strip().lower()
            hit = canon.get(key)
            if hit and hit not in out:
                out.append(hit)
                break
    return out[:MAX_LINKS]


#: List furniture a model may wrap a line in: bullets, checkboxes, numbering.
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*+\u2022]|\[[ xX]\]|\(?\d+[.)])\s*")


# --- prompts --------------------------------------------------------------

def _note_block(note: Note, *, limit: int | None = None) -> str:
    body = note.body or ""
    if limit and len(body) > limit:
        body = body[:limit] + "\n... [truncated]"
    head = f"# {note.title}\n"
    if note.tags:
        head += f"tags: {', '.join(note.tags)}\n"
    head += f"dates: {', '.join(d.isoformat() for d in note.dates) or 'none'}\n"
    return f"{head}\n{body}".strip()


def build_summary_messages(note: Note) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You summarise short notes. Reply with markdown only: two or three "
                "sentences of plain prose, then at most three bullet points for the "
                "actionable parts. No preamble, no headings, no restating the title."
            ),
        },
        {"role": "user", "content": _note_block(note)},
    ]


def build_tags_messages(note: Note, vault_tags: Sequence[str]) -> list[dict]:
    existing = ", ".join(vault_tags[:60]) or "none yet"
    return [
        {
            "role": "system",
            "content": (
                "You suggest short lowercase tags for notes. Tags are one or two words "
                "with hyphens, never sentences. Reply with ONLY a comma-separated list "
                f"of at most {MAX_TAGS} tags and nothing else."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Tags already used in this vault: {existing}\n"
                f"Prefer reusing those where they fit.\n\n"
                f"{_note_block(note)}\n\n"
                f"Already on this note: {', '.join(note.tags) or 'none'}"
            ),
        },
    ]


def build_links_messages(note: Note, candidate_titles: Sequence[str]) -> list[dict]:
    listing = "\n".join(f"- {t}" for t in candidate_titles) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "You connect notes in a personal wiki. You are given one note and a "
                "list of candidate titles that were ALREADY pre-selected as possibly "
                "related. Choose every candidate that shares a topic, entity or task "
                "with the note. Prefer naming a few real connections over naming none; "
                "a shared subject word is enough. Reply with ONLY the chosen titles, "
                "one per line, copied exactly as given. Reply NONE only if the note "
                "genuinely has nothing in common with any candidate."
            ),
        },
        {
            "role": "user",
            "content": f"{_note_block(note)}\n\nCandidate titles:\n{listing}",
        },
    ]


def build_daily_summary_messages(day: date, done: Sequence[Note]) -> list[dict]:
    """Ask for a recap of a day's finished work.

    The list is supplied as fact, so the instruction that matters most is the
    one forbidding invention: a journal entry that credits work nobody did is
    worse than a thin entry, because it is the thing you would later trust.
    """
    listing = "\n".join(f"- {n.title}" for n in done)
    return [
        {
            "role": "system",
            "content": (
                "You write a short first-person recap of a day's work for the "
                "person's own journal. Two or three sentences, plain prose. No "
                "heading, no bullets, no lists, no quotation marks. Say what "
                "actually got done and what it added up to. Use only the work "
                "listed: do not invent anything, do not estimate effort, do not "
                "add encouragement, and do not refer to the list itself."
            ),
        },
        {
            "role": "user",
            "content": f"Date: {day.isoformat()}\n\nCompleted:\n{listing}",
        },
    ]


def build_weekly_summary_messages(
    key: str, done_by_day: dict[date, list[Note]], felt: str | None
) -> list[dict]:
    """Ask for a recap of a week's finished work.

    The same prohibition as the daily prompt, for the same reason, plus one that
    only a longer period invites: a week is enough room for a model to invent a
    narrative arc -- "by Friday things were back on track" -- that nothing in the
    list supports. A week's recap is a summary, not a story about the week.
    """
    blocks = []
    for day in sorted(done_by_day):
        titles = ", ".join(n.title for n in done_by_day[day])
        blocks.append(f"{day.strftime('%A %d')}: {titles}")
    listing = "\n".join(blocks)
    user = f"Week: {key}\n\nFinished:\n{listing}"
    if felt:
        user += f"\n\nHow the week felt: {felt}"
    return [
        {
            "role": "system",
            "content": (
                "You write a short first-person recap of a week's work for the "
                "person's own journal. Three or four sentences, plain prose. No "
                "heading, no bullets, no lists, no quotation marks. Say what got "
                "done over the week and what it added up to. Use only the work "
                "listed: do not invent anything, do not narrate a trajectory the "
                "list does not show, do not estimate effort, do not add "
                "encouragement, and do not refer to the list itself. If how the "
                "week felt is given, you may mention it plainly, but do not read "
                "meaning into it."
            ),
        },
        {"role": "user", "content": user},
    ]


def parse_summary(text: str) -> str:
    """Collapse a model reply into one recap paragraph.

    This text lands in a Markdown file, so a stray heading or code fence would
    be committed to the note as broken markup. The section already lists the
    tasks, so bullets in the recap are joined into prose instead of repeating
    the list in a second shape.
    """
    parts: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        line = _LIST_PREFIX_RE.sub("", line).strip()
        if line:
            parts.append(line)
    para = re.sub(r"\s+", " ", " ".join(parts)).strip()
    para = para.strip('"').strip("\u201c\u201d").strip()
    if len(para) > MAX_RECAP_CHARS:
        cut = para[:MAX_RECAP_CHARS]
        # Trim to a sentence boundary when there is one, so the note does not
        # end mid-word.
        stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        para = (cut[: stop + 1] if stop > MAX_RECAP_CHARS // 2 else cut.rstrip()).strip()
    return para


def build_ask_messages(question: str, context: Sequence[Note]) -> list[dict]:
    if context:
        blocks = "\n\n---\n\n".join(
            _note_block(n, limit=CONTEXT_CHARS_PER_NOTE) for n in context
        )
    else:
        blocks = "(no notes matched this question)"
    return [
        {
            "role": "system",
            "content": (
                "You answer questions about a personal note collection. Use only the "
                "notes given. Cite the notes you rely on as [[Note title]]. If the "
                "notes do not answer the question, say so plainly instead of guessing."
            ),
        },
        {"role": "user", "content": f"Notes:\n\n{blocks}\n\nQuestion: {question}"},
    ]
