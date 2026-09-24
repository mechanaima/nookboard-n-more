"""Summarising a recording: chunking, the prompts, and the reply's shape.

The summary of a lecture is the slowest and least reliable part of the pipeline,
so the parts that can be checked without a model are checked without one.
"""

import pytest

from app import ai


# --- chunking ---------------------------------------------------------------

def test_short_text_is_one_chunk():
    assert ai.chunk_text("one paragraph") == ["one paragraph"]
    assert ai.chunk_text("") == []
    assert ai.chunk_text("   \n\n  ") == []


def test_paragraphs_are_packed_up_to_the_limit():
    paras = [f"paragraph {i} " + "x" * 100 for i in range(8)]
    text = "\n\n".join(paras)
    chunks = ai.chunk_text(text, limit=400)
    assert len(chunks) > 1
    # The invariant, not a relationship between neighbouring chunks: no chunk
    # may exceed the budget. `chunks[1] == chunks[0] + something` would pass even
    # if every chunk were oversized.
    assert all(len(c) <= 400 for c in chunks), [len(c) for c in chunks]
    assert sum(len(c) for c in chunks) <= len(text) + 4 * len(chunks)


def test_a_chunk_boundary_falls_between_paragraphs():
    """Each paragraph ends in a marker, so "the cut landed between paragraphs"
    is a checkable claim. An earlier version asserted the chunk did not end in
    "y" when the fixture's paragraphs ended in "y" themselves -- it failed on
    correct output."""
    paras = [f"p{i} " + "y" * 90 + f" END{i}" for i in range(6)]
    chunks = ai.chunk_text("\n\n".join(paras), limit=200)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert chunk.rstrip().endswith("END" + chunk.rstrip()[-1]), (
            f"a chunk was cut mid-paragraph: ...{chunk[-30:]!r}"
        )


def test_one_oversized_paragraph_is_split_anyway():
    """There is no good place to cut a single 30-minute paragraph, so it is cut
    at a sentence boundary when there is one."""
    sentence = "This is a sentence about something. "
    text = sentence * 40  # ~1440 chars, one paragraph
    chunks = ai.chunk_text(text, limit=300)
    assert len(chunks) >= 4
    assert all(len(c) <= 300 for c in chunks)
    # Cut at ". " where possible, so most chunks end a sentence rather than a word.
    assert sum(1 for c in chunks if c.rstrip().endswith(".")) >= len(chunks) - 1


def test_chunking_keeps_every_word():
    text = "\n\n".join(f"para {i} " + "z" * 200 for i in range(10))
    chunks = ai.chunk_text(text, limit=500)
    joined = " ".join(chunks)
    for i in range(10):
        assert f"para {i}" in joined


# --- prompts ----------------------------------------------------------------

def test_the_chunk_prompt_says_which_part_it_is():
    """A chunk of a lecture reads like a whole document; without the index the
    model writes an introduction for the middle of a sentence."""
    messages = ai.build_transcript_summary_messages("hello", index=2, total=5)
    assert "part 2 of 5" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hello"}


def test_the_reduce_prompt_carries_every_set_of_notes():
    messages = ai.build_transcript_reduce_messages(["first", "second", "third"])
    body = messages[1]["content"]
    for note in ("first", "second", "third"):
        assert note in body
    assert "notes 1" in body and "notes 3" in body


def test_both_prompts_forbid_inventing_content():
    """The failure mode of a local model here is not a wrong summary, it is a
    cheerful one about an agenda the recording never had."""
    chunk = ai.build_transcript_summary_messages("x", index=1, total=1)[0]["content"]
    reduce_ = ai.build_transcript_reduce_messages(["x"])[0]["content"]
    assert "not in the text" in chunk or "not in the" in chunk
    assert "do not invent" in reduce_.lower()


# --- the reply's shape ------------------------------------------------------

def test_prose_and_bullets_both_survive():
    reply = "The lecture covered sorting.\n\n- Bubble sort\n- Merge sort\n"
    out = ai.parse_transcript_summary(reply)
    assert "The lecture covered sorting." in out
    assert "- Bubble sort" in out
    assert "- Merge sort" in out


def test_a_fully_fenced_reply_keeps_its_content():
    """Local models routinely wrap the whole answer in a fence. Dropping fenced
    lines instead of the markers would drop the entire summary."""
    out = ai.parse_transcript_summary("```markdown\nReal answer here.\n```")
    assert "Real answer here." in out
    assert "```" not in out


def test_a_heading_becomes_a_bold_lead_in():
    out = ai.parse_transcript_summary("## Summary\nBody text.")
    assert "##" not in out
    assert "**Summary**" in out
    assert "Body text." in out


def test_blank_runs_are_collapsed():
    out = ai.parse_transcript_summary("one\n\n\n\n\ntwo")
    assert out == "one\n\ntwo"


def test_an_empty_reply_is_empty_not_whitespace():
    assert ai.parse_transcript_summary("") == ""
    assert ai.parse_transcript_summary("   \n\n  ") == ""


def test_a_runaway_reply_is_capped():
    out = ai.parse_transcript_summary("word " * 3000)
    assert len(out) <= ai.TRANSCRIPT_SUMMARY_CHARS
