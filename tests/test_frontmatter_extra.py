"""Frontmatter this app did not write, and must not delete.

A vault is hand-edited text. These tests exist because a write used to rebuild the
frontmatter from a fixed list of keys, so anything the model did not recognise -- a
plugin's setting, a field from another app, a note to the future -- was gone the next
time the note was saved. Losing a note's own words is the one thing a note app must
never do, so the rule is: unfamiliar keys are read, kept verbatim, and written back.
"""
import json

from app.models import Note


def test_an_unfamiliar_key_survives_a_round_trip():
    md = """---
id: kept
collection: inbox
title: Kept
weird-key: keep me
---
body
"""
    out = Note.from_markdown(md).to_markdown()
    assert "weird-key: keep me" in out


def test_nested_and_list_values_survive():
    md = """---
id: nested
collection: inbox
title: Nested
plugin:
  depth: 2
  names:
    - one
    - two
---
body
"""
    note = Note.from_markdown(md)
    assert note.frontmatter_extra["plugin"] == {"depth": 2, "names": ["one", "two"]}
    assert "names:" in note.to_markdown()


def test_the_apps_own_fields_win_a_collision():
    # `aliases` is written by this app (to make Obsidian wikilinks resolve), so a
    # file's own value is replaced rather than preserved -- the app maintains it.
    md = """---
id: aliased
collection: inbox
title: Aliased
aliases:
  - something else
---
body
"""
    out = Note.from_markdown(md).to_markdown()
    assert "something else" not in out
    assert "Aliased" in out


def test_a_note_without_extras_has_none():
    md = "---\nid: plain\ncollection: inbox\ntitle: Plain\n---\nbody\n"
    note = Note.from_markdown(md)
    assert note.frontmatter_extra == {}
    assert "weird" not in note.to_markdown()


def test_what_the_client_is_sent_is_json():
    # YAML hands back dates and times; a client cannot render a `datetime`, and a
    # response that cannot be serialised is a note that cannot be read at all.
    md = """---
id: dated
collection: inbox
title: Dated
reviewed: 2026-09-24
maybe: null
---
body
"""
    note = Note.from_markdown(md)
    assert json.loads(json.dumps(note.to_dict()))["frontmatter_extra"]["reviewed"] == "2026-09-24"


def test_the_extras_are_not_part_of_equality():
    # Two reads of the same note are the same note, extras or not -- otherwise every
    # round-trip test in the suite would have to know about this field.
    a = Note.from_markdown("---\nid: x\ncollection: inbox\ntitle: X\nfoo: 1\n---\nbody\n")
    b = Note.from_markdown("---\nid: x\ncollection: inbox\ntitle: X\n---\nbody\n")
    assert a == b
