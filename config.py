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
