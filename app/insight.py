"""What the vault knows but no single feature does.

Pain is logged on the days it is felt and tasks are finished on the days there
is capacity for them. Both are written into the same Markdown files by the same
app, and until now nothing has ever looked at them together — which makes this
the one question a notes app cannot answer for you and this one can: does the
work happen on the good days?

The answer is usually murkier than the question, so most of this module is about
refusing to say things the data does not support. A correlation drawn from five
days is a story about five days, and telling it as a finding about a body would
be worse than saying nothing.
"""

from __future__ import annotations

from datetime import date
from statistics import mean
from typing import Iterable, Optional, Sequence

from . import daily, mood
from .models import Note

#: Days needed before a relationship is stated at all. Below this the
#: coefficient swings wildly on a single unusual day, and "not yet" is the
#: honest reading — better than dressing noise up as a finding.
MIN_DAYS = 8

#: Pain bands, as (low, high, label). Severity, not score.
BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 2, "mild"),
    (3, 5, "moderate"),
    (6, 8, "high"),
    (9, 10, "severe"),
)

#: |rho| thresholds, weakest first, with the word for that strength.
STRENGTHS: tuple[tuple[float, str], ...] = (
    (0.2, "none"),
    (0.4, "weak"),
    (0.6, "moderate"),
    (1.01, "strong"),
)


def _ranks(values: Sequence[float]) -> list[float]:
    """Competition ranks, ties sharing the average of the places they occupy.

    Ties are the normal case rather than a technicality: pain is logged on a
    0-10 scale, so a fortnight of 7s is ordinary. Ranking those by position
    would invent an ordering the data does not have.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(pairs: Sequence[tuple[float, float]]) -> Optional[float]:
    """Rank correlation between the two columns, or None if undefined.

    Rank rather than Pearson: the question is whether worse days mean less
    output, which is about order, not a straight line. Pain 3 to 4 need not be
    the same step as 7 to 8, and nothing here assumes it is.

    Returns None when a column never varies — every day logged at the same pain
    is no relationship to measure, not a perfect one.
    """
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (
        sum((a - mx) ** 2 for a in rx) ** 0.5 * sum((b - my) ** 2 for b in ry) ** 0.5
    )
    if den == 0:
        return None
    return num / den


def samples(notes: Iterable[Note]) -> dict:
    """Pair each usable day's pain with how much was finished on it.

    Only days from the first stamped completion onward are paired. Before that
    date the vault holds no completion record at all, so reading those days as
    "nothing finished" would manufacture precisely the correlation being looked
    for — high pain reported while nothing could be stamped, then a run of
    stamped days, and a relationship that is an artefact of when the field was
    introduced.
    """
    notes = list(notes)
    stamped = [n.completed for n in notes if n.completed]
    if not stamped:
        return {"pairs": [], "since": None, "skipped": []}

    since = min(stamped)
    pairs: list[tuple[int, int]] = []
    skipped: list[str] = []
    for record in mood.daily_series(notes):
        day = date.fromisoformat(record["date"])
        if day < since or record["pain"] is None:
            # Either the day predates the record of finishing things, or it
            # carries a mood with no pain reading to compare against.
            skipped.append(record["date"])
            continue
        pairs.append((record["pain"], len(daily.completed_on(notes, day))))
    return {"pairs": pairs, "since": since.isoformat(), "skipped": skipped}


def band_summary(pairs: Sequence[tuple[int, int]]) -> list[dict]:
    """Mean finished-work per pain band. The part that needs no statistics.

    This is what can be said with confidence at any sample size: on days logged
    at this pain, this much got finished.
    """
    rows = []
    for low, high, label in BANDS:
        inside = [done for pain, done in pairs if low <= pain <= high]
        if not inside:
            continue
        rows.append(
            {
                "band": label,
                "low": low,
                "high": high,
                "days": len(inside),
                "mean_completed": round(mean(inside), 1),
                "total_completed": sum(inside),
            }
        )
    return rows


def reading(rho: Optional[float], n: int, *, min_days: int = MIN_DAYS) -> dict:
    """Plain words for the number, including the words for "no number"."""
    if n < min_days:
        return {
            "strength": "unknown",
            "direction": None,
            "text": (
                f"Not enough to go on yet: {n} day{'' if n == 1 else 's'} logged "
                f"with both pain and finished work. Around {min_days} is where a "
                "pattern starts to mean anything."
            ),
        }
    if rho is None:
        return {
            "strength": "unknown",
            "direction": None,
            "text": (
                f"Pain has not varied across these {n} days, so there is nothing "
                "to compare it against yet."
            ),
        }

    strength = next(word for limit, word in STRENGTHS if abs(rho) < limit)
    if strength == "none":
        text = (
            f"Across {n} days, pain and finished work move independently — "
            "worse days have not meant less done."
        )
    else:
        # A negative coefficient means output falls as pain rises, so the work
        # is being done on the *lower*-pain days. Getting this backwards
        # produces a fluent sentence that states the opposite of the finding,
        # which is worse than showing nothing at all.
        direction = "lower-pain" if rho < 0 else "higher-pain"
        text = (
            f"Across {n} days, more gets finished on {direction} days "
            f"({strength} relationship)."
        )
    return {
        "strength": strength,
        "direction": "negative" if rho < 0 else "positive",
        "text": text,
    }


def pain_vs_output(notes: Iterable[Note], *, min_days: int = MIN_DAYS) -> dict:
    """The whole picture: the pairs, the bands, the coefficient, and the caveat."""
    notes = list(notes)
    found = samples(notes)
    pairs = found["pairs"]
    rho = spearman(pairs) if len(pairs) >= min_days else None
    return {
        "days_paired": len(pairs),
        "min_days": min_days,
        "since": found["since"],
        "days_excluded": len(found["skipped"]),
        "rho": None if rho is None else round(rho, 3),
        "bands": band_summary(pairs),
        "reading": reading(rho, len(pairs), min_days=min_days),
        # Stated in the payload rather than left to the UI so it cannot be
        # dropped for looking untidy: this is association, and a body is not a
        # scatter plot.
        "caveat": (
            "This is a pattern in what was written down, not a cause. It cannot "
            "tell you whether pain reduced the work or the work worsened the "
            "pain, and it says nothing about days that went unlogged."
        ),
    }
