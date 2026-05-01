from __future__ import annotations

import json
from typing import Any, Dict

from storage import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "config.json"

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
