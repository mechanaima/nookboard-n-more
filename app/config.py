"""Runtime configuration for nookboard.

Everything is env-overridable so the same code can point at a different vault
or a different llama.cpp server without edits.

  NOOKBOARD_VAULT           path to the Markdown vault  (default <repo>/vault)
  NOOKBOARD_LLM_URL         OpenAI-compatible base URL  (default llama.cpp :11440)
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

    @property
    def llm_chat_url(self) -> str:
        return self.llm_url.rstrip("/") + "/chat/completions"


def load_settings() -> Settings:
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
    )
