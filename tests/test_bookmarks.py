"""The rules a bookmark follows.

Pure: no vault, no HTTP. What is a bookmark, what a group is, what a browser
would refuse, and the one thing this feature exists *not* to do -- touch the
network.
"""

from __future__ import annotations

import pytest

from app import bookmarks as B
from app.models import Note


def note(nid="b1", title="Nookboard", url="http://127.0.0.1:8765", tags=(), body=""):
    return Note(id=nid, collection="bookmarks", title=title, body=body, url=url,
                tags=list(tags))


# -- what a bookmark is -------------------------------------------------------

def test_a_note_with_an_address_is_one_and_without_is_not():
    assert B.is_bookmark(note())
    assert not B.is_bookmark(note(url=None))
    assert not B.is_bookmark(note(url=""))
    # Whitespace is not an address: an address row cleared and saved as " " is empty.
    assert not B.is_bookmark(note(url="   "))
    # And what is stored is what was typed, without the padding.
    assert B.url_of(note(url="  http://x.test/a?b=1  ")) == "http://x.test/a?b=1"


def test_a_note_that_is_not_a_bookmark_is_left_out_of_the_view_entirely():
    payload = B.view([note(nid="yes"), note(nid="no", title="Plain", url=None)])
    assert payload["count"] == 1
    assert [i["id"] for g in payload["groups"] for i in g["items"]] == ["yes"]


# -- what a browser cannot open ----------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "127.0.0.1:8765",           # no scheme at all: the commonest typo
        "localhost:8137",
        "example.com",
        "javascript:alert(1)",      # a note is the person's file, so this is possible
        "file:///home/irving/x",
        "ftp://example.com/pub",
        "http://",                  # a scheme with nothing after it
        "https://",
    ],
)
def test_an_address_a_browser_would_not_open_is_refused(url):
    assert B.url_problem(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765",
        "https://braxia.tel",
        "http://ghost.braxia.tel/",
        "https://example.com/a/b?c=d#e",
        "HTTP://EXAMPLE.COM",       # scheme case is not the person's mistake
    ],
)
def test_an_address_a_browser_would_open_is_not(url):
    assert B.url_problem(url) is None


def test_the_reason_is_a_sentence_someone_can_act_on():
    """`no scheme` is only useful to someone who already knows what a scheme is.

    The check is on shape -- several words, no code-speak -- because that is the
    property; the first attempt asserted `.islower()` and failed on a typographic
    quote, which is a fact about quotation marks, not about sentences.
    """
    for url in ("127.0.0.1:8765", "javascript:alert(1)", "http://"):
        why = B.url_problem(url)
        assert why
        assert " " in why and "_" not in why
        assert why.lstrip("\u201c(")[:1].islower()
    # The commonest mistake names the thing you would have to type.
    assert "https://" in B.url_problem("127.0.0.1:8765")


def test_host_is_what_tells_two_bookmarks_apart():
    assert B.host_of("http://127.0.0.1:8796/x") == "127.0.0.1:8796"
    assert B.host_of("https://braxia.tel") == "braxia.tel"
    assert B.host_of("") == ""


# -- grouping -----------------------------------------------------------------

def test_a_group_is_the_first_tag_and_untagged_goes_to_other():
    payload = B.view([
        note(nid="a", title="A", tags=["services"]),
        note(nid="b", title="B", tags=["services", "extra"]),
        note(nid="c", title="C", tags=[]),
    ])
    assert [g["name"] for g in payload["groups"]] == ["services", B.OTHER]


def test_other_comes_last_even_when_its_name_sorts_first():
    """`Other` is the absence of a decision, not a decision that sorts early."""
    payload = B.view([
        note(nid="c", title="C", tags=[]),
        note(nid="a", title="A", tags=["zebra"]),
    ])
    assert [g["name"] for g in payload["groups"]] == ["zebra", B.OTHER]


def test_groups_and_items_are_ordered_here_so_the_browser_never_sorts():
    payload = B.view([
        note(nid="2", title="Zebra", tags=["tools"]),
        note(nid="1", title="apple", tags=["tools"]),
        note(nid="3", title="Beta", tags=["services"]),
    ])
    assert [g["name"] for g in payload["groups"]] == ["services", "tools"]
    tools = next(g for g in payload["groups"] if g["name"] == "tools")
    # casefold, not the tuple's own order: `Zebra` must not sort above `apple`
    assert [i["title"] for i in tools["items"]] == ["apple", "Zebra"]


# -- the unopenable, which is shown rather than dropped -----------------------

def test_an_address_that_cannot_be_opened_is_shown_with_its_reason():
    payload = B.view([note(nid="bad", title="Typo", url="127.0.0.1:8765")])
    assert payload["count"] == 1
    assert payload["unusable"] == 1
    group = payload["groups"][-1]
    assert group["name"] == B.UNUSABLE
    assert group["problem"] is True
    assert group["items"][0]["problem"]


def test_the_unusable_group_is_not_counted_as_a_bookmark_that_works():
    """The line above the list says one number and its noise, not two numbers."""
    payload = B.view([note(nid="ok"), note(nid="bad", url="nope")])
    assert payload["count"] == 2
    assert payload["unusable"] == 1
    assert payload["count"] - payload["unusable"] == 1


def test_a_bookmark_carries_its_own_note_because_that_is_usually_the_reason():
    payload = B.view([note(nid="a", body="  the vault itself  ", tags=["services"])])
    item = payload["groups"][0]["items"][0]
    assert item["body"] == "the vault itself"


def test_nothing_bookmarked_is_an_empty_view_not_a_broken_one():
    payload = B.view([])
    assert payload["groups"] == [] and payload["count"] == 0 and payload["unusable"] == 0
