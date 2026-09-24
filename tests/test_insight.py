"""Pain vs output: the coefficient, the bands, and the refusals.

Most of these are about what the module declines to claim. A correlation from
five days is a story about five days, and the value of this feature is that it
will not dress that up as a statement about a body.
"""

from datetime import date, timedelta

import pytest

from app import insight
from app.models import Note, Signifier, Status

TODAY = date(2026, 9, 23)


def _logged(nid: str, day: date, *, pain=None, mood=None) -> Note:
    return Note(
        id=nid,
        collection="inbox",
        title=nid,
        body="",
        signifier=Signifier.NOTE,
        status=Status.OPEN,
        dates=[day],
        mood=mood,
        pain=pain,
    )


def _done(nid: str, day: date) -> Note:
    return Note(
        id=nid,
        collection="inbox",
        title=nid,
        body="",
        signifier=Signifier.TASK,
        status=Status.COMPLETE,
        dates=[day],
        completed=day,
    )


# --- the coefficient ------------------------------------------------------

def test_spearman_agrees_perfectly_with_a_perfect_agreement():
    assert insight.spearman([(1, 1), (2, 2), (3, 3)]) == pytest.approx(1.0)


def test_spearman_is_minus_one_when_the_columns_reverse():
    assert insight.spearman([(1, 3), (2, 2), (3, 1)]) == pytest.approx(-1.0)


def test_ranks_share_the_place_among_ties():
    """The tie behaviour on its own, rather than through a coefficient.

    A coefficient would also pass if ties were ranked by position, which is
    exactly the bug worth catching: pain is logged on a 0-10 scale, so a
    fortnight of 7s is ordinary and ordering those by appearance would invent a
    sequence the data does not have.
    """
    assert insight._ranks([1, 2, 2, 4]) == [1.0, 2.5, 2.5, 4.0]
    assert insight._ranks([5, 5, 5]) == [2.0, 2.0, 2.0]


def test_spearman_with_ties_matches_a_hand_computed_rank_correlation():
    # Ranks x [1,2,3,4] and y [1,2.5,2.5,4]: sums of squared deviations 5.0 and
    # 4.5, numerator 4.5, so 4.5 / (sqrt(5) * sqrt(4.5)).
    assert insight.spearman([(1, 1), (2, 2), (3, 2), (4, 4)]) == pytest.approx(
        0.94868329805, abs=1e-9
    )


def test_spearman_is_undefined_when_a_column_never_varies():
    """Every day at the same pain is no relationship, not a perfect one."""
    assert insight.spearman([(5, 1), (5, 2), (5, 3)]) is None


def test_spearman_needs_two_points_to_mean_anything():
    assert insight.spearman([]) is None
    assert insight.spearman([(3, 1)]) is None


# --- the pairs ------------------------------------------------------------

def test_days_before_the_first_stamp_are_excluded():
    """The artefact this guard exists to prevent.

    Completion dates only exist from the day the field was introduced. Reading
    the logged days before that as "nothing finished" would line up old
    high-pain days against zero output and manufacture a relationship out of
    when the feature shipped.
    """
    old = TODAY - timedelta(days=20)
    notes = [
        _logged("old", old, pain=9),
        _done("t1", TODAY),
        _logged("d1", TODAY, pain=3),
    ]
    found = insight.samples(notes)
    assert found["since"] == TODAY.isoformat()
    assert found["pairs"] == [(3, 1)]
    assert old.isoformat() in found["skipped"]


def test_a_logged_day_with_nothing_finished_is_still_a_pair():
    """Otherwise every day would be filtered down to the productive ones."""
    notes = [_done("t1", TODAY - timedelta(days=1)), _logged("d1", TODAY, pain=8)]
    assert insight.samples(notes)["pairs"] == [(8, 0)]


def test_a_mood_only_day_has_no_pain_to_compare():
    notes = [
        _done("t1", TODAY - timedelta(days=1)),
        _logged("d1", TODAY, mood="good"),
    ]
    assert insight.samples(notes)["pairs"] == []


def test_without_any_stamp_there_is_nothing_to_pair():
    found = insight.samples([_logged("d1", TODAY, pain=5)])
    assert found["pairs"] == []
    assert found["since"] is None


# --- the bands ------------------------------------------------------------

def test_bands_report_the_mean_work_per_pain_band():
    rows = insight.band_summary([(1, 4), (2, 4), (7, 1), (9, 0)])
    assert [(r["band"], r["days"], r["mean_completed"]) for r in rows] == [
        ("mild", 2, 4.0),
        ("high", 1, 1.0),
        ("severe", 1, 0.0),
    ]


def test_an_unused_pain_band_is_omitted_rather_than_shown_empty():
    rows = insight.band_summary([(1, 2)])
    assert [r["band"] for r in rows] == ["mild"]


# --- the refusals ---------------------------------------------------------

def _ten_days_where_pain_falls_as_output_rises() -> list[Note]:
    notes = []
    for i in range(10):
        day = TODAY - timedelta(days=9 - i)
        notes.append(_logged(f"d{i}", day, pain=9 - i))
        for k in range(i):
            notes.append(_done(f"t{i}-{k}", day))
    return notes


def test_no_correlation_is_claimed_below_the_minimum():
    notes = [_done(f"t{i}", TODAY - timedelta(days=10 - i)) for i in range(3)]
    notes += [_logged(f"d{i}", TODAY - timedelta(days=i), pain=i * 3) for i in range(3)]

    got = insight.pain_vs_output(notes, min_days=8)
    assert got["days_paired"] == 3
    assert got["rho"] is None, "a coefficient from 3 days must not be reported"
    assert got["reading"]["strength"] == "unknown"
    assert "Not enough" in got["reading"]["text"]
    # The bands still say something, because they need no statistics.
    assert got["bands"]


def test_more_work_on_lower_pain_days_is_described_that_way():
    """The sentence is the whole feature, so assert what it *means*.

    A negative coefficient means output falls as pain rises -- so the work
    happens on the lower-pain days. Stating it the other way round still reads
    as fluent English and would quietly tell the reader the opposite of their
    own data.
    """
    got = insight.pain_vs_output(_ten_days_where_pain_falls_as_output_rises())
    assert got["rho"] == pytest.approx(-1.0)
    assert got["reading"]["direction"] == "negative"
    assert got["reading"]["strength"] == "strong"
    assert "more gets finished on lower-pain days" in got["reading"]["text"]


def test_more_work_on_higher_pain_days_is_described_that_way():
    """The mirror case, so a swapped comparison cannot pass both tests."""
    notes = []
    for i in range(10):
        day = TODAY - timedelta(days=9 - i)
        notes.append(_logged(f"d{i}", day, pain=i))
        for k in range(i):
            notes.append(_done(f"t{i}-{k}", day))
    got = insight.pain_vs_output(notes)
    assert got["rho"] == pytest.approx(1.0)
    assert "more gets finished on higher-pain days" in got["reading"]["text"]


def test_the_caveat_refuses_to_claim_cause():
    """Association is all this can ever be, and the payload says so."""
    caveat = insight.pain_vs_output([])["caveat"]
    assert "not a cause" in caveat
    assert "unlogged" in caveat


def test_no_stamps_at_all_still_answers_without_guessing():
    got = insight.pain_vs_output([_logged("d1", TODAY, pain=5)])
    assert got["days_paired"] == 0
    assert got["since"] is None
    assert got["rho"] is None
    assert got["reading"]["strength"] == "unknown"
    assert got["bands"] == []
