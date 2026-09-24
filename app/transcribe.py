"""Turning a recording into a note.

Everything here is a pure function of its arguments: the argv a subprocess
needs, the parse of what it printed, and the markdown that comes out. The actual
running lives in `transcribe_run`, which is the only part that touches the disk
or a process.

Two decisions shape this file.

**The engine is a shell-out to a local whisper.cpp build**, not a Python
binding. Da already has one built against his GPU with models downloaded, and a
transcription is minutes of work on a file that never leaves the machine: an
in-process library would mean a second copy of the model and a second place for
"is it installed" to be wrong.

**Availability is answered up front, not discovered by failing.** `available()`
reports the binary, the models it can see, and ffmpeg — so the UI can say
"whisper-cli not found, set NOOKBOARD_WHISPER_CLI" instead of starting a job and
dying three seconds later with a traceback in a log nobody reads.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

#: Notes about recordings go in their own collection by default, so a vault that
#: gets a lecture a week does not bury everything else.
TRANSCRIPT_COLLECTION = "transcripts"

#: The models offered. `small` is the default because it is the one that finishes
#: a lecture while you are still in the room; `medium` is there for a recording
#: you care about enough to wait for.
MODEL_CHOICES = ("small", "medium")

#: The id prefix. `models.is_transcript_note_id` owns this pattern -- don't
#: re-spell it here, or the guard and the producer drift and the note quietly
#: becomes a board card (see the weekly-note bug in the skill).
ID_PREFIX = "transcript-"

#: The scopes `sections` fences inside a transcript note. The summary is its own
#: region so that re-summarising cannot touch the transcript, and the transcript
#: is its own region so that re-transcribing cannot touch anything you wrote
#: underneath it.
SUMMARY_SCOPE = "transcript-summary"
BODY_SCOPE = "transcript-body"

#: whisper.cpp writes a JSON report next to the audio; we read that rather than
#: scraping its stdout, which is decorated with progress and timing lines.
MEDIA_EXTENSIONS = {
    ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma",
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts",
}


# -- ids and names -----------------------------------------------------------


def slugify(text: str, *, limit: int = 44) -> str:
    """A filename-safe slug. Falls back to `recording` rather than empty.

    An audio file's name is often all punctuation or non-Latin script; an empty
    slug would make every such note collide on the same id.
    """
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:limit].strip("-") or "recording"


def note_id(day: date, slug: str, taken: Iterable[str] = ()) -> str:
    """`transcript-<day>-<slug>`, suffixed only if that is already used.

    The first one is bare: `transcript-2026-09-24-lecture-3`, not `...-2`. The
    suffix counts from the second collision, which is the same shape as
    `templates.unique_title`. Tests must assert the first case directly -- a
    check that only compares one id to the next passes just as happily when
    every id is shifted by one.
    """
    taken_set = set(taken)
    base = f"{ID_PREFIX}{day.isoformat()}-{slug}"
    if base not in taken_set:
        return base
    n = 2
    while f"{base}-{n}" in taken_set:
        n += 1
    return f"{base}-{n}"


def stamp(seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` past an hour.

    A lecture is over an hour and a voice memo is under a minute; one shape
    cannot serve both, and `0:04:31` in a two-minute recording looks like a bug.
    """
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# -- probing and extracting --------------------------------------------------


@dataclass(frozen=True)
class Media:
    """What ffprobe could tell us about the input."""

    path: Path
    duration_s: float
    container: str
    audio_codec: str
    sample_rate: int
    channels: int

    def describe(self) -> str:
        return f"{self.container} · {self.audio_codec} · {stamp(self.duration_s)}"


def ffprobe_for(ffmpeg: str) -> str:
    """ffprobe ships beside ffmpeg.

    Used so that the binary named in the engine line is the binary that actually
    runs: reporting one ffmpeg and running another would make the report useless
    exactly when it is needed.
    """
    if ffmpeg and _looks_like_a_path(ffmpeg):
        beside = Path(ffmpeg).with_name("ffprobe")
        if beside.is_file() and os.access(beside, os.X_OK):
            return str(beside)
    return "ffprobe"


def probe_args(path: Path, *, ffprobe: str = "ffprobe") -> list[str]:
    """ffprobe, asking only for what we use, in JSON."""
    return [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]


def parse_probe(payload: str) -> Media:
    """Read ffprobe's JSON. Refuses a file with no audio stream.

    A silent screen recording is the common case: the pipeline would otherwise
    transcribe nothing, write a note containing an empty transcript, and report
    success. Better to say there is no audio in it.
    """
    data = json.loads(payload or "{}")
    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise TranscriptionError("that file has no audio track to transcribe")
    fmt = data.get("format") or {}
    fmt_name = str(fmt.get("format_name") or "").split(",")[0] or "unknown"
    try:
        duration = float(fmt.get("duration") or audio.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return Media(
        path=Path(str(fmt.get("filename") or "")),
        duration_s=duration,
        container=fmt_name,
        audio_codec=str(audio.get("codec_name") or "unknown"),
        sample_rate=int(audio.get("sample_rate") or 0),
        channels=int(audio.get("channels") or 0),
    )


def extract_args(src: Path, dest: Path, *, ffmpeg: str = "ffmpeg") -> list[str]:
    """ffmpeg: one channel at 16 kHz, which is what whisper wants.

    `-vn` drops video, so a lecture recording costs a decode and not 2 GB of
    frames. This is also why a video file needs no special handling anywhere
    else in the pipeline.
    """
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", "-y", str(dest),
    ]


def whisper_args(
    cli: str, model_path: Path, audio: Path, out_prefix: Path, *, threads: int = 0,
) -> list[str]:
    """whisper-cli, asked for its JSON report at a known path."""
    args = [
        cli, "-m", str(model_path), "-f", str(audio),
        "-oj", "-of", str(out_prefix), "-l", "auto",
    ]
    if threads > 0:
        args += ["-t", str(threads)]
    return args


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcription:
    language: str
    segments: tuple[Segment, ...]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()


def parse_whisper_json(payload: str) -> Transcription:
    """Read whisper.cpp's `-oj` report.

    Offsets are in milliseconds; the human-readable `timestamps` strings are
    ignored, because two representations of the same instant are two things that
    can disagree and one of them has a comma in it.
    """
    data = json.loads(payload or "{}")
    result = data.get("result") or {}
    language = str(result.get("language") or "").strip() or "unknown"
    segments: list[Segment] = []
    for item in data.get("transcription") or []:
        offsets = item.get("offsets") or {}
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(offsets.get("from") or 0) / 1000.0
            end = float(offsets.get("to") or 0) / 1000.0
        except (TypeError, ValueError):
            start = end = 0.0
        segments.append(Segment(start=start, end=end, text=text))
    if not segments:
        raise TranscriptionError(
            "whisper found no speech in that recording"
        )
    return Transcription(language=language, segments=tuple(segments))


# -- turning segments into something readable --------------------------------


@dataclass(frozen=True)
class Paragraph:
    start: float
    text: str


def group_paragraphs(
    segments: Sequence[Segment],
    *,
    gap_s: float = 1.2,
    max_s: float = 48.0,
    max_chars: int = 800,
) -> list[Paragraph]:
    """Merge whisper's ~5-second segments into paragraphs.

    Raw output is one line per segment, which reads as a wall of fragments.
    Whisper's own segment breaks are driven by its decoder window, not by
    meaning, so the useful signal is the pause: a gap between segments is where
    a speaker drew breath or the topic changed.

    Length caps matter as much as the pause -- a lecturer who never pauses for
    breath gets a single paragraph with a timestamp at the top and no way to
    navigate an hour of text.
    """
    paragraphs: list[Paragraph] = []
    current: list[Segment] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(s.text for s in current).strip()
        if text:
            paragraphs.append(Paragraph(start=current[0].start, text=text))
        current.clear()

    for seg in segments:
        if current:
            prev = current[-1]
            long_enough = (seg.end - current[0].start) > max_s
            too_long = len(" ".join(s.text for s in current)) > max_chars
            if (seg.start - prev.end) > gap_s or long_enough or too_long:
                flush()
        current.append(seg)
    flush()
    return paragraphs


def transcript_markdown(paragraphs: Sequence[Paragraph]) -> str:
    """The transcript as markdown: one fenced block per paragraph.

    Fenced, not plain, because a transcript is not prose the reader should be
    asked to hold in their head -- it is a record. The fence keeps it visually
    separate from the summary and stops any `*` or `_` in the speech from being
    read as markup.
    """
    lines: list[str] = []
    for para in paragraphs:
        lines.append(f"`[{stamp(para.start)}]` {para.text}")
        lines.append("")
    return "\n".join(lines).strip()


# -- availability ------------------------------------------------------------


@dataclass(frozen=True)
class Engine:
    """What `available()` found, and what it could not."""

    ready: bool
    cli: str
    models: dict[str, Path]
    ffmpeg: str
    problems: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "cli": self.cli,
            "models": sorted(self.models),
            "ffmpeg": self.ffmpeg,
            "problems": list(self.problems),
        }


#: Where a local whisper.cpp build tends to be, checked in order when
#: NOOKBOARD_WHISPER_CLI is unset. Reported back either way, so the UI never has
#: to guess which engine it is talking to -- and so a wrong one is visible rather
#: than mysterious.
CLI_CANDIDATES = (
    "whisper-cli",
    "~/.local/bin/whisper-cli",
    "~/whisper.cpp/build/bin/whisper-cli",
    "~/.hermes/profiles/school/workspace/whisper.cpp/build/bin/whisper-cli",
    "/usr/local/bin/whisper-cli",
)


def _looks_like_a_path(value: str) -> bool:
    return os.sep in value or bool(os.altsep and os.altsep in value)


def resolve_tool(value: str, *, default: str = "") -> str:
    """The executable for a tool you may have named or pathed, or "".

    A bare name is looked up on PATH; anything containing a separator is taken as
    a path and checked. Both whisper-cli and ffmpeg go through here, because they
    had two implementations of one job and drifted: the CLI resolved a bare name
    on PATH and ffmpeg did not, so a perfectly good ffmpeg was reported missing
    and the whole feature was refused on a machine where it worked.
    """
    wanted = (value or default).strip()
    if not wanted:
        return ""
    if _looks_like_a_path(wanted):
        path = Path(wanted).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else ""
    return shutil.which(wanted) or ""


def resolve_cli(explicit: str = "") -> str:
    """The first candidate that exists, or "". Explicit wins over everything.

    A configured value is NOT checked against the candidate list: if you asked
    for a particular binary, the app should fail loudly about that one rather
    than quietly use a different one and transcribe with an engine you did not
    choose.
    """
    if explicit:
        return explicit
    for candidate in CLI_CANDIDATES:
        found = resolve_tool(candidate)
        if found:
            return found
    return ""


def default_models_dir(cli: str) -> Path:
    """whisper.cpp keeps its models at `<repo>/models`, beside `build/bin/cli`."""
    if cli:
        build_bin = Path(cli).expanduser().resolve().parent
        for candidate in (build_bin.parent.parent / "models", build_bin.parent / "models"):
            if candidate.is_dir():
                return candidate
        return build_bin.parent.parent / "models"
    return Path.home() / "whisper.cpp" / "models"


def resolve_engine(*, cli: str = "", models_dir: str | Path = "", wanted: Sequence[str] = ()) -> Engine:
    """Find the pieces and report them. The one entry point the app calls."""
    resolved_cli = resolve_cli(cli)
    directory = Path(models_dir).expanduser() if models_dir else default_models_dir(resolved_cli)
    return available(cli=resolved_cli, models_dir=directory, wanted=wanted)


def find_model(models_dir: Path, name: str) -> Path | None:
    """`ggml-<name>.bin` in the models directory, if it is there."""
    for candidate in (models_dir / f"ggml-{name}.bin", models_dir / f"{name}.bin"):
        if candidate.is_file():
            return candidate
    return None


def available(
    *, cli: str, models_dir: Path, ffmpeg: str = "ffmpeg", wanted: Sequence[str] = (),
) -> Engine:
    """Look for the pieces, and say plainly which are missing.

    Each failure line names the environment variable that fixes it: people do
    not guess their way to `NOOKBOARD_WHISPER_CLI` from "transcription failed".
    """
    problems: list[str] = []
    cli_path = resolve_tool(cli, default="whisper-cli")
    if not cli_path:
        if cli and _looks_like_a_path(cli):
            problems.append(
                f"whisper-cli is not at {Path(cli).expanduser()} — "
                "check NOOKBOARD_WHISPER_CLI"
            )
        else:
            problems.append(
                "no whisper-cli found — build whisper.cpp, or set NOOKBOARD_WHISPER_CLI"
            )

    models: dict[str, Path] = {}
    for name in wanted:
        found = find_model(models_dir, name)
        if found:
            models[name] = found
    if not models:
        problems.append(
            f"no whisper models in {models_dir} — set NOOKBOARD_WHISPER_MODELS, "
            "or download ggml-small.bin"
        )

    ffmpeg_path = resolve_tool(ffmpeg, default="ffmpeg")
    if not ffmpeg_path:
        if _looks_like_a_path(ffmpeg or ""):
            problems.append(
                f"ffmpeg is not at {Path(ffmpeg).expanduser()} — check the ffmpeg path"
            )
        else:
            problems.append("ffmpeg is not on PATH — it is what reads video and m4a")

    return Engine(
        ready=not problems,
        cli=cli_path,
        models=models,
        ffmpeg=ffmpeg_path,
        problems=tuple(problems),
    )


class TranscriptionError(RuntimeError):
    """Something about the input or the engine made the job impossible.

    Distinct from a crash: the message is written for the person who handed over
    the file, and the runner puts it in the job's `error` for the UI to show.
    """


# -- the note ----------------------------------------------------------------


def provenance(
    *, media: Media | None, source: str, model: str, language: str, kept: bool,
) -> str:
    """One line saying where this came from, in the note itself.

    Deliberately in the body and not in frontmatter: `Note` has a fixed set of
    keys and a new one would not survive a rewrite, so metadata the app cannot
    promise to keep is metadata the app should not claim.
    """
    bits = [f"Source: `{source}`"]
    if media is not None:
        bits.append(media.describe())
    bits.append(f"whisper `{model}`")
    if language and language != "unknown":
        bits.append(language)
    if kept and media is None:
        bits.append("audio kept beside the note")
    return "_" + " · ".join(bits) + "_"


def render_body(*, provenance_line: str, transcript: str) -> str:
    """The note's body: a source line, then the two generated regions.

    Both regions start as placeholders so the note is never a fragment while the
    summary is still being written -- the summary is the slow part, and a note
    that appears with a transcript and then grows a summary is a better
    experience than one that appears empty and fills in.
    """
    from . import sections

    body = provenance_line + "\n\n"
    body += sections.mark(SUMMARY_SCOPE, "start") + "\n"
    body += "_Summarising…_\n"
    body += sections.mark(SUMMARY_SCOPE, "end") + "\n\n"
    body += sections.mark(BODY_SCOPE, "start") + "\n"
    body += transcript + "\n"
    body += sections.mark(BODY_SCOPE, "end") + "\n"
    return body


def _fenced(scope: str, content: str) -> str:
    """`content` wrapped in a scope's markers.

    The markers are part of what `sections.upsert` writes, not just what it looks
    for: it replaces everything from `start` through `end` inclusive, so passing
    bare text deletes the fence and the *next* run appends a second copy instead
    of replacing the first. `daily.render` builds its section the same way.
    """
    from . import sections

    return "\n".join(
        [sections.mark(scope, "start"), "", content.strip(), "", sections.mark(scope, "end")]
    )


def set_summary(body: str, summary: str) -> str:
    """Replace just the summary region."""
    from . import sections

    return sections.upsert(
        body,
        _fenced(SUMMARY_SCOPE, summary.strip() or "_No summary._"),
        start=sections.mark(SUMMARY_SCOPE, "start"),
        end=sections.mark(SUMMARY_SCOPE, "end"),
    )


def set_transcript(body: str, transcript: str) -> str:
    """Replace just the transcript region."""
    from . import sections

    return sections.upsert(
        body,
        _fenced(BODY_SCOPE, transcript.strip()),
        start=sections.mark(BODY_SCOPE, "start"),
        end=sections.mark(BODY_SCOPE, "end"),
    )


def title_for(source: str, day: date) -> str:
    """A human title: the file's own name, dated.

    The note's title is what the user will search for, and `lecture-3` is what
    they called the file. Prefixing the date keeps a term's worth of recordings
    in order without inventing a naming scheme they did not choose.
    """
    stem = Path(source).stem
    cleaned = re.sub(r"[_\s]+", " ", stem).strip() or "Recording"
    return f"{cleaned} · {day.isoformat()}"
