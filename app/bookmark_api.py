"""HTTP routes for notes that preserve browsable addresses."""
from __future__ import annotations

from fastapi import APIRouter

from . import bookmarks, health_run
from .vault import Vault


def build_bookmark_router(vault: Vault) -> APIRouter:
    """Build routes for listing bookmarks and checking their addresses."""
    router = APIRouter()

    @router.get("/api/bookmarks")
    def list_bookmarks():
        """Every note that points at an address, grouped by its first tag."""
        return bookmarks.view(
            [note for note in vault.list_all() if bookmarks.is_bookmark(note)]
        )

    @router.post("/api/bookmarks/check")
    def check_bookmarks():
        """Check every saved address and return one bounded result per URL."""
        notes = [
            note for note in vault.list_all() if bookmarks.is_bookmark(note)
        ]
        urls = [url for url in (bookmarks.url_of(n) for n in notes) if url]
        return health_run.check_all(urls)

    return router
