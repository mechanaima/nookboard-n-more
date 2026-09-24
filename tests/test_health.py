"""What a check means, with no network anywhere near it.

The runner opens sockets; this is the half that decides what a socket's answer
means, and it is pure so that every judgment can be examined directly.
"""

from __future__ import annotations

import pytest

from app import health as H


@pytest.mark.parametrize("status,expected", [(200, H.UP), (204, H.UP), (301, H.UP), (302, H.UP)])
def test_a_success_is_up(status, expected):
    assert H.kind_of(status, None) == expected


@pytest.mark.parametrize("status", [400, 401, 404, 418, 500, 503])
def test_an_answer_that_is_not_a_success_is_still_an_answer(status):
    """Reachable is not the same as working, and the difference is the number."""
    assert H.kind_of(status, None) == H.ANSWERED
    assert str(status) in H.words(H.ANSWERED, status)


def test_silence_is_down_and_the_error_is_why():
    assert H.kind_of(None, "nothing is listening on that port") == H.DOWN
    assert H.words(H.DOWN) == "no answer"


def test_nothing_at_all_is_not_up():
    """The trap this whole module exists to avoid: an empty result read as healthy."""
    assert H.kind_of(None, None) == H.UNKNOWN
    assert H.words(H.UNKNOWN) == "not checked"


def test_up_is_the_only_word_that_claims_something_works():
    assert H.words(H.UP, 200) == "answering"
    for kind in (H.ANSWERED, H.DOWN, H.UNKNOWN):
        assert "answering" not in H.words(kind, 500)


def test_why_is_detail_beside_the_chip_not_instead_of_it():
    assert H.why(None, "nothing is listening on that port") == "nothing is listening on that port"
    assert H.why(200, None) is None


def test_a_missing_page_is_not_the_same_fact_as_a_broken_service():
    """A llama.cpp server answers `/` with 404 because it serves `/completion`.

    Both are "answered, not with a success", and only one of them means something is
    wrong: saying "unhappy" for the 404 is the app diagnosing what it did not look at.
    """
    missing = H.why(404, None)
    broken = H.why(503, None)
    assert missing != broken
    assert "that path is not" in missing and "unhappy" not in missing
    assert "unhappy" in broken
    assert H.why(204, None) is None


def test_a_result_carries_when_it_was_taken():
    """A status without a timestamp is a claim about the past dressed as the present."""
    res = H.result(status=200, ms=12.4, at="2026-09-24T21:50:00+00:00")
    assert res["at"] == "2026-09-24T21:50:00+00:00"
    assert res["ms"] == 12
    assert res["words"] == "answering" and res["why"] is None


def test_a_result_with_no_time_says_so_rather_than_inventing_one():
    assert H.unanswered()["at"] is None
    assert H.unanswered()["kind"] == H.UNKNOWN


def test_only_an_address_a_browser_could_open_is_worth_asking_about():
    assert H.is_checkable("http://127.0.0.1:8765")
    assert H.is_checkable("https://braxia.tel")
    # a typo has no host to reach: asking would make one mistake into two complaints
    assert not H.is_checkable("127.0.0.1:8765")
    assert not H.is_checkable("file:///etc/hosts")
