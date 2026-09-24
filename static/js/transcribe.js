// The transcribe view's wording, and the small pieces its rows are built from.
//
// The engine line and the jobs come from /api/transcribe; the numbers in them are
// the server's. What lives here is the language, because a transcription takes
// minutes and the difference between a job that is working and one that has died
// is a sentence -- so most of this module is about saying what is happening.
//
// The two things the browser knows and the server does not: how long ago a job
// started (a clock the server would render stale) and what this browser can
// record from a microphone.

//: One sentence per state, in the present tense, for a thing that is happening.
const STAGE_WORDS = {
  queued: "waiting its turn",
  probing: "reading the file",
  extracting: "extracting audio",
  transcribing: "transcribing",
  summarising: "summarising",
  done: "done",
  failed: "failed",
};

export function stageWords(state) {
  return STAGE_WORDS[state] || state || "unknown";
}

//: How long a job has been running. Seconds while it is short, because that is
//: the unit you read while watching it; minutes and hours once it is not.
export function elapsedText(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m < 60) return `${m}m ${String(rest).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

//: How long the recording is -- `0s` and `null` are different things here, so an
//: unknown length says so rather than claiming to be zero seconds long.
export function durationText(seconds) {
  if (seconds === null || seconds === undefined) return "unknown length";
  const s = Math.round(Number(seconds) || 0);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m < 60) return `${m}m ${String(rest).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function percent(job) {
  const p = Number(job && job.progress);
  if (!Number.isFinite(p)) return 0;
  return Math.max(0, Math.min(100, Math.round(p * 100)));
}

//: The line under a job's name. A failure says what went wrong; a job that is
//: running says what it is doing and for how long; a finished one says how long
//: it took and where the note is.
export function headline(job) {
  const state = job && job.state;
  if (state === "failed") return job.error || "failed";
  if (state === "done") {
    const where = job.note_id ? `note ${job.note_id}` : "no note";
    return `finished in ${elapsedText(job.elapsed_s)} · ${where}`;
  }
  return `${stageWords(state)} · ${elapsedText(job && job.elapsed_s)}`;
}

//: What a job is a job *about*, so a list of six of them is readable.
export function sourceLabel(job) {
  if (!job) return "";
  if (job.source && job.source.includes("/")) {
    const parts = job.source.split("/");
    return parts[parts.length - 1] || job.source;
  }
  return job.source || "";
}

//: The engine banner: one line, and whether transcription can run at all.
export function engineWords(engine) {
  if (!engine) return { ok: false, line: "asking what is installed…", detail: "" };
  const models = (engine.models || []).join(", ");
  if (engine.ready) {
    const cli = engine.cli ? engine.cli.split("/").pop() : "whisper-cli";
    return {
      ok: true,
      line: `${cli} · ${models || "no models"} · all local`,
      detail: engine.cli || "",
    };
  }
  return {
    ok: false,
    line: "not ready",
    detail: (engine.problems || []).join(" · "),
  };
}

//: A name for a recording the browser is about to make. Dated, because two
//: lectures recorded a week apart must not be called the same thing.
export function recordingName(now = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  const day = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `recording-${day}-${time}`;
}

//: What the recorder offers, best first. Opus in a container is what browsers
//: actually give you; the server reads all of these through ffmpeg.
export const RECORDER_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
];

const EXTENSIONS = {
  "audio/webm": "webm",
  "audio/ogg": "ogg",
  "audio/mp4": "mp4",
  "audio/mpeg": "mp3",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
};

export function extensionFor(mimeType) {
  const bare = String(mimeType || "").split(";")[0].trim().toLowerCase();
  return EXTENSIONS[bare] || "webm";
}

//: The first type this browser will actually record. `isSupported` is
//: `MediaRecorder.isTypeSupported` in the browser and a stub in the tests.
export function pickRecorderMime(isSupported = () => true) {
  for (const type of RECORDER_TYPES) {
    try {
      if (isSupported(type)) return type;
    } catch {
      // A throwing predicate is a browser we do not understand; try the next.
    }
  }
  return "";
}

//: Where a finished job's note lives, or null if there is not one yet.
export function noteHref(job) {
  if (!job || !job.note_id) return null;
  return `#/note/${encodeURIComponent(job.note_id)}`;
}

//: Whether this browser can record at all. Said out loud in the UI rather than
//: hidden behind a button that does nothing when clicked.
export function micAvailable(nav) {
  const n = nav || (typeof navigator === "undefined" ? null : navigator);
  return Boolean(n && n.mediaDevices && n.mediaDevices.getUserMedia && typeof MediaRecorder !== "undefined");
}
