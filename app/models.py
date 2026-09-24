"""Domain model for nookboard notes.

A Note is the universal record. It can be:
  - a long-form NeatNook-style entry inside a collection, or
  - a date-tagged Agenda-style note that surfaces on the timeline, or
  - a rapid-log BuJo-style bullet (task / event / note).

The same record drives all three because they share the same primitives
in practice: a thing you wrote, with optional dates, optional collection,
and optional task state.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from enum import Enum
from typing import Optional

import re

import frontmatter

from .obsidian import extract_inline_tags, split_frontmatter_tags
# `schedule` imports Note only for typing (behind TYPE_CHECKING), so this is not
# a cycle. One parser for a time, used by the model and by the ICS feed alike.
from .schedule import label as schedule_label
from .schedule import parse_time, raw_time_from_frontmatter


class Signifier(str, Enum):
    """BuJo bullet signifiers. None = general note, not a bullet."""
    TASK = "task"          # •  (to-do)
    EVENT = "event"        # ○  (date-related)
    NOTE = "note"          # –  (observation / fact)


class Status(str, Enum):
    """BuJo task states. Ignored for Signifier.NOTE."""
    OPEN = "open"          # •
    COMPLETE = "complete"  # X
    MIGRATED = "migrated"  # >
    SCHEDULED = "scheduled"# <
    IRRELEVANT = "irrelevant"  # struck through


class Stage(str, Enum):
    """Board column — the kanban view's axis.

    Orthogonal to Status in principle, reconciled with it in practice (see
    `reconcile`): a card in `done` is a complete task, and completing a task
    moves its card to `done`. Two axes that can disagree would mean the board
    and the rapid log telling different stories about the same note.

    `None` on a Note means "never placed on the board" — its column is derived
    from status. That keeps rapid-log entries free of a frontmatter line they
    have not earned yet.
    """
    BACKLOG = "backlog"
    TODO = "todo"
    DOING = "doing"
    REVIEW = "review"
    DONE = "done"


STAGE_LABELS = {
    Stage.BACKLOG: "Backlog",
    Stage.TODO: "To do",
    Stage.DOING: "Doing",
    Stage.REVIEW: "Review",
    Stage.DONE: "Done",
}


#: Ids this app generates: `daily-YYYY-MM-DD`, `weekly-YYYY-Www`, and
#: `transcript-<day>-<slug>` (produced by `transcribe.note_id`).
DAILY_ID_RE = re.compile(r"^daily-\d{4}-\d{2}-\d{2}$")
WEEKLY_ID_RE = re.compile(r"^weekly-\d{4}-W\d{2}$")
TRANSCRIPT_ID_RE = re.compile(r"^transcript-\d{4}-\d{2}-\d{2}-.+$")


def is_daily_note_id(note_id: Optional[str]) -> bool:
    """Whether an id belongs to a generated daily note.

    A daily note is a container the app writes, not a note you author, and two
    rules hang off that fact:

    - It is never counted as a day's finished work. Marking one done is natural
      (it *is* a note you finish), but the recap would then list the page you are
      reading as one of the day's accomplishments.
    - It is never treated as a recurrence parent. Making one recur daily reads as
      a parent, so the next instance is named `daily-2026-09-23-2026-09-24` and
      collects beside it -- one more every day, forever. The app already writes a
      note per day, so the only correct number of instances is none.
    """
    return bool(DAILY_ID_RE.match(str(note_id or "")))


def is_weekly_note_id(note_id: Optional[str]) -> bool:
    """Whether an id belongs to a generated weekly note. See `is_daily_note_id`."""
    return bool(WEEKLY_ID_RE.match(str(note_id or "")))


def is_transcript_note_id(note_id: Optional[str]) -> bool:
    """Whether an id belongs to a note written from a recording.

    A transcript is a record *of* something, not a thing to do, which is why it
    belongs to the same set as the period notes even though it summarises no
    period. Without this it is a plain note with no stage, so it lands in the
    board's first column as an unplaced card and sits there until it is
    dismissed -- a lecture a week would bury the board in a term.
    """
    return bool(TRANSCRIPT_ID_RE.match(str(note_id or "")))


def is_generated_note_id(note_id: Optional[str]) -> bool:
    """Whether an id is a note this program writes rather than one you author.

    Asked by everything that has to tell work apart from a record of work: a
    generated note must never be counted as something accomplished, and must
    never be read as a recurrence parent. One predicate so a new kind of
    generated note cannot be added on only some of those paths.
    """
    return (
        is_daily_note_id(note_id)
        or is_weekly_note_id(note_id)
        or is_transcript_note_id(note_id)
    )


#: The collection templates live in, here rather than in `templates.py` for the
#: same reason `iso_week` is: three features need to know whether a note is a
#: shape for other notes, and one answer is what keeps them agreeing.
TEMPLATES_COLLECTION = "templates"


def is_template(note: "Note") -> bool:
    """Whether a note is a template -- a shape rather than a thing."""
    return getattr(note, "collection", None) == TEMPLATES_COLLECTION


#: Collections whose notes are never work, whatever their ids look like.
#:
#: A `daily` note is a page you write on, a `weekly` one is a rollup of what you
#: finished, a `bookmarks` note is an address, a `workspaces` note is a folder, and
#: `testing` is a scratch pile. None of them is a thing to be *doing* -- but their
#: notes carry ordinary ids, so the id rule below cannot see them, and the board
#: filled with cards that could only be dismissed one at a time, forever. Named here
#: so the board, the dashboard and an unnarrowed query leave out one set between them.
NOT_WORK_COLLECTIONS = frozenset({"daily", "weekly", "bookmarks", "testing", "workspaces"})


def not_work_reason(note: "Note") -> str | None:
    """Why this note is not work -- `template`, `generated`, `collection` -- or None.

    One reason per note, deliberately: a template that also lives in `daily` is held
    back for being a template, and counting it twice would make the board's arithmetic
    disagree with the board. The order is the order of the questions -- a shape, then
    a record, then a place notes go.
    """
    if is_template(note):
        return "template"
    if is_generated_note_id(note.id):
        return "generated"
    if note.collection in NOT_WORK_COLLECTIONS:
        return "collection"
    return None


def is_work(note: "Note") -> bool:
    """Whether a note is a thing to be doing, rather than a shape or a record.

    A template is a shape for other notes, a period note is something the program
    wrote, and a bookmark or a workspace is a note *about* something rather than a
    thing to do -- so none of them is work. The board hides that set, the dashboard
    counts it, and a query that was not narrowed to a collection leaves it out: all
    three have to hide exactly the same thing, or the app contradicts itself about
    what you have left to do. Which is why they all ask *this* function, and why the
    board no longer spells the rule out for itself.
    """
    return not_work_reason(note) is None


def iso_week(day: date) -> str:
    """The ISO week label containing `day`, e.g. `2026-W39`.

    Shared vocabulary rather than one feature's private helper: the weekly
    rollup names its notes with it and templates label their output with it, and
    two implementations of ISO 8601 week numbering is one too many.
    """
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def iso_week_bounds(day: date) -> tuple[date, date]:
    """The Monday and Sunday of the ISO week containing `day`.

    Next to `iso_week` for the same reason: two features need the same week, and
    the way to keep them from disagreeing is for there to be one answer.
    """
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def stage_for_status(status: Status) -> Stage:
    """Which column a note belongs in when nobody has placed it by hand."""
    if status in (Status.COMPLETE, Status.IRRELEVANT):
        return Stage.DONE
    return Stage.TODO


def stamp_completed(
    previous: Optional[date],
    *,
    complete: bool,
    today: Optional[date] = None,
) -> Optional[date]:
    """The day a note was finished, maintained across edits.

    A completion date has to survive ordinary editing: re-saving a finished
    task, renaming it, or dragging its card around the board must not move the
    work to today. So an existing date is kept for as long as the note stays
    complete, and only a *fresh* completion -- after being reopened -- gets a
    new one. Reopening clears it, which is what makes that distinction possible.
    """
    if not complete:
        return None
    return previous or (today or date.today())


def reconcile(stage: Optional[str], status: Status) -> tuple[Optional[str], Status]:
    """Stop the board column and the BuJo status from contradicting each other.

    Called from every write path so both directions work: completing a task
    from the rapid log moves its card, and dropping a card in Done completes
    the task. Returns the pair to persist; `None` stage means "keep deriving
    it", which is how untouched notes stay clean on disk.
    """
    if stage == Stage.DONE.value and status not in (Status.COMPLETE, Status.IRRELEVANT):
        return stage, Status.COMPLETE
    if status in (Status.COMPLETE, Status.IRRELEVANT) and stage is not None and stage != Stage.DONE.value:
        return Stage.DONE.value, status
    return stage, status


#: A YAML value as something JSON can carry, without throwing anything away.
def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _coerce(enum_cls, value, default):
    """Enum lookup that falls back instead of raising.

    Foreign vaults may carry values we do not know (or none at all); a note
    we cannot classify is better than a note we refuse to open.
    """
    if value is None:
        return default
    try:
        return enum_cls(str(value))
    except ValueError:
        return default


#: Mood levels, best -> worst. This order is the colour ramp: the UI paints
#: index 0 as the brightest cell and the last as the darkest.
MOOD_LEVELS: tuple[str, ...] = ("great", "good", "meh", "low", "bad")

#: Numeric score per level, so a day's mood can be averaged or compared.
#: Deliberately not exposed as a raw number to the user — the UI shows words.
MOOD_SCORE: dict[str, int] = {
    level: len(MOOD_LEVELS) - i for i, level in enumerate(MOOD_LEVELS)
}

#: Pain is a 0-10 self-report, matching how pain is described clinically.
PAIN_MIN, PAIN_MAX = 0, 10


def normalize_mood(value: object) -> Optional[str]:
    """Return a known mood level, or None for anything else.

    A vault may have been written by hand, or by an older/foreign tool, so an
    unrecognised mood is not an error — it is simply not a level we can plot.
    Mapping it to None keeps junk out of the series instead of inventing a
    colour for it.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in MOOD_SCORE else None


def coerce_pain(value: object) -> Optional[int]:
    """Clamp a pain reading to 0-10, or None when there is nothing to read.

    Out-of-range values are clamped rather than rejected: a 12 on a bad day is
    a real thing someone types, and refusing to save it loses the entry.
    """
    if value is None or value == "":
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(PAIN_MIN, min(PAIN_MAX, number))


#: The frontmatter keys this model reads and writes. Anything else in a file belongs
#: to whoever put it there and is carried through untouched.
CLAIMED_KEYS = frozenset({
    "id", "collection", "title", "signifier", "status", "dates", "at", "until",
    "path", "url", "icon", "parent_id", "created", "mood", "pain", "tags",
    "recurrence", "stage", "blocked_by", "position", "completed", "aliases",
})


@dataclass(frozen=True)
class Note:
    id: str
    collection: str
    title: str
    body: str
    signifier: Signifier = Signifier.NOTE
    status: Status = Status.OPEN
    dates: list[date] = field(default_factory=list)
    #: Time of day, "HH:MM" in 24h, for a note that names an *instant* and not
    #: just a day -- an appointment, a class, a call. None means the note
    #: belongs to the whole of each of its dates, which is what every note was
    #: before this field existed, and is still what most notes are.
    at: Optional[str] = None
    #: When a timed note ends, "HH:MM". None means `schedule.DEFAULT_DURATION`.
    #: Kept as a clock time and not a duration so it reads the way a timetable
    #: does -- "14:30 until 16:00" -- and so a wrong end is visibly wrong
    #: rather than a number someone has to add up. An end at or before the
    #: start crosses midnight.
    until: Optional[str] = None
    #: A directory this note is *about* -- a code workspace, a project folder.
    #: The note is the home for that folder: what it holds, what is uncommitted,
    #: when it was last committed. Kept as the person wrote it (`~/...` and all)
    #: because the path is theirs to recognise, and expanded only when read.
    path: Optional[str] = None
    #: An address this note *is about* -- a service, a page, someone else's machine.
    #: A note with one is a bookmark. Kept exactly as written and never normalized:
    #: the person's own link is the truth, and whether a browser can open it is
    #: `app.bookmarks`' answer to give, not this model's to enforce. A typo must not
    #: cost you the note -- the same reasoning as an unreadable `at:`.
    url: Optional[str] = None
    #: A Lucide icon name (`server`, `book-open`, `heart-pulse`), drawn beside the
    #: note wherever a note is shown as a card. A name, not an image and not a
    #: character: the drawings are vendored (`static/vendor/lucide.js`), so the file
    #: stays portable text and the icon still renders with no network. An unknown
    #: name is kept and reported rather than refused -- a note is the person's file.
    icon: Optional[str] = None
    parent_id: Optional[str] = None
    created: date = field(default_factory=date.today)
    mood: Optional[str] = None
    #: 0-10 self-reported pain, or None when it was not logged. Kept alongside
    #: mood because neither one alone says much: a low mood with low pain and a
    #: low mood with high pain are different days.
    pain: Optional[int] = None
    tags: list[str] = field(default_factory=list)
    recurrence: Optional[str] = None  # "daily" | "weekly" | "monthly"
    # -- task management ----------------------------------------------------
    # Board column. None = derive from status (never explicitly placed).
    stage: Optional[str] = None
    # Task ids this note waits on. Stored in ONE direction only: the reverse
    # ("what does this block?") is derived, so the two views cannot drift.
    blocked_by: list[str] = field(default_factory=list)
    # Explicit order within its column. None sorts last, by creation date.
    position: Optional[float] = None
    #: The day this note was finished. Stamped when a note *becomes* complete,
    #: kept while it stays complete, and cleared when it is reopened so a task
    #: finished twice reports the second time. Only Status.COMPLETE stamps it:
    #: a struck-through note sits in the Done column but was abandoned, not
    #: completed, and counting it would make a summary of the day's work lie.
    #: Tasks completed before this field existed simply have no date -- a status
    #: flag cannot be turned back into a day, and guessing one from the file's
    #: mtime would attribute work to days it never happened on.
    completed: Optional[date] = None
    # Where this note lives in the vault, relative to the vault root. Set by
    # Vault on read so writes return to the same file. Never persisted to
    # frontmatter, and excluded from equality so round-trip tests are unaffected.
    source_rel: Optional[str] = field(default=None, compare=False, repr=False)
    #: Frontmatter this model does not own, exactly as it was written.
    #:
    #: A vault is hand-edited text, and a file may carry keys with no meaning here --
    #: a plugin's settings, a field from another app, a note to the future. They used
    #: to be dropped on the next save, which is the one thing a note-taking app must
    #: never do to a note. Kept verbatim and written back; excluded from equality so
    #: round-trip tests keep comparing the fields this model actually understands.
    #:
    #: It is a snapshot from the last read, not a live view: a key removed in an editor
    #: outside this app comes back on the next save unless the note is read again,
    #: which the app does whenever the vault changes on disk.
    frontmatter_extra: dict = field(default_factory=dict, compare=False, repr=False)

    def to_markdown(self) -> str:
        post = frontmatter.Post(self.body)
        # Keys this model does not own come first and are written back as they were
        # read; the app's own facts follow and win any collision, because those are
        # the fields it maintains. Nothing here deletes a key for being unfamiliar.
        post.metadata = {**self.frontmatter_extra, **{
            "id": self.id,
            "collection": self.collection,
            "title": self.title,
            "signifier": self.signifier.value,
            "status": self.status.value,
            "dates": [d.isoformat() for d in self.dates],
            # Normalized, and zero-padded on purpose: `14:30` written plain is
            # base-60 to a YAML loader, while `09:05`/`14:30` stay strings. The
            # app's own files must never be the ambiguous form.
            "at": parse_time(self.at),
            "until": parse_time(self.until),
            "path": self.path,
            "url": self.url,
            "icon": self.icon,
            "parent_id": self.parent_id,
            "created": self.created.isoformat(),
            "mood": self.mood,
            "pain": self.pain,
            "tags": list(self.tags),
            "recurrence": self.recurrence,
            "stage": self.stage,
            "blocked_by": list(self.blocked_by),
            "position": self.position,
            "completed": self.completed.isoformat() if self.completed else None,
        }}
        # Obsidian resolves [[Title]] by filename or alias, never by our
        # `title:` field, so record the title as an alias to make the same
        # wikilink work in both apps.
        if self.title:
            post.metadata["aliases"] = [self.title]
        return frontmatter.dumps(post)

    @classmethod
    def from_markdown(cls, md: str, *, fallback_id: str | None = None) -> "Note":
        """Parse a note. Tolerates arbitrary Obsidian files.

        A file in someone else's vault may have no frontmatter, or frontmatter
        missing any field we care about. `fallback_id` (normally the filename
        stem) supplies id and title when the frontmatter does not.
        """
        post = frontmatter.loads(md)
        meta = dict(post.metadata or {})
        body = post.content or ""
        stem = str(meta.get("id") or fallback_id or "untitled")

        fm_tags = split_frontmatter_tags(meta.get("tags"))
        tags = list(fm_tags)
        for tag in extract_inline_tags(body):
            if tag not in tags:
                tags.append(tag)

        dates: list[date] = []
        raw_dates = meta.get("dates") or []
        if isinstance(raw_dates, str):
            raw_dates = [raw_dates]
        for raw in raw_dates:
            try:
                dates.append(date.fromisoformat(str(raw)))
            except ValueError:
                continue

        # The raw frontmatter text first: YAML turns an unquoted `14:30` into
        # 870 and an unquoted `0930` into 930, two different times that cannot
        # be told apart once parsed. The text is the truth when we can read it.
        # A time we cannot read at all is dropped rather than rounded or
        # refused: a note with a typo in `at:` still opens, and `schedule` then
        # treats it as untimed. Scheduling the wrong hour would be worse than
        # not scheduling.
        at = parse_time(raw_time_from_frontmatter(md, "at") or meta.get("at"))
        until = parse_time(raw_time_from_frontmatter(md, "until") or meta.get("until"))
        # A path is not a time: YAML has one way to lose it (`~` alone is null,
        # which is how "no path" reads anyway) and no way to mangle it. Kept as
        # written, and str() because a path that parses as a number -- `path: 7`
        # -- arrives as an int and would otherwise be compared as one.
        path = meta.get("path")
        path = str(path) if path is not None else None
        # Same treatment as a path, and for the same reason: str() because a url that
        # happens to parse as a number arrives as an int, and comparing ints to urls is
        # how you get a link that silently is not one.
        url = meta.get("url")
        url = str(url) if url is not None else None
        # And again: `icon: 7` (a name that is only digits) arrives as an int, and an
        # icon name has to stay the text the person wrote or nothing can look it up.
        icon = meta.get("icon")
        icon = str(icon) if icon is not None else None

        try:
            created = date.fromisoformat(str(meta["created"])) if meta.get("created") else date.today()
        except ValueError:
            created = date.today()

        # A foreign vault may write deps as a lone id, a comma list, or a YAML
        # sequence; accept all three. Unknown column names are dropped rather
        # than raising — a note we cannot fully classify still opens.
        blocked_by: list[str] = []
        raw_deps = meta.get("blocked_by") or []
        if isinstance(raw_deps, str):
            raw_deps = raw_deps.replace(",", " ").split()
        for raw in raw_deps:
            dep = str(raw).strip()
            if dep and dep not in blocked_by:
                blocked_by.append(dep)

        stage = meta.get("stage")
        try:
            stage = Stage(str(stage)).value if stage else None
        except ValueError:
            stage = None

        try:
            position = float(meta["position"]) if meta.get("position") is not None else None
        except (TypeError, ValueError):
            position = None

        try:
            completed = date.fromisoformat(str(meta["completed"])) if meta.get("completed") else None
        except ValueError:
            completed = None

        return cls(
            frontmatter_extra={k: v for k, v in meta.items() if k not in CLAIMED_KEYS},
            id=stem,
            collection=str(meta.get("collection") or "inbox"),
            title=str(meta.get("title") or stem),
            body=body,
            signifier=_coerce(Signifier, meta.get("signifier"), Signifier.NOTE),
            status=_coerce(Status, meta.get("status"), Status.OPEN),
            dates=dates,
            at=at,
            until=until,
            path=path,
            url=url,
            icon=icon,
            parent_id=meta.get("parent_id"),
            created=created,
            mood=meta.get("mood"),
            pain=coerce_pain(meta.get("pain")),
            tags=tags,
            recurrence=meta.get("recurrence"),
            stage=stage,
            blocked_by=blocked_by,
            position=position,
            completed=completed,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        # Frontmatter this model does not own, made safe to send. YAML hands back
        # dates and nested maps, and a client cannot render a `datetime`; an unfamiliar
        # value becomes its own text rather than being dropped, because the reading view
        # exists to show what the file says.
        d["frontmatter_extra"] = {k: _json_safe(v) for k, v in self.frontmatter_extra.items()}
        d["signifier"] = self.signifier.value
        d["status"] = self.status.value
        # Always report a concrete column: a note nobody has placed on the
        # board still belongs in one, so the client never has to re-derive
        # this rule (and cannot get it subtly wrong).
        d["stage"] = self.stage or stage_for_status(self.status).value
        d["dates"] = [x.isoformat() for x in self.dates]
        # The normalized time, not the raw frontmatter: `9:05` in the file is
        # `09:05` everywhere the client sees it, and `label` is the display
        # spelling so no client has to reassemble a range.
        d["at"] = parse_time(self.at)
        d["until"] = parse_time(self.until)
        # The path as written. Whether it is a workspace, and what that workspace
        # holds, is `app.workspace`'s answer -- not this model's.
        d["path"] = self.path
        # The address as written. Whether a browser can open it, and what group it
        # belongs to, is `app.bookmarks`' answer.
        d["url"] = self.url
        # The icon name as written. Whether it names something Lucide can draw is
        # `app.note_icons`' answer, and the browser holds the same generated set, so
        # the two cannot disagree about it.
        d["icon"] = self.icon
        d["time_label"] = schedule_label(self)
        d["created"] = self.created.isoformat()
        # asdict() leaves these as date objects, which are not JSON.
        d["completed"] = self.completed.isoformat() if self.completed else None
        return d