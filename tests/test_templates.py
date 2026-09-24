"""Templates: what a template may say, and what applying one produces."""

from datetime import date, datetime

import pytest

from app import templates
from app.models import Note, Signifier, Status

DAY = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 9, 5)


def _template(nid="tpl-journal", title="Journal", body="", **kw):
    return Note(
        id=nid,
        collection=templates.TEMPLATES_COLLECTION,
        title=title,
        body=body,
        **kw,
    )


# --- what counts as a template -------------------------------------------

def test_a_note_in_the_templates_collection_is_a_template():
    assert templates.is_template(_template())
    assert not templates.is_template(Note(id="a", collection="inbox", title="a", body=""))


def test_templates_are_listed_in_a_stable_order():
    notes = [
        _template("t-b", "Beta"),
        _template("t-a", "alpha"),
        _template("t-c", "Beta"),
        Note(id="x", collection="journal", title="Not a template", body=""),
    ]
    got = [(n.id, n.title) for n in templates.list_templates(notes)]
    # case-insensitive by title, id breaking the tie, so the list never reshuffles
    assert got == [("t-a", "alpha"), ("t-b", "Beta"), ("t-c", "Beta")]


# --- placeholders ---------------------------------------------------------

def test_the_four_placeholders_expand():
    out = templates.expand(
        "{{date}} {{time}} {{week}} {{title}}", day=DAY, now=NOW, title="Soup"
    )
    assert out == "2026-09-24 09:05 2026-W39 Soup"


def test_unknown_placeholders_are_left_exactly_as_written():
    """Not eaten, not guessed at -- the mistake stays visible in the note."""
    src = "{* stuff *} {{stuff}} {{DATE_TYPO}} {{}}"
    assert templates.expand(src, day=DAY, now=NOW) == src


def test_a_title_placeholder_needs_a_title():
    # In a template's title field there is no title yet, so it stays literal
    # rather than expanding to something made up.
    assert templates.expand("{{title}}", day=DAY, now=NOW) == "{{title}}"
    assert templates.expand("{{title}}", day=DAY, now=NOW, title="Real") == "Real"


def test_a_template_title_may_still_be_dated():
    """The journal case: a title that labels itself by the day."""
    assert templates.expand("Journal {{date}}", day=DAY, now=NOW) == "Journal 2026-09-24"


def test_spacing_inside_the_braces_is_forgiving():
    assert templates.expand("{{ date }}", day=DAY, now=NOW) == "2026-09-24"


def test_expanding_nothing_is_not_an_error():
    assert templates.expand("", day=DAY, now=NOW) == ""
    assert templates.expand(None, day=DAY, now=NOW) == ""


# --- titles ---------------------------------------------------------------

def test_a_free_title_is_kept_as_it_is():
    assert templates.unique_title("Soup", ["Stew"]) == "Soup"


def test_a_taken_title_gets_a_number():
    assert templates.unique_title("Soup", ["Soup"]) == "Soup (2)"
    assert templates.unique_title("Soup", ["Soup", "Soup (2)"]) == "Soup (3)"


def test_title_clashes_ignore_case():
    """`Soup` and `soup` resolve to each other as wikilinks, so they clash."""
    assert templates.unique_title("Soup", ["soup"]) == "Soup (2)"


# --- applying one ---------------------------------------------------------

def test_the_template_becomes_a_note():
    tpl = _template(
        title="Journal {{date}}",
        body="# {{title}}\n\nMood:\nPain:\n",
        tags=["journal", "daily"],
    )
    note = templates.build_note(tpl, note_id="n1", day=DAY, now=NOW)

    assert note.id == "n1"
    assert note.title == "Journal 2026-09-24"
    # the body's {{title}} resolves to the title the note actually ended up with
    assert note.body == "# Journal 2026-09-24\n\nMood:\nPain:\n"
    assert note.tags == ["journal", "daily"]
    assert note.dates == [DAY]
    assert note.created == DAY
    assert note.status is Status.OPEN
    assert note.collection == "inbox"


def test_a_title_you_give_it_wins_over_the_template_s():
    tpl = _template(title="Journal {{date}}", body="{{title}}")
    note = templates.build_note(tpl, note_id="n1", title="Soup night", day=DAY, now=NOW)
    assert note.title == "Soup night"
    assert note.body == "Soup night"


def test_the_template_s_signifier_carries_so_a_task_template_makes_a_task():
    tpl = _template(title="Weekly shop", body="- [ ] milk\n", signifier=Signifier.TASK)
    note = templates.build_note(tpl, note_id="n1", day=DAY, now=NOW)
    assert note.signifier is Signifier.TASK


def test_a_new_note_starts_open_even_if_the_template_was_finished():
    """State is not inherited: a template left marked done still makes open work."""
    tpl = _template(title="Chore", status=Status.COMPLETE, completed=DAY)
    note = templates.build_note(tpl, note_id="n1", day=DAY, now=NOW)
    assert note.status is Status.OPEN
    assert note.completed is None


def test_applying_a_template_twice_does_not_make_twin_titles():
    tpl = _template(title="Journal {{date}}")
    first = templates.build_note(tpl, note_id="n1", day=DAY, now=NOW)
    second = templates.build_note(tpl, note_id="n2", day=DAY, now=NOW, taken=[first.title])
    assert first.title == "Journal 2026-09-24"
    assert second.title == "Journal 2026-09-24 (2)"


def test_a_template_with_no_usable_title_still_makes_something_named():
    note = templates.build_note(_template(title=""), note_id="n1", day=DAY, now=NOW)
    assert note.title == "Untitled 2026-09-24"


def test_the_collection_asked_for_is_the_collection_used():
    tpl = _template()
    note = templates.build_note(tpl, note_id="n1", collection="journal", day=DAY, now=NOW)
    assert note.collection == "journal"


def test_an_empty_collection_falls_back_rather_than_writing_nowhere():
    tpl = _template()
    note = templates.build_note(tpl, note_id="n1", collection="", day=DAY, now=NOW)
    assert note.collection == "inbox"


# --- through the API ------------------------------------------------------

def _journal_template(seed_note, nid="tpl-journal", title="Journal {{date}}"):
    return seed_note(
        nid,
        title=title,
        body="# {{title}}\n\nMood:\nPain:\n",
        collection=templates.TEMPLATES_COLLECTION,
        tags=("journal",),
    )


def test_the_api_lists_the_templates_and_what_they_use(client_factory, seed_note):
    _journal_template(seed_note)
    seed_note("a-note", title="Just a note", body="{{date}}", collection="journal")
    c = client_factory()

    got = c.get("/api/templates").json()
    assert got["collection"] == "templates"
    assert [t["title"] for t in got["templates"]] == ["Journal {{date}}"]
    # the placeholders a template actually uses, not the whole vocabulary
    assert got["templates"][0]["placeholders"] == ["date", "title"]


def test_applying_a_template_makes_a_real_note(client_factory, seed_note):
    _journal_template(seed_note)
    c = client_factory()

    made = c.post("/api/templates/apply", json={"template": "tpl-journal"}).json()
    assert made["title"].startswith("Journal ")
    assert made["body"].startswith(f"# {made['title']}")
    assert made["collection"] == "inbox"
    assert made["tags"] == ["journal"]
    # and it is really in the vault, not just in the reply
    stored = c.get(f"/api/notes/{made['id']}").json()
    assert stored["title"] == made["title"]
    assert stored["body"] == made["body"]


def test_a_template_can_be_applied_by_title(client_factory, seed_note):
    _journal_template(seed_note)
    c = client_factory()
    made = c.post("/api/templates/apply", json={"template": "Journal {{date}}"}).json()
    assert made["id"].startswith("tpl-")


def test_the_title_and_collection_asked_for_are_used(client_factory, seed_note):
    _journal_template(seed_note)
    c = client_factory()
    made = c.post(
        "/api/templates/apply",
        json={"template": "tpl-journal", "title": "Soup night", "collection": "journal"},
    ).json()
    assert made["title"] == "Soup night"
    assert made["collection"] == "journal"


def test_a_date_can_be_asked_for_so_a_template_can_be_used_for_another_day(
    client_factory, seed_note
):
    _journal_template(seed_note)
    c = client_factory()
    made = c.post(
        "/api/templates/apply", json={"template": "tpl-journal", "date": "2026-01-01"}
    ).json()
    assert made["title"] == "Journal 2026-01-01"
    assert made["dates"] == ["2026-01-01"]


def test_the_first_use_of_a_template_gets_the_plain_title(client_factory, seed_note):
    """A template must not reserve its own title.

    It is a note in the vault, so counting it among the taken titles numbers
    every note the template ever makes -- the first would be `Journal ... (2)`.
    Asserted on the *first* use, because asserting only that the second is
    `first + " (2)"` holds just as well when both are off by one.
    """
    _journal_template(seed_note)
    c = client_factory()
    made = c.post("/api/templates/apply", json={"template": "tpl-journal"}).json()
    assert made["title"].startswith("Journal 20")
    assert "(" not in made["title"], made["title"]


def test_applying_one_twice_does_not_make_twin_titles(client_factory, seed_note):
    _journal_template(seed_note)
    c = client_factory()
    first = c.post("/api/templates/apply", json={"template": "tpl-journal"}).json()
    second = c.post("/api/templates/apply", json={"template": "tpl-journal"}).json()
    assert first["id"] != second["id"]
    assert second["title"] == f"{first['title']} (2)"


def test_a_note_that_is_not_a_template_is_refused(client_factory, seed_note):
    seed_note("a-note", title="Just a note", collection="journal")
    c = client_factory()
    assert c.post("/api/templates/apply", json={"template": "a-note"}).status_code == 404
    assert c.post("/api/templates/apply", json={"template": "nope"}).status_code == 404
    assert c.post("/api/templates/apply", json={}).status_code == 404


def test_a_malformed_date_is_refused_rather_than_guessed_at(client_factory, seed_note):
    _journal_template(seed_note)
    c = client_factory()
    got = c.post(
        "/api/templates/apply", json={"template": "tpl-journal", "date": "last tuesday"}
    )
    assert got.status_code == 400


def test_the_templates_collection_is_offered_before_it_exists(client_factory):
    """Otherwise you cannot put the first template anywhere.

    The editor's collection dropdown offers what `/api/collections` reports, so a
    collection that only appears once it has something in it is one you can never
    put the first thing into.
    """
    c = client_factory()
    assert templates.TEMPLATES_COLLECTION in c.get("/api/collections").json()


def test_a_template_moved_out_of_the_collection_stops_being_one(
    client_factory, seed_note
):
    """Unassigning is just as important as assigning."""
    seed_note("tpl-journal", title="Journal {{date}}", collection="templates")
    c = client_factory()
    assert len(c.get("/api/templates").json()["templates"]) == 1

    c.patch("/api/notes/tpl-journal", json={"collection": "journal"})
    assert c.get("/api/templates").json()["templates"] == []


def test_a_template_is_not_a_card_on_the_board(client_factory, seed_note):
    """A shape for notes is not a thing to be doing."""
    _journal_template(seed_note)
    seed_note("a-task", title="Ship it", signifier="task", collection="journal")
    c = client_factory()

    board = c.get("/api/board").json()
    ids = [card["id"] for col in board["columns"] for card in col["cards"]]
    assert "tpl-journal" not in ids
    assert "a-task" in ids
    assert board["hidden_templates"] == 1
    assert board["hidden_generated"] == 0
