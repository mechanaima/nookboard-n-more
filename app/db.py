"""SQLite index for nookboard notes.

Markdown remains the source of truth. This module maintains a denormalized
index that powers search, calendar aggregation, mood breakdown, tags,
backlinks, and the recurring-note scheduler.

The schema lives in SCHEMA below; rebuilding from .md files is done by
app.main on first boot if the DB file is missing or empty.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Iterable

import sqlite3

from .models import Note, Signifier, Status, is_generated_note_id
from .obsidian import wikilink_targets


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    collection  TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    signifier   TEXT NOT NULL,
    status      TEXT NOT NULL,
    dates_csv   TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    created     TEXT NOT NULL,
    mood        TEXT,
    pain        INTEGER,
    tags_csv    TEXT NOT NULL DEFAULT '',
    recurrence  TEXT,
    stage       TEXT,
    blocked_by_csv TEXT NOT NULL DEFAULT '',
    position    REAL,
    completed   TEXT,
    search_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_collection ON notes(collection);
CREATE TABLE IF NOT EXISTS note_links (
    source_id    TEXT NOT NULL,
    target_title TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_target ON note_links(target_title);
CREATE TABLE IF NOT EXISTS daily_summary_state (
    day          TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    settled      INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT
);
CREATE TABLE IF NOT EXISTS weekly_summary_state (
    key          TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    settled      INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT
);
CREATE TABLE IF NOT EXISTS recurrence_state (
    note_id  TEXT PRIMARY KEY,
    last_run TEXT NOT NULL
);
-- sieve scrapes. A run is minutes of work and an accepted start spends
-- credits, so this ledger is the durable half of the integration: the session
-- id is written here before the response returns, and a crash resumes polling
-- from it rather than starting a duplicate run. It survives `rebuild_from`,
-- which only clears the note index.
CREATE TABLE IF NOT EXISTS sieve_sessions (
    session_id     TEXT PRIMARY KEY,
    instruction    TEXT NOT NULL,
    request_json   TEXT NOT NULL,
    status         TEXT NOT NULL,
    turns          INTEGER NOT NULL DEFAULT 0,
    awaiting_turn  INTEGER,
    turn_body_json TEXT,
    poll_delay     REAL NOT NULL DEFAULT 5.0,
    result_json    TEXT,
    error          TEXT,
    next_poll_at   TEXT,
    created        TEXT NOT NULL,
    updated        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sieve_pending ON sieve_sessions(status);
"""

#: Columns added after the first release. `CREATE TABLE IF NOT EXISTS` is a
#: no-op on an existing file, so a vault indexed by an older build keeps the old
#: shape and every read of a new column raises. These additive ALTERs keep the
#: index disposable without making it stale.
MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("stage", "TEXT"),
    ("blocked_by_csv", "TEXT NOT NULL DEFAULT ''"),
    ("position", "REAL"),
    ("pain", "INTEGER"),
    ("completed", "TEXT"),
    ("pinned", "INTEGER NOT NULL DEFAULT 0"),
)

#: Additive columns for the daily-summary ledger, same reasoning as MIGRATIONS.
#: `settled` is the load-bearing one: a row used to mean "done, never again",
#: which conflated a recap that worked with one that never happened. A failed
#: recap now records its attempt without settling the day, so the next tick
#: tries again and the prose arrives as soon as the model does.
DAILY_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("settled", "INTEGER NOT NULL DEFAULT 1"),
    ("last_error", "TEXT"),
)

#: How many times a day's recap is retried before it is left with just its list.
#: Six ticks is half an hour: enough to ride out a model still starting up, not
#: so many that a machine left overnight keeps asking a model that is not there.
MAX_RECAP_ATTEMPTS = 6


def _loads(raw: str | None, default):
    """Decode a stored JSON column, falling back rather than raising.

    The index is disposable; a row somebody hand-edited to nonsense should cost
    that one value, not the whole read.
    """
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _extract_wikilink_titles(body: str) -> list[str]:
    return wikilink_targets(body)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        have = {row["name"] for row in self.conn.execute("PRAGMA table_info(notes)")}
        for column, decl in MIGRATIONS:
            if column not in have:
                self.conn.execute(f"ALTER TABLE notes ADD COLUMN {column} {decl}")
        # Every row written before retries existed was a settled success, which
        # is what the `settled` default says, so the old table migrates cleanly.
        daily_have = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(daily_summary_state)")
        }
        for column, decl in DAILY_MIGRATIONS:
            if column not in daily_have:
                self.conn.execute(
                    f"ALTER TABLE daily_summary_state ADD COLUMN {column} {decl}"
                )

    def upsert(self, n: Note) -> None:
        """Index a note. The Note is the whole truth — never a partial update.

        This used to take an optional `mood` and keep the indexed value when it
        was absent (`COALESCE(excluded.mood, notes.mood)`). Since the vault file
        is written from the same Note, the only thing that could do was leave
        the index holding a mood the file no longer had: clearing a mood cleared
        the file and silently kept the row, so search and the index kept
        reporting a mood the note had dropped.
        """
        search_text = f"{n.title}\n{n.body}".lower()
        tags_csv = ",".join(n.tags)
        self.conn.execute(
            """
            INSERT INTO notes (id, collection, title, body, signifier, status,
                               dates_csv, parent_id, created, mood, pain, tags_csv,
                               recurrence, stage, blocked_by_csv, position,
                               completed, pinned, search_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                collection=excluded.collection,
                title=excluded.title,
                body=excluded.body,
                signifier=excluded.signifier,
                status=excluded.status,
                dates_csv=excluded.dates_csv,
                parent_id=excluded.parent_id,
                created=excluded.created,
                mood=excluded.mood,
                pain=excluded.pain,
                tags_csv=excluded.tags_csv,
                recurrence=excluded.recurrence,
                stage=excluded.stage,
                blocked_by_csv=excluded.blocked_by_csv,
                position=excluded.position,
                completed=excluded.completed,
                pinned=excluded.pinned,
                search_text=excluded.search_text
            """,
            (
                n.id, n.collection, n.title, n.body,
                n.signifier.value, n.status.value,
                ",".join(d.isoformat() for d in n.dates),
                n.parent_id, n.created.isoformat(),
                n.mood, n.pain, tags_csv, n.recurrence,
                n.stage, ",".join(n.blocked_by), n.position,
                n.completed.isoformat() if n.completed else None,
                1 if n.pinned else 0,
                search_text,
            ),
        )
        # Update backlink index: drop old, re-insert from current body.
        self.conn.execute("DELETE FROM note_links WHERE source_id = ?", (n.id,))
        targets = _extract_wikilink_titles(n.body)
        if targets:
            self.conn.executemany(
                "INSERT INTO note_links (source_id, target_title) VALUES (?, ?)",
                [(n.id, t) for t in targets],
            )
        # Ensure a recurrence_state row exists.
        self.conn.execute(
            "INSERT OR IGNORE INTO recurrence_state (note_id, last_run) VALUES (?, ?)",
            (n.id, n.created.isoformat()),
        )
        self.conn.commit()

    def delete(self, note_id: str) -> None:
        self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.execute("DELETE FROM note_links WHERE source_id = ?", (note_id,))
        self.conn.execute("DELETE FROM recurrence_state WHERE note_id = ?", (note_id,))
        self.conn.commit()

    def get(self, note_id: str) -> Note | None:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return None
        return self._row_to_note(row)

    def search(self, q: str) -> list[Note]:
        if not q.strip():
            return []
        like = f"%{q.strip().lower()}%"
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE search_text LIKE ? ORDER BY created DESC",
            (like,),
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def all_ids(self) -> list[str]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM notes").fetchall()]

    def month_counts(self, year: int, month: int) -> dict[str, int]:
        prefix = f"{year:04d}-{month:02d}-"
        rows = self.conn.execute("SELECT dates_csv FROM notes").fetchall()
        out: dict[str, int] = {}
        for row in rows:
            for d in (row["dates_csv"] or "").split(","):
                if d.startswith(prefix):
                    out[d] = out.get(d, 0) + 1
        return out

    def filter_by_tag(self, tag: str) -> list[Note]:
        like = f"%{tag}%"
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE ',' || tags_csv || ',' LIKE ? ORDER BY created DESC",
            (like,),
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def backlinks_for_title(self, title: str) -> list[Note]:
        rows = self.conn.execute(
            "SELECT n.* FROM notes n JOIN note_links l ON l.source_id = n.id "
            "WHERE l.target_title = ? ORDER BY n.created DESC",
            (title,),
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    # -- daily summaries ---------------------------------------------------
    # A run at 22:00 has to happen exactly once. `pending_days` cannot tell the
    # difference between "owed" and "owed again", so the fact is recorded here
    # rather than inferred: without it, every tick would spend a model call and
    # rewrite the note for the rest of the evening.

    # `column` is the period key's column name, which differs because the names
    # are the honest ones: a day is a `day`, an ISO week is a `key`. Taken as an
    # argument so one implementation serves both rather than two that drift.
    def _settle(
        self, table: str, column: str, key: str, at: datetime | None = None
    ) -> None:
        """Settle a period: it has been summarised and will never be re-run."""
        self.conn.execute(
            f"INSERT INTO {table} ({column}, generated_at, settled) VALUES (?, ?, 1) "
            f"ON CONFLICT({column}) DO UPDATE SET generated_at=excluded.generated_at, "
            "settled=1",
            (key, (at or datetime.now()).isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def _attempt(
        self, table: str, column: str, key: str, error: str, at: datetime | None = None
    ) -> bool:
        """Record a recap that failed, without settling the period.

        Returns whether the period is now settled -- true once the attempts run
        out, so a model that is never coming back is stopped being asked. Until
        then it stays owed and the next tick tries again: a machine whose model
        starts at nine gets its recap instead of a permanently prose-less note.
        """
        now = (at or datetime.now()).isoformat(timespec="seconds")
        row = self.conn.execute(
            f"SELECT attempts FROM {table} WHERE {column} = ?", (key,)
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        settled = attempts >= MAX_RECAP_ATTEMPTS
        self.conn.execute(
            f"INSERT INTO {table} ({column}, generated_at, attempts, settled, "
            "last_error) VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT({column}) DO UPDATE SET generated_at=excluded.generated_at, "
            "attempts=excluded.attempts, settled=excluded.settled, "
            "last_error=excluded.last_error",
            (key, now, attempts, int(settled), error),
        )
        self.conn.commit()
        return settled

    def _settled(self, table: str, column: str) -> set[str]:
        rows = self.conn.execute(
            f"SELECT {column} AS k FROM {table} WHERE settled = 1"
        ).fetchall()
        return {row["k"] for row in rows}

    def _retrying(self, table: str, column: str) -> dict[str, str]:
        rows = self.conn.execute(
            f"SELECT {column} AS k, last_error FROM {table} WHERE settled = 0"
        ).fetchall()
        return {row["k"]: row["last_error"] for row in rows}

    # Two tables rather than one keyed by period: the keys are dates and ISO
    # weeks, and anything reading a shared ledger would have to tell them apart
    # before it could parse one -- a crash waiting for the first weekly row.
    # The semantics above are shared; only the namespace differs.

    def mark_daily_generated(self, day: str, at: datetime | None = None) -> None:
        self._settle("daily_summary_state", "day", day, at)

    def note_daily_attempt(
        self, day: str, error: str, at: datetime | None = None
    ) -> bool:
        return self._attempt("daily_summary_state", "day", day, error, at)

    def daily_generated_days(self) -> set[str]:
        """Days that will not be re-run: settled successes and given-up failures."""
        return self._settled("daily_summary_state", "day")

    def daily_retrying_days(self) -> dict[str, str]:
        """Days written with their list but still waiting on a recap."""
        return self._retrying("daily_summary_state", "day")

    def clear_daily_generated(self, day: str) -> None:
        """Forget that a day was summarised, so a forced refresh can re-run it."""
        self.conn.execute("DELETE FROM daily_summary_state WHERE day = ?", (day,))
        self.conn.commit()

    def mark_weekly_generated(self, key: str, at: datetime | None = None) -> None:
        self._settle("weekly_summary_state", "key", key, at)

    def note_weekly_attempt(
        self, key: str, error: str, at: datetime | None = None
    ) -> bool:
        return self._attempt("weekly_summary_state", "key", key, error, at)

    def weekly_generated_weeks(self) -> set[str]:
        """Weeks that will not be re-run."""
        return self._settled("weekly_summary_state", "key")

    def weekly_retrying_weeks(self) -> dict[str, str]:
        """Weeks written with their list but still waiting on a recap."""
        return self._retrying("weekly_summary_state", "key")

    def clear_weekly_generated(self, key: str) -> None:
        """Forget that a week was summarised, so a forced refresh can re-run it."""
        self.conn.execute("DELETE FROM weekly_summary_state WHERE key = ?", (key,))
        self.conn.commit()

    # -- sieve scrapes -----------------------------------------------------
    # The session id is written before the API response leaves the server, and
    # it is the whole point of the table: a run that was accepted is minutes of
    # work and already spent credits, so a restart must resume polling it rather
    # than start a second one. Nothing here ever re-POSTs a start.

    def save_sieve_session(
        self,
        *,
        session_id: str,
        instruction: str,
        request: dict,
        status: str = "queued",
        next_poll_at: str | None = None,
        at: datetime | None = None,
    ) -> None:
        now = (at or datetime.now()).isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO sieve_sessions
                (session_id, instruction, request_json, status, turns,
                 awaiting_turn, turn_body_json, poll_delay, result_json, error,
                 next_poll_at, created, updated)
            VALUES (?, ?, ?, ?, 0, NULL, NULL, 5.0, NULL, NULL, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                instruction=excluded.instruction,
                request_json=excluded.request_json,
                status=excluded.status,
                next_poll_at=excluded.next_poll_at,
                updated=excluded.updated
            """,
            (session_id, instruction, json.dumps(request), status, next_poll_at, now, now),
        )
        self.conn.commit()

    def get_sieve_session(self, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sieve_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return self._sieve_row(row)

    def list_sieve_sessions(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sieve_sessions ORDER BY created DESC, session_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._sieve_row(row) for row in rows]

    def pending_sieve_sessions(self) -> list[dict]:
        """Runs that are still owed a poll, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM sieve_sessions WHERE status IN ('queued', 'running') "
            "ORDER BY created ASC, session_id ASC"
        ).fetchall()
        return [self._sieve_row(row) for row in rows]

    def set_sieve_session(
        self,
        session_id: str,
        *,
        status: str,
        turns: int | None = None,
        poll_delay: float | None = None,
        payload: dict | None = None,
        error: str | None = None,
        next_poll_at: str | None = None,
        at: datetime | None = None,
    ) -> None:
        """Record one poll's outcome.

        `turns`, `poll_delay` and the payload are COALESCEd: a poll that only
        learns the status must not erase the last answer or reset the backoff.
        `error` and `next_poll_at` are written as given, because clearing a
        transient error and scheduling the next poll are exactly the writes a
        successful poll needs to make.
        """
        now = (at or datetime.now()).isoformat(timespec="seconds")
        result = json.dumps(payload) if payload is not None else None
        self.conn.execute(
            """
            UPDATE sieve_sessions SET
                status = ?,
                error = ?,
                next_poll_at = ?,
                turns = COALESCE(?, turns),
                poll_delay = COALESCE(?, poll_delay),
                result_json = COALESCE(?, result_json),
                updated = ?
            WHERE session_id = ?
            """,
            (status, error, next_poll_at, turns, poll_delay, result, now, session_id),
        )
        self.conn.commit()

    def set_sieve_awaiting_turn(
        self, session_id: str, awaiting_turn: int, body: dict, at: datetime | None = None
    ) -> None:
        """Record a follow-up turn that has been sent but not yet answered.

        Polling stops treating this run as finished until the server's turn
        count passes `awaiting_turn`; the body is kept so a lost POST can be
        resent rather than reconstructed from memory.
        """
        now = (at or datetime.now()).isoformat(timespec="seconds")
        self.conn.execute(
            "UPDATE sieve_sessions SET awaiting_turn = ?, turn_body_json = ?, updated = ? "
            "WHERE session_id = ?",
            (awaiting_turn, json.dumps(body), now, session_id),
        )
        self.conn.commit()

    def clear_sieve_awaiting_turn(self, session_id: str, at: datetime | None = None) -> None:
        now = (at or datetime.now()).isoformat(timespec="seconds")
        self.conn.execute(
            "UPDATE sieve_sessions SET awaiting_turn = NULL, turn_body_json = NULL, "
            "updated = ? WHERE session_id = ?",
            (now, session_id),
        )
        self.conn.commit()

    @staticmethod
    def _sieve_row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "instruction": row["instruction"],
            "request": _loads(row["request_json"], {}),
            "status": row["status"],
            "turns": row["turns"],
            "awaiting_turn": row["awaiting_turn"],
            "turn_body": _loads(row["turn_body_json"], None),
            "poll_delay": row["poll_delay"],
            "result": _loads(row["result_json"], None),
            "error": row["error"],
            "next_poll_at": row["next_poll_at"],
            "created": row["created"],
            "updated": row["updated"],
        }

    def run_recurring(self, today: date) -> list[Note]:
        """For each note with recurrence, instantiate due dates up to `today`.

        Returns the list of newly-created Note instances. The caller
        is responsible for writing them to the vault (file system).
        """
        created: list[Note] = []
        rows = self.conn.execute(
            "SELECT n.*, COALESCE(s.last_run, n.created) AS last_run "
            "FROM notes n LEFT JOIN recurrence_state s ON s.note_id = n.id "
            "WHERE n.recurrence IS NOT NULL AND n.recurrence != ''"
        ).fetchall()
        for row in rows:
            real = self._row_to_note(row)
            if is_generated_note_id(real.id):
                # The app already writes these itself. Instantiating one as if
                # it were a recurrence parent manufactures junk beside it --
                # `daily-<day>-<next-day>` -- a new one every day.
                continue
            last_run = date.fromisoformat(row["last_run"])
            if not real.recurrence:
                continue
            while True:
                nxt = _next_instance(last_run, real.recurrence)
                if not nxt or nxt > today:
                    break
                new_id = f"{real.id}-{nxt.isoformat()}"
                if self.get(new_id) is not None:
                    last_run = nxt
                    continue
                instance = Note(
                    id=new_id,
                    collection=real.collection,
                    title=f"{real.title} ({nxt.isoformat()})",
                    body=real.body,
                    signifier=real.signifier,
                    status=Status.OPEN,
                    dates=[nxt],
                    parent_id=real.id,
                    created=nxt,
                    mood=None,
                    tags=[],
                )
                # Caller writes the file; we just bump state and return the note.
                self.conn.execute(
                    "UPDATE recurrence_state SET last_run = ? WHERE note_id = ?",
                    (nxt.isoformat(), real.id),
                )
                self.conn.commit()
                created.append(instance)
                last_run = nxt
        return created

    def rebuild_from(self, notes: Iterable[Note]) -> None:
        self.conn.executescript("DELETE FROM notes; DELETE FROM note_links; DELETE FROM recurrence_state;")
        for n in notes:
            self.upsert(n)

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> Note:
        dates_csv = row["dates_csv"] or ""
        dates = [date.fromisoformat(d) for d in dates_csv.split(",") if d]
        tags_csv = row["tags_csv"] or ""
        tags = [t for t in tags_csv.split(",") if t]
        deps_csv = row["blocked_by_csv"] or ""
        blocked_by = [d for d in deps_csv.split(",") if d]
        return Note(
            id=row["id"],
            collection=row["collection"],
            title=row["title"],
            body=row["body"],
            signifier=Signifier(row["signifier"]),
            status=Status(row["status"]),
            dates=dates,
            parent_id=row["parent_id"],
            created=date.fromisoformat(row["created"]),
            mood=row["mood"],
            pain=row["pain"],
            tags=tags,
            recurrence=row["recurrence"],
            stage=row["stage"],
            blocked_by=blocked_by,
            pinned=bool(row["pinned"]),
            position=row["position"],
            completed=date.fromisoformat(row["completed"]) if row["completed"] else None,
        )


def _next_instance(last: date, kind: str) -> date | None:
    if kind == "daily":
        return last + timedelta(days=1)
    if kind == "weekly":
        return last + timedelta(days=7)
    if kind == "monthly":
        from calendar import monthrange
        year = last.year + (1 if last.month == 12 else 0)
        month = 1 if last.month == 12 else last.month + 1
        day = min(last.day, monthrange(year, month)[1])
        return date(year, month, day)
    return None