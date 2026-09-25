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
  NOOKBOARD_LLM_MODEL       model name to request
  NOOKBOARD_LLM_MAX_TOKENS  completion budget per call
  NOOKBOARD_LLM_TIMEOUT     seconds to wait for a completion
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    daily_summary_hour: int = 22
    #: Whether to also write a weekly review. Shares `daily_summary_hour` as its
    #: cutoff -- the same question ("has the period's cutoff passed?") and one
    #: constant, so the two cannot drift about what "after the cutoff" means.
    weekly_summary: bool = True
    #: Path to a secondary Obsidian vault to surface in the dashboard (read-only).
    #: Leave unset (None) to disable.
    obsidian_vault: Path | None = None

    @property
    def llm_chat_url(self) -> str:
        return self.llm_url.rstrip("/") + "/chat/completions"


def load_settings() -> Settings:
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
        daily_summary_hour=_env_int("NOOKBOARD_DAILY_SUMMARY_HOUR", 22),
        weekly_summary=_env_bool("NOOKBOARD_WEEKLY_SUMMARY", True),
        obsidian_vault=obsidian_vault,
    )
