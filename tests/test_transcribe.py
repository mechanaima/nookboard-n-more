"""Transcription: what the engine is given, and what comes back out.

The fixtures here are the shapes the real tools produce, not invented ones:
whisper.cpp's `-oj` report and ffprobe's JSON are both spelled out in full so a
parser change that would break against the actual binary breaks here first.
"""

import shutil
from datetime import date
from pathlib import Path

import pytest

from app import transcribe, transcribe_run
from app.transcribe import (
    Media, Paragraph, Segment, TranscriptionError,
)

DAY = date(2026, 9, 24)

#: From `whisper-cli -oj`, trimmed to the keys we read but keeping the nesting.
WHISPER_JSON = """
{
  "systeminfo": "AVX = 1 | CUDA = 1",
  "model": {"type": "small", "multilingual": true, "mels": 80},
  "params": {"model": "models/ggml-small.bin", "language": "en", "translate": false},
  "result": {"language": "en"},
  "transcription": [
    {"timestamps": {"from": "00:00:00,000", "to": "00:00:02,500"},
     "offsets": {"from": 0, "to": 2500},
     "text": " And so my fellow Americans,"},
    {"timestamps": {"from": "00:00:02,500", "to": "00:00:07,000"},
     "offsets": {"from": 2500, "to": 7000},
     "text": " ask not what your country can do for you,"},
    {"timestamps": {"from": "00:00:09,000", "to": "00:00:11,000"},
     "offsets": {"from": 9000, "to": 11000},
     "text": " ask what you can do for your country."}
  ]
}
"""

#: From `ffprobe -print_format json -show_format -show_streams`.
PROBE_JSON = """
{
  "streams": [
    {"codec_type": "video", "codec_name": "h264", "width": 1280},
    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2}
  ],
  "format": {"filename": "/tmp/lecture-3.m4a", "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
             "duration": "3125.40"}
}
"""


# --- ids --------------------------------------------------------------------

def test_the_first_id_is_bare_and_only_later_ones_are_numbered():
    """Asserted directly, not by comparing one id to the next.

    A test that only checks `second == first + "-2"` passes just as happily when
    every id is shifted -- which is how the templates shipped with every first
    application coming back as `(2)`.
    """
    # Named by literal, on a slug with no digits of its own: `"-2" not in first`
    # looked like a bareness check and was really checking the date, which
    # contains "-2" itself.
    first = transcribe.note_id(DAY, "algebra")
    assert first == "transcript-2026-09-24-algebra"

    second = transcribe.note_id(DAY, "algebra", taken={first})
    assert second == "transcript-2026-09-24-algebra-2"
    third = transcribe.note_id(DAY, "algebra", taken={first, second})
    assert third == "transcript-2026-09-24-algebra-3"


def test_a_gap_in_the_suffixes_does_not_reuse_a_taken_id():
    taken = {"transcript-2026-09-24-a", "transcript-2026-09-24-a-2"}
    assert transcribe.note_id(DAY, "a", taken) == "transcript-2026-09-24-a-3"


def test_the_id_prefix_matches_the_predicate_that_guards_it():
    """The derived id against the predicate, never a literal.

    `weekly.note_id` emitted `week-` while the regex expected `weekly-`, so the
    guard matched nothing and the note was free to become a board card. This
    asserts what the code *produces*.
    """
    from app import models

    assert models.is_transcript_note_id(transcribe.note_id(DAY, "anything"))
    assert models.is_generated_note_id(transcribe.note_id(DAY, "anything"))


# --- slugs and stamps -------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Lecture 3 — Recursion.m4a", "lecture-3-recursion-m4a"),
        ("  spaced  out  ", "spaced-out"),
        ("Café résumé", "cafe-resume"),
        ("!!!", "recording"),
        ("", "recording"),
        ("日本語", "recording"),
    ],
)
def test_slugify(raw, expected):
    assert transcribe.slugify(raw) == expected


def test_slugify_caps_length():
    assert len(transcribe.slugify("word " * 40, limit=20)) <= 20


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0:00"), (4.9, "0:04"), (61, "1:01"), (3599, "59:59"),
     (3600, "1:00:00"), (7325, "2:02:05"), (-5, "0:00")],
)
def test_stamp(seconds, expected):
    assert transcribe.stamp(seconds) == expected


def test_an_hour_long_recording_gets_hours_in_its_stamps():
    """The reason `stamp` is not just `m:ss`: a lecture is over an hour, and
    `62:05` in a two-hour recording reads as broken arithmetic."""
    assert transcribe.stamp(3725).startswith("1:")


# --- probing ----------------------------------------------------------------

def test_parse_probe_reads_the_audio_stream_not_the_video_one():
    media = transcribe.parse_probe(PROBE_JSON)
    assert media.audio_codec == "aac"
    assert media.channels == 2
    assert media.container == "mov"
    assert media.duration_s == pytest.approx(3125.40)
    assert "52:05" in media.describe()


def test_a_file_with_no_audio_is_refused_by_name():
    """A silent screen recording is the common case. Transcribing it would
    produce an empty note reported as a success."""
    silent = PROBE_JSON.replace('"codec_type": "audio"', '"codec_type": "data"')
    with pytest.raises(TranscriptionError, match="no audio track"):
        transcribe.parse_probe(silent)


def test_probe_arrays_ask_for_json_and_name_the_input(tmp_path):
    args = transcribe.probe_args(tmp_path / "a.m4a")
    assert args[0] == "ffprobe"
    assert "-print_format" in args and "json" in args
    assert str(tmp_path / "a.m4a") in args


def test_extract_drops_video_and_asks_for_16k_mono(tmp_path):
    """`-vn` is why a video file needs no special path anywhere else: the only
    thing downstream ever sees is a 16 kHz mono wav."""
    args = transcribe.extract_args(tmp_path / "lecture.mp4", tmp_path / "out.wav")
    assert args[0] == "ffmpeg"
    assert "-vn" in args
    assert args[args.index("-ac") + 1] == "1"
    assert args[args.index("-ar") + 1] == "16000"


def test_whisper_is_asked_for_its_json_report(tmp_path):
    args = transcribe.whisper_args(
        "/w/whisper-cli", tmp_path / "ggml-small.bin",
        tmp_path / "a.wav", tmp_path / "out",
    )
    assert args[0] == "/w/whisper-cli"
    assert "-oj" in args
    assert args[args.index("-of") + 1] == str(tmp_path / "out")


# --- parsing the report -----------------------------------------------------

def test_parse_whisper_json_reads_offsets_as_seconds():
    result = transcribe.parse_whisper_json(WHISPER_JSON)
    assert result.language == "en"
    assert len(result.segments) == 3
    assert result.segments[0].start == 0.0
    assert result.segments[2].start == pytest.approx(9.0)
    assert result.segments[1].text.startswith("ask not")


def test_silence_is_refused_rather_than_written_as_an_empty_note():
    empty = '{"result": {"language": "en"}, "transcription": []}'
    with pytest.raises(TranscriptionError, match="no speech"):
        transcribe.parse_whisper_json(empty)


def test_segments_with_no_text_are_skipped():
    payload = """
    {"result": {"language": "en"}, "transcription": [
      {"offsets": {"from": 0, "to": 100}, "text": "   "},
      {"offsets": {"from": 100, "to": 200}, "text": "real words"}
    ]}
    """
    result = transcribe.parse_whisper_json(payload)
    assert [s.text for s in result.segments] == ["real words"]


# --- paragraphs -------------------------------------------------------------

def _seg(start, end, text):
    return Segment(start=start, end=end, text=text)


def test_a_pause_starts_a_new_paragraph():
    paras = transcribe.group_paragraphs([
        _seg(0, 5, "one"), _seg(5, 9, "two"), _seg(11, 14, "three"),
    ])
    assert len(paras) == 2
    assert paras[0].text == "one two"
    assert paras[0].start == 0
    assert paras[1].text == "three"
    assert paras[1].start == 11


def test_an_unbroken_monologue_is_still_cut_into_navigable_paragraphs():
    """The length cap is not cosmetic: whisper only breaks on its decoder
    window, so a lecturer who never pauses would otherwise produce one
    timestamp for an hour of text and no way to find anything in it."""
    segs = [_seg(i * 5, i * 5 + 5, "word") for i in range(40)]  # 200s, no gaps
    paras = transcribe.group_paragraphs(segs, max_s=48.0)
    assert len(paras) > 1
    assert all(p.start < 200 for p in paras)


def test_a_long_paragraph_is_also_capped_by_length():
    segs = [_seg(i * 5, i * 5 + 5, "word " * 40) for i in range(20)]
    paras = transcribe.group_paragraphs(segs, max_s=10_000, max_chars=500)
    assert len(paras) > 1


def test_grouping_nothing_is_not_an_error():
    assert transcribe.group_paragraphs([]) == []


def test_transcript_markdown_stamps_every_paragraph():
    md = transcribe.transcript_markdown([
        Paragraph(start=0, text="hello"), Paragraph(start=75, text="world"),
    ])
    assert "`[0:00]` hello" in md
    assert "`[1:15]` world" in md


# --- availability -----------------------------------------------------------

def _fake_tool(path):
    """A stand-in binary: present, and executable. `resolve_tool` insists on the
    exec bit, and a text file with no chmod is not a binary."""
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _engine(tmp_path, *, cli=True, models=("small",), ffmpeg=True):
    cli_path = tmp_path / "whisper-cli"
    if cli:
        _fake_tool(cli_path)
    models_dir = tmp_path / "models"
    models_dir.mkdir(exist_ok=True)
    for name in models:
        (models_dir / f"ggml-{name}.bin").write_bytes(b"x" * 16)
    # `ffmpeg` is True (make one), False (none), or an explicit path -- which is
    # how a test asks for "configured but wrong", and it has to stay wrong. The
    # first version of this treated the argument as a boolean and created a real
    # file even when a bogus path was passed in.
    if ffmpeg is True:
        ffmpeg_arg = str(_fake_tool(tmp_path / "ffmpeg"))
    else:
        ffmpeg_arg = str(ffmpeg) if ffmpeg else ""
    return transcribe.available(
        cli=str(cli_path) if cli else "",
        models_dir=models_dir,
        ffmpeg=ffmpeg_arg,
        wanted=["small", "medium"],
    )


def test_a_complete_install_is_ready(tmp_path):
    engine = _engine(tmp_path)
    assert engine.ready
    assert engine.cli.endswith("whisper-cli")
    assert "small" in engine.models
    assert engine.problems == ()


def test_only_the_models_that_are_present_are_offered(tmp_path):
    engine = _engine(tmp_path, models=("small",))
    assert sorted(engine.models) == ["small"]


def test_each_missing_piece_is_named_with_the_variable_that_fixes_it(tmp_path):
    """A bogus ffmpeg path, not an empty one: empty falls back to PATH, where
    ffmpeg really is installed."""
    engine = _engine(tmp_path, cli=False, models=(), ffmpeg=str(tmp_path / "nope-ffmpeg"))
    assert not engine.ready
    joined = " ".join(engine.problems)
    assert "NOOKBOARD_WHISPER_CLI" in joined
    assert "NOOKBOARD_WHISPER_MODELS" in joined
    assert "ffmpeg" in joined


def test_a_cli_path_that_does_not_exist_is_reported_as_such(tmp_path):
    """Configured but wrong, which is a different failure from unset: the first
    is a typo to fix, the second is a thing to install."""
    engine = transcribe.available(
        cli=str(tmp_path / "nope" / "whisper-cli"),
        models_dir=tmp_path / "models",
        ffmpeg="ffmpeg",
        wanted=["small"],
    )
    assert not engine.ready
    assert any("is not at" in p for p in engine.problems)


# --- the note ---------------------------------------------------------------

def test_the_body_starts_with_both_regions_fenced():
    body = transcribe.render_body(provenance_line="_Source: x_", transcript="hi")
    assert "<!-- nookboard:transcript-summary:start -->" in body
    assert "<!-- nookboard:transcript-body:start -->" in body
    assert body.index("transcript-summary:start") < body.index("transcript-body:start")


def test_re_summarising_leaves_the_transcript_and_your_own_text_alone():
    """The property the fence exists for, and the one that is worth a test:
    everything this program did not write survives."""
    body = transcribe.render_body(provenance_line="_Source: x_", transcript="first transcript")
    body = transcribe.set_summary(body, "first summary")
    body = body.rstrip("\n") + "\n\nMy own note about this lecture.\n"
    body = transcribe.set_summary(body, "second summary")

    assert "second summary" in body
    assert "first summary" not in body
    assert "first transcript" in body
    assert "My own note about this lecture." in body


def test_re_transcribing_replaces_only_the_transcript():
    body = transcribe.render_body(provenance_line="_Source: x_", transcript="old text")
    body = transcribe.set_summary(body, "the summary")
    body = transcribe.set_transcript(body, "new text")
    assert "new text" in body
    assert "old text" not in body
    assert "the summary" in body


def test_an_empty_summary_says_so_instead_of_leaving_a_hole():
    body = transcribe.set_summary(
        transcribe.render_body(provenance_line="_S_", transcript="t"), "   "
    )
    assert "No summary" in body


def test_provenance_names_the_source_the_model_and_the_length():
    line = transcribe.provenance(
        media=Media(path=__import__("pathlib").Path("/tmp/a.m4a"), duration_s=3125.4,
                    container="mov", audio_codec="aac", sample_rate=48000, channels=2),
        source="/tmp/a.m4a", model="small", language="en", kept=False,
    )
    assert "/tmp/a.m4a" in line
    assert "small" in line
    assert "52:05" in line


def test_provenance_omits_a_language_it_does_not_know():
    line = transcribe.provenance(
        media=None, source="rec.webm", model="small", language="unknown", kept=True,
    )
    assert "unknown" not in line


def test_the_title_is_the_filename_dated():
    assert transcribe.title_for("/x/Lecture 3 — Recursion.m4a", DAY) == (
        "Lecture 3 — Recursion · 2026-09-24"
    )
    assert transcribe.title_for("/x/__.wav", DAY).startswith("Recording")


# --- finding the tools ------------------------------------------------------
#
# This is where a real bug lived. whisper-cli resolved a bare name on PATH and
# ffmpeg did not, so on a machine with a working ffmpeg the engine reported
# "ffmpeg is not at ffmpeg" and refused every job. The end-to-end run caught it;
# these pin it so it cannot come back.

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")
def test_a_bare_tool_name_is_looked_up_on_path():
    assert transcribe.resolve_tool("ffmpeg") == shutil.which("ffmpeg")
    assert transcribe.resolve_tool("", default="ffmpeg") == shutil.which("ffmpeg")


def test_a_tool_that_is_not_there_resolves_to_nothing():
    assert transcribe.resolve_tool("definitely-not-a-real-tool-9c3f") == ""
    assert transcribe.resolve_tool("") == ""
    assert transcribe.resolve_tool("", default="") == ""


def test_a_configured_path_is_checked_rather_than_looked_up(tmp_path):
    real = _fake_tool(tmp_path / "my-ffmpeg")
    assert transcribe.resolve_tool(str(real)) == str(real)
    assert transcribe.resolve_tool(str(tmp_path / "missing")) == ""


def test_a_missing_configured_path_says_so_instead_of_blaming_the_path():
    engine = transcribe.available(
        cli="/nope/whisper-cli", models_dir=Path("/nope"), ffmpeg="/nope/ffmpeg",
        wanted=["small"],
    )
    joined = " · ".join(engine.problems)
    assert "/nope/whisper-cli" in joined
    assert "/nope/ffmpeg" in joined
    assert not engine.ready


def test_ffprobe_is_sought_beside_a_resolved_ffmpeg(tmp_path):
    """The engine line names a binary; that binary should be the one that runs."""
    assert transcribe.ffprobe_for("ffmpeg") == "ffprobe"
    beside = _fake_tool(tmp_path / "ffprobe")
    ffmpeg = _fake_tool(tmp_path / "ffmpeg")
    assert transcribe.ffprobe_for(str(ffmpeg)) == str(beside)


def test_the_tools_get_the_names_the_engine_resolved():
    """A bare name through the arguments means the PATH's ffmpeg runs while the
    engine line reports a different one."""
    args = transcribe.probe_args(Path("/x/a.wav"), ffprobe="/opt/bin/ffprobe")
    assert args[0] == "/opt/bin/ffprobe"
    args = transcribe.extract_args(Path("/x/a.mp4"), Path("/tmp/a.wav"), ffmpeg="/opt/bin/ffmpeg")
    assert args[0] == "/opt/bin/ffmpeg"


# --- the card, and what a full one costs ------------------------------------

#: What `nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv,
#: noheader,nounits` and the `--query-compute-apps` call beside it really print,
#: copied from the run on 25 September 2026 rather than composed here.
GPU_CSV = "8151, 7103, 644"
APPS_CSV = "llama-server, 7080"

#: And the tail whisper.cpp printed while that was true: the abort that started
#: this. `ggml-cuda.cu:108` in it is the GGML_ABORT inside `ggml_cuda_error`,
#: which is why the obvious line to read is not the one that failed.
OOM_TAIL = [
    "CUDA error: out of memory",
    "  current device: 0, in function stream at "
    "/home/irving/.hermes/profiles/school/workspace/whisper.cpp/ggml/src/ggml-cuda/common.cuh:1492",
    "  cudaStreamCreateWithFlags(&streams[device][stream], 0x01)",
]


def _sized_model(tmp_path, name="small", mib=465):
    """A model file of the size it would really be, and none of the contents.

    Sparse on purpose: the only thing anything asks of it is `st_size`, and
    writing half a gigabyte of filler to answer a question about a number would
    be absurd. It is *not* zero-sized -- the check under test compares this
    number against the card, and an empty stand-in makes every card look roomy.
    """
    path = tmp_path / f"ggml-{name}.bin"
    with path.open("wb") as fh:
        fh.truncate(mib * 1024 * 1024)
    return path


def test_a_reading_is_read_from_the_numbers_nvidia_smi_prints():
    vram = transcribe.parse_vram(GPU_CSV, APPS_CSV)
    assert (vram.total_mib, vram.used_mib, vram.free_mib) == (8151, 7103, 644)
    assert vram.holders == (("llama-server", 7080),)
    assert vram.free_line() == "0.6 of 8.0 GiB free"
    assert vram.held_by() == "llama-server 6.9 GiB"


def test_the_biggest_holder_is_named_first_and_by_its_own_name():
    """`nvidia-smi` reports the process as the path it was started from, and a
    sentence that says `.../build/bin/llama-server 6.9 GiB is holding it` is a
    sentence nobody reads. The basename is the thing a person would stop."""
    vram = transcribe.parse_vram(
        GPU_CSV,
        "/home/irving/projects/prismml-llama.cpp/build/bin/llama-server, 7064\n"
        "small-thing, 40",
    )
    assert [name for name, _ in vram.holders] == ["llama-server", "small-thing"]
    assert vram.held_by() == "llama-server 6.9 GiB, small-thing 0.0 GiB"
    # A name that was never a path is left exactly as it is.
    plain = transcribe.parse_vram(GPU_CSV, "python3, 512")
    assert plain.holders == (("python3", 512),)


def test_a_card_we_cannot_ask_is_not_an_error():
    """No `nvidia-smi`, an AMD card, a driver that answers in prose: a check that
    does not happen. It must not raise, and it must not claim a card either --
    the difference between "nobody looked" and "there is room" is what keeps a
    job on the GPU on a machine with no NVIDIA card at all."""
    assert transcribe.parse_vram("") is None
    assert transcribe.parse_vram("No devices were found") is None
    assert transcribe.parse_vram("memory.total [MiB], memory.used [MiB]") is None
    assert transcribe.parse_vram("n/a, n/a, n/a") is None
    # A readable reading with no processes listed is a reading, not a failure.
    assert transcribe.parse_vram("8151, 15, 7732", "").holders == ()


def test_the_reading_that_actually_failed_is_a_cpu_run(tmp_path):
    """The case this was written from, and the reason the reserve is part of the
    question: 644 MiB free and a 465 MiB `small` is *not* a fit -- asked the
    narrow way (`free >= weights`) it would have said yes and aborted again."""
    model = _sized_model(tmp_path, "small", mib=465)
    busy = transcribe.parse_vram(GPU_CSV, APPS_CSV)
    assert transcribe.weights_mib(model) == 465
    assert transcribe.fits_on_gpu(busy, model) is False
    assert transcribe.fits_on_gpu(transcribe.parse_vram("8151, 15, 7732"), model) is True
    # Nobody looked: leave the job where it was.
    assert transcribe.fits_on_gpu(None, model) is True


def test_a_smaller_model_is_what_fits_on_a_busy_card(tmp_path):
    """Which is the whole reason the ladder grows downwards."""
    busy = transcribe.parse_vram(GPU_CSV, APPS_CSV)
    assert transcribe.fits_on_gpu(busy, _sized_model(tmp_path, "tiny", mib=75)) is True
    assert transcribe.fits_on_gpu(busy, _sized_model(tmp_path, "medium", mib=1533)) is False


def test_the_ladder_is_smallest_first_and_the_default_did_not_move():
    assert transcribe.MODEL_CHOICES == ("tiny", "base", "small", "medium")


def test_the_cpu_sentence_names_who_has_the_card(tmp_path):
    model = _sized_model(tmp_path, "small")
    said = transcribe.vram_sentence(
        transcribe.parse_vram(GPU_CSV, APPS_CSV), "small", model
    )
    assert "0.6 of 8.0 GiB free" in said
    assert "llama-server" in said
    assert "running on the CPU instead" in said
    # A card with nothing else on it still gets a sentence, just a shorter one.
    quiet = transcribe.vram_sentence(transcribe.parse_vram("8151, 8151, 0"), "small", model)
    assert "holding it" not in quiet
    assert "721" in quiet, "465 MiB of weights and the 256 MiB reserve"


def test_a_card_that_will_not_hold_it_says_so_before_the_job(tmp_path):
    """A warning, not a refusal: the engine is ready and the job runs, on the
    CPU. Said early because the alternative is finding out from how long it took."""
    model = _sized_model(tmp_path, "small")
    busy = transcribe.parse_vram(GPU_CSV, APPS_CSV)
    warning = transcribe.vram_warning(busy, "small", model)
    assert warning is not None
    assert "will run on the CPU" in warning
    assert "721" in warning, "the reserve it was measured against"
    # It is rendered next to `vram`, so it must not restate the card: the two
    # sentences would otherwise sit on one line saying the same thing.
    assert "llama-server" not in warning
    assert "GiB free" not in warning
    roomy = transcribe.parse_vram("8151, 15, 7732")
    assert transcribe.vram_warning(roomy, "small", model) is None
    assert transcribe.vram_warning(None, "small", model) is None


def test_the_engine_reports_the_card_and_warns_about_the_default_model(tmp_path):
    """The default model is the one the form starts on, so it is the one whose
    fit is worth a sentence. A full card is not a broken install."""
    cli = _fake_tool(tmp_path / "whisper-cli")
    models = tmp_path / "models"
    models.mkdir()
    _sized_model(models, "small")
    engine = transcribe.available(
        cli=str(cli), models_dir=models, ffmpeg="ffmpeg", wanted=["small"],
        vram=transcribe.parse_vram(GPU_CSV, APPS_CSV),
    )
    assert engine.ready is True
    assert engine.problems == ()
    assert engine.vram.free_mib == 644
    assert len(engine.warnings) == 1 and "small" in engine.warnings[0]
    assert engine.as_dict()["vram"]["free_mib"] == 644

    # And with no reading at all, nothing is claimed either way.
    blind = transcribe.available(
        cli=str(cli), models_dir=models, ffmpeg="ffmpeg", wanted=["small"],
    )
    assert blind.vram is None
    assert blind.warnings == ()
    assert blind.as_dict()["vram"] is None


def test_the_cpu_run_is_the_same_binary_told_to_leave_the_card_alone(tmp_path):
    """One install serves both, so a busy card needs no second binary to keep in
    step with the first."""
    model = tmp_path / "ggml-small.bin"
    args = transcribe.whisper_args(
        "/w/whisper-cli", model, tmp_path / "a.wav", tmp_path / "out", on_cpu=True,
    )
    assert "--no-gpu" in args
    on_gpu = transcribe.whisper_args(
        "/w/whisper-cli", model, tmp_path / "a.wav", tmp_path / "out",
    )
    assert "--no-gpu" not in on_gpu
    # On that path the thread count is the only thing setting the pace.
    threaded = transcribe.whisper_args(
        "/w/whisper-cli", model, tmp_path / "a.wav", tmp_path / "out",
        threads=8, on_cpu=True,
    )
    assert threaded[threaded.index("-t") + 1] == "8"


def test_the_note_says_when_it_was_made_on_the_cpu():
    """A transcript that took forty minutes because the card was busy should say
    so in the note it lands in, and the ordinary run stays as quiet as it was."""
    def line(**extra):
        return transcribe.provenance(
            media=None, source="/x/a.m4a", model="small", language="en",
            kept=False, **extra,
        )

    assert "CPU" not in line()
    assert "whisper `small` on the CPU" in line(on_cpu=True)


def test_the_cuda_abort_that_started_this_is_a_sentence_now():
    said = transcribe_run._explain_failure(
        "/w/whisper-cli", -6, OOM_TAIL,
        vram=transcribe.parse_vram(GPU_CSV, APPS_CSV), model="small",
    )
    assert said.startswith("whisper ran out of GPU memory for 'small'")
    assert "0.6 of 8.0 GiB free" in said
    assert "llama-server" in said
    # The tool's own words stay underneath: the sentence is for whoever handed
    # over the file, the tail is the evidence for anyone checking the sentence.
    assert "cudaStreamCreateWithFlags" in said
    assert "out of memory" in said


def test_an_out_of_memory_with_no_reading_still_says_what_to_do():
    said = transcribe_run._explain_failure("/w/whisper-cli", -6, OOM_TAIL, model="small")
    assert "GPU memory" in said
    assert "smaller model" in said


def test_a_signal_is_named_and_an_ordinary_failure_is_left_alone():
    """`-6` is what the abort looked like from outside, and "exit -6" is a number
    nobody can act on. Everything without a signal keeps the sentence it had."""
    said = transcribe_run._explain_failure("/w/whisper-cli", -6, ["something else"])
    assert "killed by SIGABRT" in said
    assert "exit -6" not in said
    killed = transcribe_run._explain_failure("/w/whisper-cli", -9, ["boom"])
    assert "killed by SIGKILL" in killed
    # The second shape a full card produced in the wild: whisper.cpp segfaulted
    # rather than aborting, on the same card, one line differently.
    crashed = transcribe_run._explain_failure("/w/whisper-cli", -11, ["Segmentation fault"])
    assert "killed by SIGSEGV" in crashed
    assert crashed.count("(") == 1, "no nested parentheses to read through"
    ffmpeg = transcribe_run._explain_failure(
        "/usr/bin/ffmpeg", 1, ["Invalid data found when processing input"]
    )
    assert ffmpeg.startswith("ffmpeg could not read that file (exit 1):")
    assert "Invalid data found" in ffmpeg
