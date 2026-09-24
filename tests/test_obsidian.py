"""TDD: Obsidian interoperability.

nookboard and Obsidian both use plain Markdown, but they disagree on a few
conventions. These tests pin the translation layer:

  - Obsidian resolves [[X]] by FILENAME/ALIAS; nookboard resolves by the
    `title:` frontmatter field. We emit `aliases:` so links resolve both ways.
  - Obsidian notes may have no frontmatter at all. Parsing must not explode.
  - Obsidian supports inline #tags and [[Target|Display]] piped links.
"""
from datetime import date
from app.models import Note, Signifier, Status
from app.obsidian import (
    extract_inline_tags, wikilink_targets, split_frontmatter_tags,
)


# --- inline tags ----------------------------------------------------------

def test_extracts_inline_tags():
    assert extract_inline_tags("did the thing #home #urgent") == ["home", "urgent"]


def test_extracts_nested_tag():
    assert extract_inline_tags("see #project/nookboard notes") == ["project/nookboard"]


def test_inline_tags_dedupe_preserving_order():
    assert extract_inline_tags("#b #a #b") == ["b", "a"]


def test_markdown_headings_are_not_tags():
    assert extract_inline_tags("# Heading\n## Subheading") == []


def test_fenced_code_is_not_scanned():
    md = "```\n#not-a-tag\n```\nreal #yes"
    assert extract_inline_tags(md) == ["yes"]


def test_url_fragments_are_not_tags():
    assert extract_inline_tags("see https://example.com/page#section") == []


def test_tags_do_not_start_with_a_digit():
    assert extract_inline_tags("issue #123") == []


# --- wikilinks ------------------------------------------------------------

def test_wikilink_targets_plain():
    assert wikilink_targets("see [[Buying plants]]") == ["Buying plants"]


def test_wikilink_targets_piped():
    assert wikilink_targets("see [[Buying plants|the list]]") == ["Buying plants"]


def test_wikilink_targets_multiple_and_dedupe():
    got = wikilink_targets("[[A]] [[B|b]] [[A]]")
    assert got == ["A", "B"]


def test_wikilink_ignores_code():
    assert wikilink_targets("```\n[[nope]]\n```") == []


# --- frontmatter tag shapes ----------------------------------------------

def test_frontmatter_tags_list():
    assert split_frontmatter_tags(["a", "b"]) == ["a", "b"]


def test_frontmatter_tags_space_separated_string():
    assert split_frontmatter_tags("a b c") == ["a", "b", "c"]


def test_frontmatter_tags_comma_separated_string():
    assert split_frontmatter_tags("a, b") == ["a", "b"]


def test_frontmatter_tags_hash_prefixed():
    assert split_frontmatter_tags(["#a", "b"]) == ["a", "b"]


def test_frontmatter_tags_none():
    assert split_frontmatter_tags(None) == []


# --- round-trip -----------------------------------------------------------

def test_to_markdown_emits_alias_for_obsidian_links():
    n = Note(id="abc", collection="inbox", title="Buying plants", body="rose",
             signifier=Signifier.NOTE, status=Status.OPEN, dates=[])
    md = n.to_markdown()
    assert "aliases:" in md
    assert "Buying plants" in md


def test_bare_obsidian_note_parses_without_frontmatter():
    """An arbitrary Obsidian note has no nookboard fields at all."""
    md = "Just a note about #garden\n\nwith a body."
    n = Note.from_markdown(md, fallback_id="My Note")
    assert n.title == "My Note"
    assert n.collection == "inbox"
    assert n.signifier == Signifier.NOTE
    assert "garden" in n.tags


def test_inline_tags_merge_with_frontmatter_tags():
    md = "---\nid: x\ntitle: T\ntags: [a]\n---\n\nbody with #b"
    n = Note.from_markdown(md)
    assert n.tags == ["a", "b"]


def test_created_falls_back_to_today_without_frontmatter():
    n = Note.from_markdown("bare note", fallback_id="Bare")
    assert n.created == date.today()
