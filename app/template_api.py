"""HTTP routes for listing and applying note templates."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime

from fastapi import APIRouter, HTTPException

from . import templates
from .models import Note
from .vault import Vault


TemplateIndex = Callable[[], tuple[list[Note], dict[str, Note]]]
DateCoercer = Callable[[object, str], date]


def build_template_router(
    vault: Vault,
    index: TemplateIndex,
    coerce_date: DateCoercer,
) -> APIRouter:
    """Build template routes from the application's existing vault dependencies."""
    router = APIRouter()

    def free_note_id(by_id: Mapping[str, Note]) -> str:
        """Mint an ID for a note created from a template."""
        stamp = int(datetime.now().timestamp() * 1000)
        candidate = f"tpl-{stamp:x}"
        suffix = 2
        while candidate in by_id:
            candidate = f"tpl-{stamp:x}-{suffix}"
            suffix += 1
        return candidate

    @router.get("/api/templates")
    def list_templates():
        """The shapes a note can be made from."""
        notes, _ = index()
        return {
            "collection": templates.TEMPLATES_COLLECTION,
            "templates": [
                {
                    "id": note.id,
                    "title": note.title,
                    "signifier": note.signifier.value,
                    "tags": list(note.tags),
                    "body": note.body,
                    "placeholders": sorted(
                        {
                            match.lower()
                            for match in templates.PLACEHOLDER_RE.findall(note.body)
                            + templates.PLACEHOLDER_RE.findall(note.title)
                        }
                        & set(templates.KNOWN)
                    ),
                }
                for note in templates.list_templates(notes)
            ],
        }

    @router.post("/api/templates/apply")
    def apply_template(payload: dict):
        """Make a note from a template, by ID or by title."""
        wanted = str(payload.get("template") or "").strip()
        notes, by_id = index()
        source = by_id.get(wanted) or next(
            (
                note
                for note in templates.list_templates(notes)
                if note.title.lower() == wanted.lower()
            ),
            None,
        )
        if source is None or not templates.is_template(source):
            raise HTTPException(404, f"no template {wanted!r}")

        day = coerce_date(payload["date"], "date") if payload.get("date") else date.today()
        note = templates.build_note(
            source,
            note_id=free_note_id(by_id),
            title=payload.get("title"),
            collection=str(payload.get("collection") or "").strip() or "inbox",
            day=day,
            taken={note.title for note in notes if not templates.is_template(note)},
        )
        vault.write(note)
        return note.to_dict()

    return router
