"""TDD: the AI feature layer.

Prompts and parsing are pure and tested here; the streaming endpoints are
tested in test_ai_api.py against a fake SSE server.
"""
from datetime import date

from app.ai import (
    MAX_RECAP_CHARS,
    parse_daily_summary,
    parse_link_suggestions,
    parse_tag_suggestions,
    select_relevant,
    build_daily_summary_messages,
    build_summary_messages,
    build_tags_messages,
    build_links_messages,
    build_ask_messages,
)
from app.models import Note, Signifier, Status


def _note(nid, title, body="", tags=None, collection="inbox"):
    return Note(id=nid, collection=collection, title=title, body=body,
                signifier=Signifier.NOTE, status=Status.OPEN, dates=[],
                tags=tags or [])


# --- tag parsing ----------------------------------------------------------

def test_parses_comma_separated_tags():
    assert parse_tag_suggestions("urgent, home, work", []) == ["urgent", "home", "work"]


def test_parses_bulleted_tags_and_strips_chatter():
    text = "Here are some tags:\n- urgent\n- home\n* work"
    assert parse_tag_suggestions(text, []) == ["urgent", "home", "work"]


def test_drops_tags_the_note_already_has():
    assert parse_tag_suggestions("home, work", ["home"]) == ["work"]


def test_normalises_tag_shape():
    assert parse_tag_suggestions("Home Repair, #urgent,  multi word ", []) == [
        "home-repair", "urgent", "multi-word",
    ]


def test_strips_a_prose_label_prefix():
    text = "Here are some tags: urgent, home\nAlso maybe: work"
    assert parse_tag_suggestions(text, []) == ["urgent", "home", "work"]


def test_rejects_sentence_like_candidates():
    assert parse_tag_suggestions("this is about gardening", []) == []


def test_caps_tag_count():
    assert len(parse_tag_suggestions(", ".join(f"t{i}" for i in range(20)), [])) <= 6


# --- link parsing ---------------------------------------------------------

def test_matches_suggested_titles_case_insensitively():
    known = ["Buying plants", "Garden tour"]
    assert parse_link_suggestions("buying plants\ngarden tour", known, exclude="x") == [
        "Buying plants", "Garden tour",
    ]


def test_link_suggestions_drop_unknown_titles():
    assert parse_link_suggestions("Buying plants\nNonexistent", ["Buying plants"], exclude="x") == [
        "Buying plants",
    ]


def test_link_suggestions_exclude_the_note_itself():
    assert parse_link_suggestions("Target", ["Target"], exclude="Target") == []


def test_link_suggestions_handle_bullets_and_prose():
    text = "- Buying plants\n- Garden tour\nThese are related."
    got = parse_link_suggestions(text, ["Buying plants", "Garden tour"], exclude="z")
    assert got == ["Buying plants", "Garden tour"]


# --- relevance selection --------------------------------------------------

def test_select_relevant_ranks_by_term_overlap():
    notes = [
        _note("a", "Garden tour", "roses and basil"),
        _note("b", "Server notes", "nginx reverse proxy"),
        _note("c", "Plant care", "water the basil plants weekly"),
    ]
    got = select_relevant(notes, "basil plants", limit=2)
    assert [n.id for n in got] == ["c", "a"]


def test_select_relevant_ignores_stopwords():
    notes = [_note("a", "x", "the and of"), _note("b", "nginx proxy", "reverse proxy")]
    got = select_relevant(notes, "what is the nginx proxy", limit=2)
    assert [n.id for n in got] == ["b"]


def test_select_relevant_returns_nothing_for_an_all_stopword_question():
    notes = [_note("a", "x", "the and of")]
    assert select_relevant(notes, "what is the and of", limit=2) == []


def test_select_relevant_respects_limit():
    notes = [_note(str(i), f"n{i}", "alpha") for i in range(10)]
    assert len(select_relevant(notes, "alpha", limit=3)) == 3


# --- prompt construction --------------------------------------------------

def test_summary_messages_include_the_note():
    msgs = build_summary_messages(_note("a", "Garden tour", "roses"))
    assert msgs[0]["role"] == "system"
    assert any("Garden tour" in m["content"] and "roses" in m["content"] for m in msgs)


def test_summary_warns_about_existing_summary():
    msgs = build_summary_messages(_note("a", "T", "long body"))
    blob = " ".join(m["content"] for m in msgs).lower()
    assert "markdown" in blob


def test_tags_messages_list_existing_tags():
    msgs = build_tags_messages(_note("a", "T", "b", tags=["home"]), vault_tags=["home", "work"])
    blob = " ".join(m["content"] for m in msgs)
    assert "home" in blob and "work" in blob


def test_links_messages_list_candidate_titles():
    cand = ["Buying plants", "Garden tour"]
    msgs = build_links_messages(_note("a", "T", "b"), cand)
    blob = " ".join(m["content"] for m in msgs)
    assert "Buying plants" in blob
    assert "Garden tour" in blob


def test_links_prompt_does_not_hand_the_model_a_cheap_escape():
    """Observed live: with an explicit 'reply NONE' escape the quantised model
    bailed on notes that plainly shared subject words. The prompt should push it
    to name real connections while still allowing a genuine NONE."""
    msgs = build_links_messages(_note("a", "T", "b"), ["X"])
    system = msgs[0]["content"].lower()
    assert "pre-selected" in system
    assert "prefer naming a few real connections over naming none" in system


def test_ask_messages_include_retrieved_context():
    ctx = [_note("a", "Garden tour", "roses and basil")]
    msgs = build_ask_messages("what plants?", ctx)
    blob = " ".join(m["content"] for m in msgs)
    assert "roses and basil" in blob
    assert "what plants?" in blob


def test_ask_messages_handle_no_context():
    msgs = build_ask_messages("anything?", [])
    assert len(msgs) >= 2


# --- daily recap ----------------------------------------------------------
#
# The recap is written into a note, so it has to arrive as prose: a heading or a
# fence would be committed to the file as broken markup.

def test_daily_messages_supply_the_work_as_fact():
    msgs = build_daily_summary_messages(date(2026, 9, 23), [_note("a", "Ship the zine")])
    system = msgs[0]["content"]
    blob = " ".join(m["content"] for m in msgs)
    assert "Ship the zine" in blob
    assert "2026-09-23" in blob
    # The instruction that matters most: a journal entry crediting work nobody
    # did is the thing you would later trust and be wrong about.
    assert "do not invent" in system
    assert "No heading, no bullets" in system


def test_recap_collapses_to_one_paragraph():
    assert parse_daily_summary("A slow day.\nI shipped the zine.") == (
        "A slow day. I shipped the zine."
    )


def test_recap_drops_headings_and_code_fences():
    assert parse_daily_summary("## Recap\n```\nA slow day.\n```") == "A slow day."


def test_recap_joins_bullets_into_prose():
    """The section already lists the tasks; a second list adds nothing."""
    assert parse_daily_summary("- Shipped the zine\n- Fixed the printer") == (
        "Shipped the zine Fixed the printer"
    )


def test_recap_strips_wrapping_quotes():
    assert parse_daily_summary('"A slow day."') == "A slow day."


def test_recap_is_capped_and_cut_at_a_sentence():
    text = ". ".join(f"Sentence number {i} with a few more words" for i in range(40))
    out = parse_daily_summary(text)
    assert len(out) <= MAX_RECAP_CHARS
    # Not truncated mid-word where a sentence boundary was available.
    assert out.endswith(".")


def test_recap_of_nothing_is_empty():
    assert parse_daily_summary("") == ""
    assert parse_daily_summary("```") == ""
    assert parse_daily_summary(None) == ""
