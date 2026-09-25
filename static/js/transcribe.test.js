// The transcribe view's wording.
//
// The property most worth pinning here is the honest one: an unknown duration is
// not zero, a failed job says why, and a running job always says how long it has
// been running. Everything else is formatting.

import assert from "node:assert/strict";
import test from "node:test";

import {
  deviceText, durationText, elapsedText, engineWords, extensionFor, headline,
  micAvailable, noteHref, percent, pickRecorderMime, recordingName, sourceLabel,
  stageWords, vramWords,
} from "./transcribe.js";

test("every state a job can be in has words", () => {
  // Not `notEqual(stageWords(s), s)`: "done" and "failed" are already the words
  // a person would use, so that assertion fails on correct output.
  for (const state of ["queued", "probing", "extracting", "transcribing", "summarising", "done", "failed"]) {
    assert.ok(stageWords(state), state);
  }
  assert.equal(stageWords("queued"), "waiting its turn");
  assert.equal(stageWords("transcribing"), "transcribing");
  assert.equal(stageWords("summarising"), "summarising");
});

test("an unknown state is shown as itself rather than swallowed", () => {
  assert.equal(stageWords("pondering"), "pondering");
  assert.equal(stageWords(""), "unknown");
});

test("elapsed time switches units where a person would", () => {
  assert.equal(elapsedText(0), "0s");
  assert.equal(elapsedText(9.4), "9s");
  assert.equal(elapsedText(59), "59s");
  assert.equal(elapsedText(60), "1m 00s");
  assert.equal(elapsedText(64), "1m 04s");
  assert.equal(elapsedText(3600), "1h 00m");
  assert.equal(elapsedText(3771), "1h 02m");
  assert.equal(elapsedText(-5), "0s", "a negative clock is not a time");
  assert.equal(elapsedText(undefined), "0s");
});

test("an unknown duration is unknown, not zero", () => {
  assert.equal(durationText(null), "unknown length");
  assert.equal(durationText(undefined), "unknown length");
  assert.equal(durationText(0), "0s", "a real zero-length recording says 0s");
  assert.equal(durationText(11.2), "11s");
  assert.equal(durationText(65), "1m 05s");
  assert.equal(durationText(3900), "1h 05m");
});

test("progress is a whole percent inside 0..100", () => {
  assert.equal(percent({ progress: 0 }), 0);
  assert.equal(percent({ progress: 0.456 }), 46);
  assert.equal(percent({ progress: 1 }), 100);
  assert.equal(percent({ progress: 1.4 }), 100, "a runaway fraction is clamped");
  assert.equal(percent({ progress: -1 }), 0);
  assert.equal(percent({}), 0);
  assert.equal(percent(null), 0);
});

test("a failed job shows the server's reason", () => {
  const line = headline({ state: "failed", error: "ffmpeg could not read that file (exit 1)" });
  assert.match(line, /ffmpeg could not read/);
});

test("a running job says what it is doing and for how long", () => {
  const line = headline({ state: "transcribing", message: "transcribing with small · 40%", elapsed_s: 95 });
  assert.match(line, /transcribing/);
  assert.match(line, /1m 35s/);
});

test("a finished job says where the note went", () => {
  const line = headline({ state: "done", note_id: "transcript-2026-09-24-lecture", elapsed_s: 240 });
  assert.match(line, /4m 00s/);
  assert.match(line, /transcript-2026-09-24-lecture/);
});

test("a source path is shown as its filename", () => {
  assert.equal(sourceLabel({ source: "/home/irving/lectures/week 3.mp4" }), "week 3.mp4");
  assert.equal(sourceLabel({ source: "recording-2026-09-24-101500.webm" }), "recording-2026-09-24-101500.webm");
  assert.equal(sourceLabel(null), "");
});

test("a ready engine says which binary and which models", () => {
  const words = engineWords({
    ready: true,
    cli: "/home/irving/whisper.cpp/build/bin/whisper-cli",
    models: ["medium", "small"],
    ffmpeg: "/usr/bin/ffmpeg",
    problems: [],
  });
  assert.equal(words.ok, true);
  assert.match(words.line, /whisper-cli/);
  assert.match(words.line, /small/);
  assert.match(words.detail, /whisper\.cpp/);
});

test("an engine that is not ready shows every problem, not just the first", () => {
  const words = engineWords({
    ready: false,
    cli: "",
    models: [],
    ffmpeg: "",
    problems: ["no whisper-cli found — build whisper.cpp, or set NOOKBOARD_WHISPER_CLI", "ffmpeg is not on PATH"],
  });
  assert.equal(words.ok, false);
  assert.match(words.detail, /NOOKBOARD_WHISPER_CLI/);
  assert.match(words.detail, /ffmpeg/);
});

test("the engine banner before the answer arrives does not claim readiness", () => {
  assert.equal(engineWords(null).ok, false);
  assert.equal(engineWords(undefined).ok, false);
});

//: The reading from the run this was written for: 7.0 GiB of an 8.0 GiB card
//: held by the model server, 644 MiB left.
const BUSY_VRAM = {
  total_mib: 8151,
  used_mib: 7103,
  free_mib: 644,
  holders: [{ name: "llama-server", mib: 7080 }],
};

test("the card is reported in the unit people read it in", () => {
  assert.equal(vramWords(BUSY_VRAM), "GPU 0.6/8.0 GiB free · llama-server has it");
  const quiet = vramWords({ total_mib: 8151, used_mib: 15, free_mib: 7732, holders: [] });
  assert.equal(quiet, "GPU 7.6/8.0 GiB free", "nobody else on it, so nobody is named");
});

test("a machine with no card to ask says nothing about one", () => {
  // Not "0.0 GiB free": that is a claim about a card we never read, and it is
  // the claim that decides whether a job runs on the CPU.
  assert.equal(vramWords(null), "");
  assert.equal(vramWords(undefined), "");
});

test("the engine line carries the card and the warning about the default model", () => {
  const words = engineWords({
    ready: true,
    cli: "/usr/local/bin/whisper-cli",
    models: ["small", "medium"],
    ffmpeg: "/usr/bin/ffmpeg",
    problems: [],
    vram: BUSY_VRAM,
    warnings: ["'small' needs about 721 MiB — it will run on the CPU"],
  });
  assert.equal(words.ok, true, "a busy card is not a broken install");
  assert.match(words.line, /GPU 0\.6\/8\.0 GiB free/);
  assert.match(words.warn, /will run on the CPU/);
  // The card is on the line once, in the line's own clause. The warning is about
  // the model, so the two do not say the same thing twice.
  assert.equal(words.warn.includes("GiB free"), false);
  // A refusal keeps the warnings field empty: the problems are the detail there.
  const bad = engineWords({ ready: false, cli: "", models: [], problems: ["no whisper-cli found"] });
  assert.equal(bad.warn, "");
});

test("only a run on the CPU is worth saying out loud", () => {
  assert.equal(deviceText({ on_cpu: true }), "on the CPU");
  assert.equal(deviceText({ on_cpu: false }), "", "the card is what everyone assumed");
  assert.equal(deviceText({}), "");
  assert.equal(deviceText(null), "");
});

test("a recording is named for the second it was made", () => {
  const at = new Date(2026, 8, 24, 14, 30, 12); // 24 September 2026, 14:30:12
  assert.equal(recordingName(at), "recording-2026-09-24-143012");
  const another = new Date(2026, 8, 24, 14, 30, 13);
  assert.notEqual(recordingName(at), recordingName(another), "two recordings a second apart differ");
});

test("the recorder picks the best type this browser admits to supporting", () => {
  assert.equal(pickRecorderMime(() => true), "audio/webm;codecs=opus");
  assert.equal(
    pickRecorderMime((t) => t === "audio/ogg;codecs=opus"),
    "audio/ogg;codecs=opus",
  );
  assert.equal(pickRecorderMime(() => false), "", "nothing supported is said, not guessed");
});

test("a throwing support check does not take the view down with it", () => {
  assert.equal(pickRecorderMime(() => { throw new Error("nope"); }), "");
});

test("mime types map to extensions the server accepts", () => {
  assert.equal(extensionFor("audio/webm;codecs=opus"), "webm");
  assert.equal(extensionFor("audio/ogg"), "ogg");
  assert.equal(extensionFor("audio/mp4"), "mp4");
  assert.equal(extensionFor(""), "webm");
  assert.equal(extensionFor(undefined), "webm");
});

test("a note link only exists once there is a note", () => {
  assert.equal(noteHref({ note_id: "transcript-2026-09-24-x" }), "#/note/transcript-2026-09-24-x");
  assert.equal(noteHref({ note_id: null }), null);
  assert.equal(noteHref({}), null);
  assert.equal(noteHref(null), null);
});

test("microphone support is reported, not assumed", () => {
  assert.equal(micAvailable({ mediaDevices: { getUserMedia: () => {} } }), typeof MediaRecorder !== "undefined");
  assert.equal(micAvailable({}), false);
  assert.equal(micAvailable(null), false);
});
