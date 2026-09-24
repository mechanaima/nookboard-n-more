"""Weekly notes: the week's completed work, summarised in place.

The same operation as a daily note at a larger scale, which is why it reuses
`sections` instead of repeating the fence mechanics. A generated section inside
an ordinary note means regenerating it can never touch what was typed around it.

Weeks run Monday to Sunday, and a week's review is written on the Monday after
it ends: the week has to be over before it can be reviewed, and Sunday evening
is not over yet.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import daily, sections
from .models import Note, Signifier, Status, iso_week

#: HTML comments so the fences never render in Obsidian or in the app.
MARK_START = sections.mark("weekly", "start")
MARK_END = sections.mark("weekly", "end")

#: How many weeks back a missed review is still written. Longer than the daily
#: window because weeks are rarer: a fortnight off is an ordinary gap and losing
#: a whole week's rollup to it would be a poor trade.
CATCHUP_WEEKS = 4

#: One review per tick. A weekly note is a bigger model call than a daily one and
#: a backlog this size is rare, so there is nothing to gain by batching them.
MAX_CATCHUP_RUNS = 1


def note_id(key: str) -> str:
    """The note id for an ISO week key. Derived from the key, so it cannot drift.

    `weekly-`, to agree with `models.WEEKLY_ID_RE` and with `daily-YYYY-MM-DD`.
    It is the regex that decides whether a note is a generated container, so an
    id that does not match it is a note that boards itself, counts itself as the
    week's work, and can be read as a recurrence parent -- all the traps the
    daily note fell into. Deriving the id here does not make it agree with the
    regex; only these two strings agreeing does.
    """
    return f"weekly-{key}"


def week_key(day: date) -> str:
    """The ISO week label containing `day`, e.g. `2026-W39`."""
    return iso_week(day)


def week_bounds(key: str) -> tuple[date, date]:
    """The Monday and Sunday an ISO week key covers."""
    year, week = key.split("-W")
    monday = date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + timedelta(days=6)


def finished_in(notes: list[Note], key: str) -> dict[date, list[Note]]:
    """What was finished each day of the week, days with nothing omitted.

    Reuses the daily rule for what counts as finished -- complete, not abandoned,
    and not a generated note -- so a week cannot disagree with the seven days
    inside it about what got done.
    """
    monday, sunday = week_bounds(key)
    out: dict[date, list[Note]] = {}
    day = monday
    while day <= sunday:
        done = daily.completed_on(notes, day)
        if done:
            out[day] = done
        day += timedelta(days=1)
    return out


def felt_line(summary: dict | None) -> str | None:
    """One line for how the week felt, from the mood layer's own numbers.

    None rather than a zero-filled line when nothing was logged: an unlogged
    week should leave the rollup saying nothing, not reporting an average of
    nothing.
    """
    if not summary or not summary.get("days_logged"):
        return None
    bits = [f"Logged {summary['days_logged']} of 7 days"]
    if summary.get("avg_mood"):
        bits.append(f"average mood {summary['avg_mood']}")
    if summary.get("avg_pain") is not None:
        bits.append(f"average pain {summary['avg_pain']}/10")
    return " \u00b7 ".join(bits) + "."


def render(
    key: str,
    done_by_day: dict[date, list[Note]],
    recap: str | None,
    felt: str | None,
    insight_reading: str | None = None,
) -> str:
    """The generated section for a week.

    `recap` and `insight_reading` are the parts that may be missing; the list of
    finished work is the part that is always there.
    """
    monday, sunday = week_bounds(key)
    span = f"{monday.day} {monday.strftime('%b')} \u2013 {sunday.day} {sunday.strftime('%b %Y')}"
    lines = [MARK_START, "", f"## Week {key} \u00b7 {span}", ""]
    if recap:
        lines += [recap.strip(), ""]

    if done_by_day:
        lines += ["### Finished", ""]
        for day in sorted(done_by_day):
            titles = ", ".join(f"[[{n.title}]]" for n in done_by_day[day])
            lines.append(f"- **{day.strftime('%a')} {day.day}** \u2014 {titles}")
        lines.append("")
    else:
        lines += ["_Nothing was marked done._", ""]

    if felt:
        lines += ["### The week", "", felt, ""]
    if insight_reading:
        lines += [f"_{insight_reading}_", ""]

    lines += [MARK_END]
    return "\n".join(lines)


def upsert_section(body: str, section: str) -> str:
    """Put the week's section into `body`, replacing any previous one."""
    return sections.upsert(body, section, start=MARK_START, end=MARK_END)


def has_summary(body: str) -> bool:
    """Whether a note already carries a generated section."""
    return MARK_START in (body or "")


def note_for(
    key: str,
    done_by_day: dict[date, list[Note]],
    recap: str | None,
    felt: str | None,
    *,
    insight_reading: str | None = None,
    created: date | None = None,
) -> Note:
    """A weekly note. Dated the Sunday the week ended.

    One date rather than all seven: the note is a record *of* the week, and
    giving it every day would print it seven times across the timeline and
    inflate each day's calendar count.
    """
    _, sunday = week_bounds(key)
    return Note(
        id=note_id(key),
        collection="weekly",
        title=key,
        body=upsert_section(
            "", render(key, done_by_day, recap, felt, insight_reading)
        ),
        signifier=Signifier.NOTE,
        status=Status.OPEN,
        dates=[sunday],
        created=created or sunday,
    )


def due_weeks(
    notes: list[Note],
    *,
    today: date,
    hour: int,
    now: datetime,
    blocked: set[str] | None = None,
    weeks: int = CATCHUP_WEEKS,
    max_runs: int = MAX_CATCHUP_RUNS,
) -> list[str]:
    """Weeks owed a review, newest first, at most `max_runs` of them.

    A week is owed once it has ended *and* the cutoff on the following Monday has
    passed. Both halves matter: reviewing on Sunday evening would review a week
    that is not over, and requiring only that the week ended would fire at 00:01
    on Monday, hours before the machine's model is likely to be up.
    """
    blocked = blocked or set()
    owed: list[str] = []
    monday_this_week = today - timedelta(days=today.weekday())
    for back in range(1, weeks + 1):
        monday = monday_this_week - timedelta(days=7 * back)
        key = week_key(monday)
        if key in blocked:
            continue
        writes_on = monday + timedelta(days=7)
        if writes_on > today:
            continue
        # Same cutoff rule as the daily run, borrowed rather than reimplemented
        # so the two can never drift apart about what "after the cutoff" means.
        if writes_on == today and not daily.is_due(now, hour):
            continue
        if not finished_in(notes, key):
            # Never invent a note for a week with nothing in it.
            continue
        owed.append(key)
    return owed[:max_runs]
