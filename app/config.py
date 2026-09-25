"""Runtime configuration for nookboard.

Everything is env-overridable so the same code can point at a different vault
or a different llama.cpp server without edits.

  NOOKBOARD_VAULT           path to the Markdown vault  (default <repo>/vault)
  NOOKBOARD_OBSIDIAN_VAULT path to a secondary Obsidian vault to surface in the
                            dashboard (e.g. ~/Documents/School). Notes are read but
                            never written. Leave empty to disable.
  NOOKBOARD_LLM_URL         OpenAI-compatible base URL  (default llama.cpp :11440)
  NOOKBOARD_WHISPER_CLI     whisper-cli to run (default: look in the usual places)
  NOOKBOARD_WHISPER_MODELS  directory of ggml-*.bin models
  NOOKBOARD_WHISPER_MODEL   model to use by default (default: small)
  NOOKBOARD_WHISPER_GPU_CHECK  ask nvidia-smi how much of the card is free before
                            running a job (default: on). Off means no reading, no
                            warning and no CPU fall-back.
  NOOKBOARD_WHISPER_CPU     run every transcription on the CPU (default: off)
  NOOKBOARD_LLM_MODEL       model name to request
  NOOKBOARD_LLM_MAX_TOKENS  completion budget per call
  NOOKBOARD_LLM_TIMEOUT     seconds to wait for a completion
  NOOKBOARD_SIEVE_BASE_URL  sieve scrape API base (default https://scrape.usesieve.com)

Secrets do not go in the environment if you would rather not export them: a
`.env` file at the repository root is read on every `load_settings()` (see
`apply_env_file`) and is where `tools/sieve-login.py` writes `SIEVE_API_KEY`.
The file is gitignored, and a real environment variable always wins over it, so
`SIEVE_API_KEY=... uv run uvicorn ...` still overrides what is on disk.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .sieve import DEFAULT_BASE_URL

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the device login drops `SIEVE_API_KEY`. Gitignored, 0600 when written.
ENV_FILE = REPO_ROOT / ".env"


def parse_env_file(text: str) -> dict[str, str]:
    """Read `KEY=value` lines.

    Deliberately smaller than python-dotenv: this app has no multiline values
    and no expansion to do, and a parser that only understands what it is given
    is easier to reason about than one that is asked to interpret. Blank lines,
    comments and `export ` prefixes are skipped; matching quotes are stripped so
    a hand-written `SIEVE_API_KEY="dc_sk_..."` reads back without them.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def apply_env_file(path: Path | None = None) -> dict[str, str]:
    """Put a `.env` into the environment, without overriding it.

    `setdefault`, not assignment: an exported variable is the more specific
    statement — it was set for *this* process — and a file on disk should not be
    able to quietly contradict it. A missing or unreadable file is simply no
    variables, never an error: the app boots fine without sieve configured.
    """
    target = path or ENV_FILE
    try:
        text = target.read_text()
    except OSError:
        return {}
    values = parse_env_file(text)
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values


def write_env_secret(key: str, value: str, path: Path | None = None) -> Path:
    """Set one `KEY=value` in the `.env`, leaving every other line alone.

    Written 0600 because this file is a secret store: the api key it holds has
    full account access. Other lines are preserved verbatim so a hand-written
    comment or a second setting is not collateral damage of a login.
    """
    target = path or ENV_FILE
    lines: list[str] = []
    found = False
    if target.exists():
        for raw in target.read_text().splitlines():
            stripped = raw.strip()
            name = stripped.removeprefix("export ").split("=", 1)[0].strip()
            if stripped and not stripped.startswith("#") and "=" in stripped and name == key:
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(raw)
    if not found:
        lines.append(f"{key}={value}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip("\n") + "\n")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    vault: Path
    llm_url: str
    llm_model: str
    llm_max_tokens: int
    llm_timeout: float
    #: Hour (0-23) the end-of-day summary runs, or -1 to switch the scheduler
    #: off entirely. One hour, not a cron expression: see daily.is_due.
    # -- transcription ------------------------------------------------------
    #: whisper.cpp's CLI. Empty means "look in the usual places" (see
    #: `transcribe.CLI_CANDIDATES`); set it to pick one and have the app say so.
    whisper_cli: str = ""
    #: Directory holding `ggml-<name>.bin`. Empty derives it from the CLI's own
    #: layout, which is where whisper.cpp puts them.
    whisper_models: str = ""
    #: Which model the UI offers first. `small` is the default because a lecture
    #: is minutes of work either way and `medium` is three times the download.
    whisper_model: str = "small"
    #: Whether to ask the card how much room is free before a job runs. On by
    #: default: a model that will not fit is the difference between a transcript
    #: in three minutes and a CUDA abort in six seconds, and the run moves to the
    #: CPU when it can see that. Off means no reading, no warning and no
    #: fall-back -- for a machine where nvidia-smi is slow or answers about a
    #: card this app is not using.
    whisper_gpu_check: bool = True
    #: Run every transcription on the CPU regardless of the card. Off by default:
    #: the automatic fall-back already covers a full GPU, so this is for keeping
    #: the card free on purpose.
    whisper_cpu: bool = False
    daily_summary_hour: int = 22
    #: Whether to also write a weekly review. Shares `daily_summary_hour` as its
    #: cutoff -- the same question ("has the period's cutoff passed?") and one
    #: constant, so the two cannot drift about what "after the cutoff" means.
    weekly_summary: bool = True
    #: Path to a secondary Obsidian vault to surface in the dashboard (read-only).
    #: Leave unset (None) to disable.
    obsidian_vault: Path | None = None
    #: sieve scrape API key. Empty means sieve is not configured and *every*
    #: sieve feature reports itself as absent rather than failing loudly -- the
    #: rest of the app must behave exactly as it did before this existed.
    sieve_api_key: str = ""
    #: Base URL of the sieve API. Overridable so a test (or a self-hosted
    #: instance) can point somewhere else without touching code.
    sieve_base_url: str = DEFAULT_BASE_URL

    @property
    def sieve_configured(self) -> bool:
        return bool(self.sieve_api_key)

    @property
    def llm_chat_url(self) -> str:
        return self.llm_url.rstrip("/") + "/chat/completions"


def load_settings() -> Settings:
    apply_env_file()
    obsidian_raw = os.environ.get("NOOKBOARD_OBSIDIAN_VAULT", "").strip()
    obsidian_vault = Path(obsidian_raw).expanduser().resolve() if obsidian_raw else None
    return Settings(
        vault=_env_path("NOOKBOARD_VAULT", REPO_ROOT / "vault"),
        llm_url=os.environ.get("NOOKBOARD_LLM_URL", "http://127.0.0.1:11440/v1").strip(),
        llm_model=os.environ.get("NOOKBOARD_LLM_MODEL", "bonsai-27b-q1_0").strip(),
        # Generous by default: the local model spends most of its budget in the
        # reasoning block and returns EMPTY content with finish_reason=length if
        # it runs out. Measured against Bonsai-27B: summarise and tags need
        # roughly 1400 tokens, links has exceeded 2048 and needed more.
        llm_max_tokens=_env_int("NOOKBOARD_LLM_MAX_TOKENS", 4096),
        llm_timeout=_env_float("NOOKBOARD_LLM_TIMEOUT", 300.0),
        whisper_cli=os.environ.get("NOOKBOARD_WHISPER_CLI", "").strip(),
        whisper_models=os.environ.get("NOOKBOARD_WHISPER_MODELS", "").strip(),
        whisper_model=os.environ.get("NOOKBOARD_WHISPER_MODEL", "small").strip() or "small",
        whisper_gpu_check=_env_bool("NOOKBOARD_WHISPER_GPU_CHECK", True),
        whisper_cpu=_env_bool("NOOKBOARD_WHISPER_CPU", False),
        daily_summary_hour=_env_int("NOOKBOARD_DAILY_SUMMARY_HOUR", 22),
        weekly_summary=_env_bool("NOOKBOARD_WEEKLY_SUMMARY", True),
        obsidian_vault=obsidian_vault,
        sieve_api_key=os.environ.get("SIEVE_API_KEY", "").strip(),
        sieve_base_url=os.environ.get(
            "NOOKBOARD_SIEVE_BASE_URL", DEFAULT_BASE_URL
        ).strip().rstrip("/") or DEFAULT_BASE_URL,
    )
