from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from storage import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "config.json"
MANAGED_COOKIES_FILE = CONFIG_DIR / "cookies.txt"

SUPPORTED_BROWSERS = ("chrome", "safari", "firefox", "brave", "edge", "chromium", "arc")


def load() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_ytdlp_browser() -> str | None:
    """Browser name yt-dlp should pull cookies from, or None to disable."""
    val = load().get("ytdlp_browser")
    if val and val.lower() in SUPPORTED_BROWSERS:
        return val.lower()
    return None


def set_ytdlp_browser(browser: str | None) -> None:
    cfg = load()
    if browser:
        cfg["ytdlp_browser"] = browser.lower()
    else:
        cfg.pop("ytdlp_browser", None)
    save(cfg)


def get_cookies_file() -> Optional[str]:
    """Path to a Netscape-format cookies.txt that yt-dlp should use, or
    None if not configured / file missing on disk."""
    p = load().get("ytdlp_cookies_file")
    if p and Path(p).exists():
        return str(p)
    return None


def set_cookies_file(path: Optional[str]) -> None:
    cfg = load()
    if path:
        cfg["ytdlp_cookies_file"] = str(Path(path).expanduser().resolve())
    else:
        cfg.pop("ytdlp_cookies_file", None)
    save(cfg)


METADATA_BACKENDS = ("standard", "advanced")


def get_metadata_backend() -> str:
    """Which path fetches video metadata.
      'standard' — no Google OAuth, uses yt-dlp + oEmbed (web_metadata).
      'advanced' — full YouTube Data API v3 over OAuth (precise stats).

    Auto-detect when not explicitly set: if a client_secret.json exists,
    assume the user did the API setup and prefer 'advanced'; else
    'standard' so a fresh install works without any setup."""
    val = (load().get("metadata_backend") or "").lower()
    if val in METADATA_BACKENDS:
        return val
    from storage import CLIENT_SECRET_PATH
    return "advanced" if CLIENT_SECRET_PATH.exists() else "standard"


def set_metadata_backend(backend: Optional[str]) -> None:
    cfg = load()
    if backend and backend.lower() in METADATA_BACKENDS:
        cfg["metadata_backend"] = backend.lower()
    else:
        cfg.pop("metadata_backend", None)  # falls back to auto-detect
    save(cfg)


WHISPER_BACKENDS = ("none", "groq")


def get_whisper_backend() -> str:
    """Transcript fallback when YouTube has no subtitles.
      'none' (default) — give up, return transcript_error.
      'groq' — transcribe audio via Groq Whisper API."""
    val = (load().get("whisper_backend") or "").lower()
    return val if val in WHISPER_BACKENDS else "none"


def set_whisper_backend(backend: Optional[str]) -> None:
    cfg = load()
    if backend and backend.lower() in WHISPER_BACKENDS and backend.lower() != "none":
        cfg["whisper_backend"] = backend.lower()
    else:
        cfg.pop("whisper_backend", None)
    save(cfg)


def get_groq_api_key() -> Optional[str]:
    """Groq API key for the 'groq' whisper backend. Falls back to
    GROQ_API_KEY env var so headless users / CI can avoid touching the
    config file."""
    import os
    cfg_val = load().get("groq_api_key")
    if cfg_val:
        return str(cfg_val).strip() or None
    env_val = os.environ.get("GROQ_API_KEY")
    return env_val or None


def set_groq_api_key(key: Optional[str]) -> None:
    cfg = load()
    if key:
        cfg["groq_api_key"] = key.strip()
    else:
        cfg.pop("groq_api_key", None)
    save(cfg)


# Detect curl_cffi availability for yt-dlp's impersonate option. yt-dlp
# 2026.3.x checks for curl_cffi 0.10–0.14.x; 0.15+ broke the ABI and
# yt-dlp drops all impersonate targets to "unavailable". The require-
# ments pin forces <0.15 so this stays True in practice — but we still
# probe via yt-dlp's own ImpersonateTarget API rather than just
# importing curl_cffi, since the curl_cffi import can succeed while
# yt-dlp refuses to use it.
try:
    from yt_dlp.networking.impersonate import ImpersonateTarget as _ImpersonateTarget
    _IMPERSONATE_CHROME = _ImpersonateTarget(client="chrome")
except Exception:
    _IMPERSONATE_CHROME = None


def ytdlp_common_opts() -> Dict[str, Any]:
    """Baseline yt-dlp options shared across every call site.

    - `extractor_args` pins YouTube's `player_client` to `web_safari`.
      The default flipped twice in late 2025 (ios → tv_simply →
      web_safari) and yt-dlp's own README points users at extractor-
      args docs to lock in a client — anchoring here keeps subtitle /
      audio / playlist / channel fetching consistent across yt-dlp
      updates.
    - `impersonate=ImpersonateTarget('chrome')` routes via curl_cffi
      for a real Chrome TLS fingerprint, slipping past soft rate
      limits even with no cookies configured. yt-dlp's API expects an
      `ImpersonateTarget` instance (passing a plain string raises an
      AssertionError on `_impersonate_target_available`). Skipped
      when curl_cffi isn't installed or its version is incompatible.

    Spread into each call site's opts dict via `**ytdlp_common_opts()`;
    per-call keys take precedence on collision (later keys win)."""
    opts: Dict[str, Any] = {
        "extractor_args": {"youtube": {"player_client": ["web_safari"]}},
    }
    if _IMPERSONATE_CHROME is not None:
        opts["impersonate"] = _IMPERSONATE_CHROME
    return opts
