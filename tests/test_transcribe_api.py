"""The transcription endpoints.

These exist because the units were not enough. `POST /api/transcribe` was
declared `def`, so FastAPI ran it in a worker thread — where `asyncio.create_task`
has no running loop — and every submission answered 500. Nothing below the
endpoint could see that. Only a request can.

The app is built here rather than through `client_factory` so the whisper knobs
can be pinned at files in `tmp_path`: an engine that is *found* on the machine
running the tests would make these pass or fail depending on whose machine it is.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _fake_tool(path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def transcribe_client(tmp_path, sse_server):
    """An app whose engine is ready, on files that exist only for this test.

    Entered and not just constructed: `TestClient` owns the event loop a job's
    task runs on, and a bare client tears that loop down between requests, so
    every job dies as "cancelled" -- which is a failure that would make the
    failure test below pass for the wrong reason.
    """
    cli = _fake_tool(tmp_path / "whisper-cli")
    models = tmp_path / "models"
    models.mkdir()
    for name in ("small", "medium"):
        (models / f"ggml-{name}.bin").write_bytes(b"x" * 32)
    base, _set_script = sse_server
    opened: list[TestClient] = []

    def _make(**overrides):
        fields = dict(
            vault=tmp_path,
            llm_url=base,
            llm_model="fake",
            llm_max_tokens=2048,
            llm_timeout=30.0,
            whisper_cli=str(cli),
            whisper_models=str(models),
        )
        fields.update(overrides)
        client = TestClient(create_app(settings=Settings(**fields)))
        client.__enter__()
        opened.append(client)
        client.vault_root = tmp_path  # type: ignore[attr-defined]
        return client

    yield _make

    for client in opened:
        client.__exit__(None, None, None)


def test_status_reports_the_engine_and_what_the_form_needs(transcribe_client):
    payload = transcribe_client().get("/api/transcribe").json()
    assert payload["engine"]["ready"] is True
    assert payload["engine"]["cli"].endswith("whisper-cli")
    assert sorted(payload["engine"]["models"]) == ["medium", "small"]
    assert payload["model"] == "small", "the configured default"
    assert payload["choices"] == ["small", "medium"]
    assert payload["collection"] == "transcripts"
    assert payload["collection"] in payload["collections"]
    assert ".mp4" in payload["extensions"] and ".wav" in payload["extensions"]
    assert payload["jobs"] == []
    assert payload["audio_dir"].endswith(".audio")


def test_an_engine_that_is_not_ready_is_reported_rather_than_hidden(transcribe_client):
    client = transcribe_client(whisper_cli="/nowhere/whisper-cli")
    payload = client.get("/api/transcribe").json()
    assert payload["engine"]["ready"] is False
    assert any("NOOKBOARD_WHISPER_CLI" in p for p in payload["engine"]["problems"])


def test_the_path_and_the_file_are_both_offered(transcribe_client):
    """The form's two inputs must both reach the server: one reads a file where
    it lies, the other receives bytes the browser holds."""
    client = transcribe_client()
    paths = {r.path for r in client.app.routes if hasattr(r, "path")}
    assert "/api/transcribe" in paths
    assert "/api/transcribe/upload" in paths
    assert "/api/transcribe/summarize" in paths
    assert "/api/transcribe/{job_id}" in paths


def test_no_path_is_refused(transcribe_client):
    resp = transcribe_client().post("/api/transcribe", json={})
    assert resp.status_code == 400
    assert "path" in resp.json()["detail"]


def test_a_path_that_is_not_there_is_refused(transcribe_client):
    resp = transcribe_client().post("/api/transcribe", json={"path": "/nope/missing.wav"})
    assert resp.status_code == 400
    assert "/nope/missing.wav" in resp.json()["detail"]


def test_submitting_a_job_answers_with_a_job(transcribe_client, tmp_path):
    """The regression test for the 500. A sync handler cannot spawn the task."""
    source = tmp_path / "lecture.wav"
    source.write_bytes(b"RIFF" + b"\0" * 200)
    client = transcribe_client()
    resp = client.post("/api/transcribe", json={"path": str(source), "summarize": True})
    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert job["id"]
    assert job["state"] in ("queued", "probing", "extracting", "transcribing", "failed")
    assert job["model"] == "small"
    assert job["summarize"] is True
    assert job["source"] == str(source)

    # Pollable by the id it handed back, and in the list the view re-reads.
    fetched = client.get(f"/api/transcribe/{job['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == job["id"]
    assert any(j["id"] == job["id"] for j in client.get("/api/transcribe").json()["jobs"])


def test_a_job_that_cannot_be_read_fails_with_a_sentence(transcribe_client, tmp_path):
    """A file that is not audio is the common mistake. It must land on the job as
    a sentence, not as a traceback in a log nobody is watching."""
    source = tmp_path / "not-audio.wav"
    source.write_text("this is not audio, it is a text file\n")
    client = transcribe_client()
    job = client.post("/api/transcribe", json={"path": str(source)}).json()
    import time

    for _ in range(100):
        state = client.get(f"/api/transcribe/{job['id']}").json()
        if state["state"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert state["state"] == "failed"
    # Not just "it failed": a job cancelled by a torn-down loop also fails, and
    # the whole point of this test is that the reason reaches the person.
    assert "ffprobe" in state["error"] or "ffmpeg" in state["error"], state["error"]
    assert "Traceback" not in state["error"]


def test_an_unknown_job_is_a_404_that_explains_itself(transcribe_client):
    resp = transcribe_client().get("/api/transcribe/deadbeef")
    assert resp.status_code == 404
    assert "restart" in resp.json()["detail"]


def test_an_upload_of_something_that_is_not_media_is_refused(transcribe_client):
    resp = transcribe_client().post(
        "/api/transcribe/upload?name=notes.pdf", content=b"%PDF-1.4 not audio"
    )
    assert resp.status_code == 400
    assert "audio or video" in resp.json()["detail"]


def test_an_empty_upload_is_refused_and_leaves_nothing_behind(transcribe_client, tmp_path):
    resp = transcribe_client().post("/api/transcribe/upload?name=empty.wav", content=b"")
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]
    assert not (tmp_path / ".audio" / "empty.wav").exists(), "an empty upload left a file"


def test_an_upload_is_kept_in_the_vault(transcribe_client, tmp_path):
    """A recording made in the browser exists nowhere else, so the note has to be
    able to name a file that will still be there tomorrow."""
    client = transcribe_client()
    resp = client.post(
        "/api/transcribe/upload?name=Lecture Notes.wav&summarize=false",
        content=b"RIFF" + b"\0" * 100,
    )
    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert job["summarize"] is False
    kept = tmp_path / ".audio" / "lecture-notes.wav"
    assert kept.is_file(), sorted(p.name for p in tmp_path.iterdir())
    assert kept.read_bytes().startswith(b"RIFF")

    # And it must not be offered as a notes collection: it is a folder of audio
    # sitting inside the vault, nothing more.
    assert ".audio" not in client.get("/api/transcribe").json()["collections"]
    assert ".audio" not in client.get("/api/collections").json()


def test_a_second_upload_does_not_overwrite_the_first(transcribe_client, tmp_path):
    """For a browser recording, the kept file is the only copy there is."""
    client = transcribe_client()
    for _ in range(2):
        resp = client.post("/api/transcribe/upload?name=same.wav", content=b"RIFF" + b"\0" * 50)
        assert resp.status_code == 201
    kept = sorted(p.name for p in (tmp_path / ".audio").iterdir())
    assert kept == ["same-2.wav", "same.wav"]


def test_resummarise_refuses_a_note_that_has_no_transcript(transcribe_client, seed_note):
    seed_note("plain-note", title="Just a note", body="No transcript here.")
    resp = transcribe_client().post("/api/transcribe/summarize", json={"id": "plain-note"})
    assert resp.status_code == 400
    assert "transcript" in resp.json()["detail"]


def test_resummarise_refuses_a_note_that_does_not_exist(transcribe_client):
    resp = transcribe_client().post("/api/transcribe/summarize", json={"id": "ghost"})
    assert resp.status_code == 404


def test_resummarise_of_a_real_transcript_starts_a_job(transcribe_client, seed_note):
    from app import transcribe

    body = transcribe.render_body(provenance_line="_Source: x_", transcript="[0:00] hello")
    seed_note("transcript-2026-09-24-x", title="A lecture", body=body,
              collection="transcripts", tags=("transcript",))
    resp = transcribe_client().post("/api/transcribe/summarize", json={"id": "transcript-2026-09-24-x"})
    assert resp.status_code == 201, resp.text
    job = resp.json()
    assert job["only_summary"] is True
    assert job["note_id"] == "transcript-2026-09-24-x"
    # Nothing was uploaded, so nothing is kept: the note is the whole artifact.
    assert job["keep"] is False


def _wait(client, job_id, tries=200):
    import time

    for _ in range(tries):
        state = client.get(f"/api/transcribe/{job_id}").json()
        if state["state"] in ("done", "failed"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished: {state}")


def _transcript_note(seed_note, ident="transcript-2026-09-24-x"):
    from app import transcribe

    body = transcribe.render_body(provenance_line="_Source: x_", transcript="[0:00] hello")
    seed_note(ident, title="A lecture", body=body, collection="transcripts",
              tags=("transcript",))
    return ident


def test_a_resummarise_whose_model_fails_fails_the_job_and_leaves_the_note_alone(
    transcribe_client, seed_note, sse_server
):
    """The summary *is* the job, so a summary that failed is the job failing.

    Reporting it as done would send someone away believing their first summary
    was rewritten when nothing was. And the note keeps the summary it already
    had: an apology from a run that never landed is worse than the older text,
    which was at least true of the same transcript.
    """
    _base, set_script = sse_server
    ident = _transcript_note(seed_note)
    client = transcribe_client()
    before = (client.vault_root / "transcripts" / f"{ident}.md").read_text()

    set_script([], status=500)
    resp = client.post("/api/transcribe/summarize", json={"id": ident})
    assert resp.status_code == 201, resp.text

    state = _wait(client, resp.json()["id"])
    assert state["state"] == "failed", state
    assert "summary failed" in state["error"], state["error"]
    assert "transcribed, but" not in state["error"], "nothing was transcribed"
    after = (client.vault_root / "transcripts" / f"{ident}.md").read_text()
    assert after == before, "a failed re-summarise must not rewrite the note"


def test_a_summary_that_fails_after_a_transcript_says_so_without_undoing_it(
    transcribe_client, seed_note, tmp_path
):
    """The other half of the same distinction.

    When the transcript is what just succeeded, an unreachable model costs the
    summary and nothing else: the transcript stays, the note explains the gap,
    and the job is done -- the expensive part is safe. `_failed_summary` is
    called directly because the branch it takes is the whole point and reaching
    it through a job would take a working whisper.
    """
    from app.llm import LLMError
    from app.transcribe_run import Job, Transcriber
    from app.vault import Vault

    ident = _transcript_note(seed_note)
    vault = Vault(tmp_path)
    runner = Transcriber(
        cli="/nonexistent", models_dir=tmp_path, default_model="small", vault=vault,
        taken_ids=lambda: [], llm=None,
    )
    job = Job(
        id="job1", source="a.m4a", path=tmp_path / "a.m4a", model="small",
        collection="transcripts", summarize=True, state="summarising", note_id=ident,
    )

    runner._failed_summary(job, LLMError("the model is not running"))

    assert job.state == "summarising", "the caller decides the state, not this"
    assert "transcribed, but the summary failed" in job.error
    body = vault.read(ident).body
    assert "No summary" in body
    assert "[0:00] hello" in body, "the transcript must survive a failed summary"

