"""The two files that make nookboard installable on the machine it runs on.

A PWA is a manifest and a service worker, and both have to be served from the
**root of the origin**: a worker's scope is the directory it is served from, so
one living under `/static/` could only ever control `/static/` and would never
see a navigation.

Nothing here decides anything about notes -- it decides what the window looks
like once the app has been installed, which is the one thing about nookboard
that is not a fact about the vault. The palette is the app's own, so an
installed nookboard opens as nookboard rather than as a browser wrapped around
it.
"""

from __future__ import annotations

#: What an installed window is painted before the first paint of the document.
#: `theme_color` matches `index.html`'s `<meta name="theme-color">` (crust);
#: `background_color` is the base the app's glass sits on.
THEME = "#11111b"
BACKGROUND = "#1e1e2e"

NAME = "nookboard"
SHORT_NAME = "nookboard"
DESCRIPTION = "Notes, tasks and a journal as Markdown files on one machine."

MANIFEST_TYPE = "application/manifest+json"
WORKER_TYPE = "text/javascript"

#: (path, sizes, purpose). Chrome wants 192 for the launcher, 512 for the
#: splash screen, and a maskable one so Android can crop it into its own shape
#: without cutting the mark. Sizes are asserted against the real files in
#: `tests/test_pwa.py` -- an icon that *claims* a size it is not fails install
#: silently, which is the worst way for this to be wrong.
ICONS = (
    ("/static/icons/icon-192.png", "192x192", "any"),
    ("/static/icons/icon-512.png", "512x512", "any"),
    ("/static/icons/icon-maskable-512.png", "512x512", "maskable"),
)


def manifest() -> dict:
    """The web app manifest, as the browser reads it."""
    return {
        "name": NAME,
        "short_name": SHORT_NAME,
        "description": DESCRIPTION,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "theme_color": THEME,
        "background_color": BACKGROUND,
        "icons": [
            {"src": src, "sizes": sizes, "type": "image/png", "purpose": purpose}
            for src, sizes, purpose in ICONS
        ],
    }
