"""Running a transcription: the only part of this feature that touches a
process, a disk or a model.

Rules live in `transcribe` (what the argv is, what the output means) and `ai`
(how a long transcript gets summarised). This module does the I/O and reports
progress honestly while it does it.

Three things about the shape of a job:

**One at a time.** whisper and the summary model share one 8 GB GPU, so two
transcriptions do not finish sooner, they page. Jobs queue and the queue is
visible -- "queued" is a state the UI can say out loud, unlike a spinner.

**Progress is real.** whisper prints `progress = N%` to stderr and it is parsed
rather than approximated, because a lecture is minutes long and a bar that lies
about how far along it is is worse than no bar.

**A job is not persisted, and the note is.** A transcription is minutes of work
on a file that is still on disk, so the honest answer after a restart is "that
job is gone, start it again" -- not a guess that it finished. Once the note
exists it is a file like any other, and the transcript in it is the real output.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from . import ai, proc, sections, transcribe
from .llm import LLMError
from .models import Note, Signifier, Status
from .transcribe import Media, TranscriptionError

#: whisper's stderr progress line, e.g.
#: `whisper_print_progress_callback: progress =  42%`.
_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)\s*%")

#: A GPU query is a subprocess that either answers at once or is not there.
VRAM_TIMEOUT = 10

#: Kept audio goes here, dot-prefixed because `Vault.collections()` lists every
#: directory: a plain `audio/` folder would be offered as an empty notes
#: collection in the editor's dropdown and look like somewhere to put notes.
AUDIO_DIRNAME = ".audio"

#: Fractions of the whole job, so the bar moves once instead of restarting at
#: each stage. Transcribing is the long part by a wide margin -- it gets the
#: middle 80% because that is roughly where the time goes.
_PROBE_END = 0.03
_EXTRACT_END = 0.10
_TRANSCRIBE_END = 0.90


@dataclass
class Job:
    """One transcription, and everything the UI needs to describe it."""

    id: str
    source: str              # what to show: a path, or the name of an upload
    path: Path               # what to read
    model: str
    collection: str
    summarize: bool
    keep: bool = False       # is `path` inside the vault (an upload we saved)?
    #: The GPU could not hold this model, so the work moved to the CPU. Set by
    #: the run rather than by the request, and on the wire: a job that takes ten
    #: times as long should say why without anyone having to ask.
    on_cpu: bool = False
    #: The sentence explaining that, when it happened.
    gpu_note: str | None = None
    #: Summarise an existing transcript note instead of transcribing a file.
    #: A retry of the cheap-but-flaky half should not repeat the expensive half.
    only_summary: bool = False
    state: str = "queued"
    progress: float = 0.0
    message: str = "queued"
    note_id: str | None = None
    error: str | None = None
    media: Media | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None

    @property
    def done(self) -> bool:
        return self.state in ("done", "failed")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "state": self.state,
            "progress": round(self.progress, 3),
            "message": self.message,
            "note_id": self.note_id,
            "error": self.error,
            "model": self.model,
            "summarize": self.summarize,
            # The view hides "re-summarise" on a job that *is* a re-summarise, so
            # this has to be on the wire. Left out, the button never appears.
            "only_summary": self.only_summary,
            "keep": self.keep,
            "on_cpu": self.on_cpu,
            "gpu_note": self.gpu_note,
            "duration_s": self.media.duration_s if self.media else None,
            "started": self.started,
            "finished": self.finished,
            "elapsed_s": round((self.finished or time.time()) - self.started, 1),
        }


class Transcriber:
    """Owns the job table and runs them one at a time."""

    def __init__(
        self,
        *,
        cli: str,
        models_dir: Path,
        default_model: str,
        vault,
        taken_ids: Callable[[], Iterable[str]],
        llm=None,
        audio_dir: Path | None = None,
        vram_probe: Callable[[], transcribe.Vram | None] | None = None,
    ):
        self.cli = cli
        self.models_dir = models_dir
        self.default_model = default_model
        self.vault = vault
        self.taken_ids = taken_ids
        self.llm = llm
        self.audio_dir = audio_dir
        #: `None` turns the check off entirely, which is what a test wants and
        #: what NOOKBOARD_WHISPER_GPU_CHECK=0 gets.
        self._vram_probe = vram_probe
        self._vram_reading: transcribe.Vram | None = None
        self._vram_at: float | None = None
        self.jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    #: A reading is a subprocess and the status view polls. Two seconds is fresh
    #: enough to be true -- a card does not empty and refill inside the first
    #: second of a job -- and stale enough that a poll loop is not a process
    #: launcher.
    _VRAM_TTL = 2.0

    def vram(self) -> transcribe.Vram | None:
        """The card, cached for a moment. `None` when it cannot be asked.

        `None` is not "the GPU is fine": it is "nobody looked", and every caller
        treats it as a reason to leave the job exactly where it was.
        """
        if self._vram_probe is None:
            return None
        now = time.monotonic()
        if self._vram_at is not None and (now - self._vram_at) < self._VRAM_TTL:
            return self._vram_reading
        self._vram_reading = self._vram_probe()
        self._vram_at = now
        return self._vram_reading

    # -- job table ----------------------------------------------------------

    def engine(
        self, wanted: Iterable[str] | None = None, vram: transcribe.Vram | None = None,
    ) -> transcribe.Engine:
        """What is installed, asked fresh so a new build is picked up without a
        restart -- and so the UI never reports a cached "ready".

        The VRAM reading comes in from the caller (`vram()` owns the cache), so
        this stays a lookup of what is on disk and nothing else.
        """
        return transcribe.resolve_engine(
            cli=self.cli, models_dir=self.models_dir,
            wanted=wanted or (self.default_model,),
            vram=vram,
        )

    def submit_resummarise(self, note_id: str) -> Job:
        """A job that only rewrites one note's summary."""
        job = Job(
            id=uuid.uuid4().hex[:12],
            source=note_id,
            path=Path(""),
            model=self.default_model,
            collection="",
            summarize=True,
            only_summary=True,
            note_id=note_id,
        )
        self.jobs[job.id] = job
        asyncio.create_task(self._run_then_release(job))
        return job

    def submit(
        self, *, path: Path, source: str, model: str, collection: str,
        summarize: bool = True, keep: bool = False, on_cpu: bool = False,
    ) -> Job:
        """Register a job and start it. Returns immediately -- this is minutes
        of work, so the caller gets an id and polls."""
        job = Job(
            id=uuid.uuid4().hex[:12],
            source=source,
            path=Path(path),
            model=model or self.default_model,
            collection=collection or transcribe.TRANSCRIPT_COLLECTION,
            summarize=summarize,
            keep=keep,
            on_cpu=on_cpu,
        )
        self.jobs[job.id] = job
        asyncio.create_task(self._run_then_release(job))
        return job

    def recent(self, limit: int = 12) -> list[dict]:
        ordered = sorted(self.jobs.values(), key=lambda j: j.started, reverse=True)
        return [j.as_dict() for j in ordered[:limit]]

    def note_ids(self) -> set[str]:
        return {n.id for n in self.vault.list_all()}

    # -- running ------------------------------------------------------------

    async def _run_then_release(self, job: Job) -> None:
        async with self._lock:
            await self._run(job)

    async def _run(self, job: Job) -> None:
        """The whole pipeline, with every failure landing on the job.

        An exception here is shown to the person who handed over the file, so
        `TranscriptionError` carries a sentence meant for them; anything else is
        reported by type and message, which is the difference between "that file
        has no audio track" and a traceback nobody will read.
        """
        try:
            if job.only_summary:
                await self._resummarise(job)
                job.state = "done"
                job.progress = 1.0
                job.message = "done"
                return
            if not job.path.is_file():
                raise TranscriptionError(f"no such file: {job.path}")
            engine = self.engine()
            model_path = engine.models.get(job.model)
            if model_path is None:
                raise TranscriptionError(
                    f"the '{job.model}' model is not installed"
                    + (f" (have: {', '.join(sorted(engine.models))})" if engine.models else "")
                )
            if not engine.cli:
                raise TranscriptionError("whisper-cli was not found — see the engine line")

            self._decide_device(job, model_path)

            with _temp_dir() as work:
                await self._pipeline(job, engine, model_path, work)
            job.state = "done"
            job.progress = 1.0
            job.message = "done"
        except asyncio.CancelledError:
            job.state = "failed"
            job.error = "cancelled"
            raise
        except TranscriptionError as exc:
            job.state = "failed"
            job.error = str(exc)
            job.message = "failed"
        except Exception as exc:  # noqa: BLE001
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "failed"
        finally:
            job.finished = time.time()

    def _decide_device(self, job: Job, model_path: Path) -> None:
        """GPU or CPU, decided once and said out loud.

        A card that cannot hold the model's weights does not fail this job; it
        moves it to the CPU and records why. Refusing would be a dead end -- the
        work is still wanted, it is just wanted somewhere else -- and the
        alternative, dying six seconds in with a CUDA abort, is what this exists
        to replace.

        The check only runs when the card can actually be asked: no reading is
        not a reason to move work off the GPU, so a machine without `nvidia-smi`
        keeps behaving exactly as it did.
        """
        if job.on_cpu:
            job.gpu_note = "asked for the CPU"
            return
        vram = self.vram()
        if transcribe.fits_on_gpu(vram, model_path):
            return
        # `vram` is not None here: `fits_on_gpu` answers True for a card we could
        # not ask, which is the branch that already returned.
        job.on_cpu = True
        job.gpu_note = transcribe.vram_sentence(vram, job.model, model_path)

    async def _resummarise(self, job: Job) -> None:
        """Rewrite one note's summary from the transcript already in it."""
        note = self.vault.read(job.note_id)
        job.source = f"summary of {note.title}"
        transcript = transcript_of(note.body)
        if not transcript:
            raise TranscriptionError(
                "that note has no transcript region — summarise it from the editor if "
                "the region was deleted"
            )
        await self._summarise(job, transcript)

    async def _pipeline(self, job: Job, engine, model_path: Path, work: Path) -> None:
        job.state = "probing"
        job.message = "reading the file"
        probe = await _exec(
            transcribe.probe_args(job.path, ffprobe=transcribe.ffprobe_for(engine.ffmpeg))
        )
        # `.stdout`, not the CmdResult: `_exec` hands back the whole result and the
        # parser wants the JSON text. Passing the result itself is a TypeError
        # that only appears when a real job runs -- which is how it was found.
        job.media = transcribe.parse_probe(probe.stdout)
        job.progress = _PROBE_END

        audio = work / "audio.wav"
        job.state = "extracting"
        job.message = "extracting audio"
        await _exec(transcribe.extract_args(job.path, audio, ffmpeg=engine.ffmpeg or "ffmpeg"))
        job.progress = _EXTRACT_END

        job.state = "transcribing"
        where = " on the CPU" if job.on_cpu else ""
        job.message = f"transcribing with {job.model}{where}"
        prefix = work / "out"
        result = await _exec(
            transcribe.whisper_args(
                engine.cli, model_path, audio, prefix,
                # whisper.cpp defaults to four threads, and on the CPU path the
                # thread count is the only thing setting the pace: an hour of
                # lecture is the difference between an hour and half of one.
                threads=(os.cpu_count() or 4) if job.on_cpu else 0,
                on_cpu=job.on_cpu,
            ),
            on_line=lambda line: self._on_whisper_line(job, line),
            vram=self.vram(),
            model=job.model,
        )
        report = prefix.with_suffix(".json")
        if not report.is_file():
            raise TranscriptionError(
                "whisper produced no report — " + _last_line(result.stderr)
            )
        transcription = transcribe.parse_whisper_json(report.read_text())
        job.progress = _TRANSCRIBE_END

        paragraphs = transcribe.group_paragraphs(transcription.segments)
        transcript = transcribe.transcript_markdown(paragraphs)
        job.message = f"{len(paragraphs)} paragraphs · writing the note"
        job.note_id = self._write_note(job, transcription, transcript)

        if job.summarize:
            await self._summarise(job, transcript)
        else:
            self._replace_summary(job, "_Not summarised._")

    def _on_whisper_line(self, job: Job, line: str) -> None:
        match = _PROGRESS_RE.search(line or "")
        if match:
            share = min(100, int(match.group(1))) / 100.0
            job.progress = _EXTRACT_END + share * (_TRANSCRIBE_END - _EXTRACT_END)
            where = " on the CPU" if job.on_cpu else ""
            job.message = f"transcribing with {job.model}{where} · {match.group(1)}%"

    # -- the note -----------------------------------------------------------

    def _write_note(self, job: Job, transcription, transcript: str) -> str:
        """Create the note as soon as there is a transcript.

        Deliberately before the summary: the transcript is the expensive part and
        it is already true, while the summary is a model call that may fail. A
        note that appears with the transcript in it and grows a summary a minute
        later is better than one that appears whole or not at all.
        """
        taken = self.note_ids()
        note_id = transcribe.note_id(
            date.today(), transcribe.slugify(Path(job.source).stem), taken
        )
        source = self._source_label(job)
        body = transcribe.render_body(
            provenance_line=transcribe.provenance(
                media=job.media, source=source, model=job.model,
                language=transcription.language, kept=job.keep, on_cpu=job.on_cpu,
            ),
            transcript=transcript,
        )
        note = Note(
            id=note_id,
            collection=job.collection,
            title=transcribe.title_for(job.source, date.today()),
            body=body,
            signifier=Signifier.NOTE,
            status=Status.OPEN,
            dates=[date.today()],
            tags=["transcript"],
            created=date.today(),
        )
        self.vault.write(note)
        return note_id

    def _source_label(self, job: Job) -> str:
        """How the note names where the audio is.

        A path the user typed is left as it is -- it is still their file, in
        their directory. An upload was copied into the vault, so the note names
        the copy, which is the one that will still exist tomorrow.
        """
        if job.keep and self.audio_dir is not None:
            with contextlib.suppress(ValueError):
                return str(Path(job.path).relative_to(self.vault.root))
        return job.source

    def _replace_summary(self, job: Job, summary: str) -> None:
        """Rewrite the note's summary region, leaving everything else alone.

        `dataclasses.replace` rather than a hand-built Note: the vault attaches
        the file it read the note from (`source_rel`), and rebuilding from the
        fields I happen to know about would drop that and relocate the file into
        a collection directory.
        """
        note = self.vault.read(job.note_id)
        self.vault.write(
            replace(note, body=transcribe.set_summary(note.body, summary))
        )

    def _stage(self, job: Job):
        """Progress for the summary pass, as a fraction of the job's last tenth."""

        def on_stage(message: str, fraction: float) -> None:
            job.message = message
            job.progress = _TRANSCRIBE_END + (1 - _TRANSCRIBE_END) * fraction

        return on_stage

    async def _summarise(self, job: Job, transcript: str) -> None:
        """Summarise the transcript. Never fails the job.

        The transcript in the note is the real output and it is already written;
        a model that is down costs the note its summary and nothing else. The
        reason is left in the note so the absence is explained rather than
        mysterious, and the summary can be retried on its own without touching
        the recording again.
        """
        if self.llm is None:
            if job.only_summary:
                raise TranscriptionError("no model is configured (see NOOKBOARD_LLM_MODEL)")
            self._replace_summary(job, "_No summary — no model is configured._")
            return
        job.state = "summarising"
        try:
            summary = await summarise_transcript(
                self.llm, transcript, on_stage=self._stage(job)
            )
        except LLMError as exc:
            self._failed_summary(job, exc)
            return
        self._replace_summary(job, summary or "_Nothing in this recording needed noting._")

    def _failed_summary(self, job: Job, exc: Exception) -> None:
        """A summary that failed, reported for the job that actually ran.

        For a fresh transcription the transcript is already written and true, so
        the note keeps it, says why there is no summary, and the job is done --
        the expensive part succeeded. For a re-summarise the summary *is* the
        whole job: calling that done would be a lie, and the note's existing
        summary is left exactly as it was rather than replaced with an apology
        for a run that never landed.
        """
        if job.only_summary:
            raise TranscriptionError(f"the summary failed: {exc}") from exc
        job.error = f"transcribed, but the summary failed: {exc}"
        self._replace_summary(
            job,
            f"_No summary — the model was unreachable ({exc}). The transcript "
            "below is complete; the summary can be retried on its own._",
        )


async def summarise_transcript(llm, transcript: str, *, on_stage=None) -> str:
    """Summarise a transcript in parts, then combine the parts into one summary.

    Chunked because an hour of speech is far past what a local model handles in
    one pass, and combined because the point is one summary and not fifteen.
    Returns markdown -- possibly empty, when the recording had nothing worth
    noting. Raises `LLMError`: the caller decides what an unreachable model costs,
    and it costs nothing to the transcript that is already in the note.
    """
    chunks = ai.chunk_text(transcript)
    notes: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        if on_stage:
            on_stage(
                f"summarising part {index} of {len(chunks)}",
                (index - 1) / max(1, len(chunks)),
            )
        reply = await llm.complete(
            ai.build_transcript_summary_messages(chunk, index=index, total=len(chunks))
        )
        reply = (reply or "").strip()
        if reply and reply != "-":
            notes.append(reply)
    if not notes:
        return ""
    if on_stage:
        on_stage("writing the summary", 0.95)
    return ai.parse_transcript_summary(
        await llm.complete(ai.build_transcript_reduce_messages(notes))
    )


def re_summarise_body(body: str, summary: str) -> str:
    """Replace the summary of an existing transcript note."""
    return transcribe.set_summary(body, summary)


def transcript_of(body: str) -> str | None:
    """The transcript back out of a note, or None if it is not a transcript."""
    return sections.read(
        body,
        start=sections.mark(transcribe.BODY_SCOPE, "start"),
        end=sections.mark(transcribe.BODY_SCOPE, "end"),
    )


# -- subprocess plumbing -----------------------------------------------------


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


async def _exec(
    args: list[str], *, on_line: Callable[[str], None] | None = None,
    vram: transcribe.Vram | None = None, model: str = "",
) -> CmdResult:
    """Run a command, streaming its stderr lines to `on_line`.

    stderr is where both ffmpeg's errors and whisper's progress go, so it is read
    line by line as it arrives rather than collected at the end -- that is the
    difference between a progress bar and an hour of silence followed by a
    failure.

    `vram` and `model` are for the explanation only: they are the numbers a
    failure sentence wants, and this is the layer that still has the command's
    own output in hand when it has to write one.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    out_lines: list[str] = []
    err_lines: list[str] = []

    async def drain(stream, sink: list[str], callback=None):
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            sink.append(line)
            if callback is not None:
                callback(line)

    await asyncio.gather(
        drain(proc.stdout, out_lines),
        drain(proc.stderr, err_lines, on_line),
    )
    code = await proc.wait()
    if code != 0:
        raise TranscriptionError(
            _explain_failure(args[0], code, err_lines, vram=vram, model=model)
        )
    return CmdResult(returncode=code, stdout="\n".join(out_lines), stderr="\n".join(err_lines))


def probe_vram() -> transcribe.Vram | None:
    """Ask `nvidia-smi` what the card looks like. `None` when it cannot be asked.

    Through `proc.run`, which is the same never-becomes-an-exception contract the
    workspace and history readers use. On a machine with no `nvidia-smi`, a
    stopped driver, or a card that is not NVIDIA, this is a check that does not
    happen -- not a failure worth reporting, and not a reason to change where a
    job runs.
    """
    if shutil.which("nvidia-smi") is None:
        return None
    out, _, code = proc.run(transcribe.vram_args(), timeout=VRAM_TIMEOUT)
    if code != 0:
        return None
    apps, _, apps_code = proc.run(transcribe.vram_apps_args(), timeout=VRAM_TIMEOUT)
    return transcribe.parse_vram(out, apps if apps_code == 0 else "")


def _last_line(text: str) -> str:
    for line in reversed((text or "").splitlines()):
        if line.strip():
            return line.strip()
    return "no output"


#: The negative exit codes a killed process reports, named. "exit -6" was the
#: whole of what nookboard had to say about the abort that started all this, and
#: a number is not something a person can act on.
#:
#: Names only, no gloss: these are read inside parentheses (`whisper-cli failed
#: (killed by SIGSEGV): ...`), and a second set of parentheses in there is
#: unreadable. Whatever the signal means belongs in the sentence, where the
#: output that produced it is quoted anyway.
_SIGNALS = {-6: "SIGABRT", -9: "SIGKILL", -11: "SIGSEGV", -15: "SIGTERM"}

#: What a card with no room left says, in the words ggml and CUDA actually print.
_OUT_OF_MEMORY = (
    "out of memory",
    "cuda_error_out_of_memory",
    "cudamalloc failed",
)


def _exit_words(code: int) -> str:
    """`exit 1`, or the signal by name when one killed the process."""
    if code < 0:
        return f"killed by {_SIGNALS.get(code, f'signal {-code}')}"
    return f"exit {code}"


def _looks_out_of_memory(err_lines: list[str]) -> bool:
    text = " ".join(err_lines).lower()
    return any(phrase in text for phrase in _OUT_OF_MEMORY)


def _oom_detail(vram: transcribe.Vram | None, model: str) -> str:
    """The numbers that make an out-of-memory sentence actionable.

    When there is no reading, the sentence still says what to do. When there is
    one it says who has the card, which is the fact that turns "out of memory"
    into "stop the model server or pick a smaller model".
    """
    named = f" for '{model}'" if model else ""
    if vram is None:
        return f"{named} — try a smaller model, or a run with --no-gpu"
    held = vram.held_by()
    who = f" ({held} is holding it)" if held else ""
    return f"{named} — {vram.free_line()}{who}; try a smaller model"


def _explain_failure(
    tool: str, code: int, err_lines: list[str], *,
    vram: transcribe.Vram | None = None, model: str = "",
) -> str:
    """A sentence for the person, from the tail of the tool's own complaint.

    The sentence leads and the tool's own words stay underneath it: the sentence
    is for whoever handed over the file, the tail is the evidence for anyone who
    wants to check it. Dropping either is a loss.
    """
    tail = " · ".join(l for l in err_lines[-3:] if l.strip()) or "no output"
    name = Path(tool).name
    if name.startswith("ffmpeg"):
        return f"ffmpeg could not read that file ({_exit_words(code)}): {tail}"
    if name.startswith("ffprobe"):
        return f"ffprobe could not read that file ({_exit_words(code)}): {tail}"
    if _looks_out_of_memory(err_lines):
        return (
            f"whisper ran out of GPU memory{_oom_detail(vram, model)}. "
            f"It said: {tail}"
        )
    return f"{name} failed ({_exit_words(code)}): {tail}"


@contextlib.contextmanager
def _temp_dir():
    """A working directory for the wav and the report, always cleaned up.

    An hour of 16 kHz mono wav is about 115 MB, which is fine in the system temp
    directory and is deleted the moment the job ends -- including when it fails,
    since a failed job that leaves a half-gigabyte wav behind is a bug the user
    only discovers when the disk is full.
    """
    path = Path(tempfile.mkdtemp(prefix="nookboard-transcribe-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
