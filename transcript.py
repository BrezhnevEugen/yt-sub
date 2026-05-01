from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)


class TranscriptError(Exception):
    pass


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PREFERRED_LANGS: Tuple[str, ...] = ("ru", "en")


def parse_video_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    if _VIDEO_ID_RE.match(s):
        return s

    try:
        u = urlparse(s)
    except ValueError as e:
        raise ValueError(f"Cannot parse URL: {e}") from e

    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    vid = ""
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
        else:
            parts = u.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
                vid = parts[1]

    if not _VIDEO_ID_RE.match(vid):
        raise ValueError(f"Cannot extract video id from: {url_or_id}")
    return vid


def _try_primary(video_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    api = YouTubeTranscriptApi()
    try:
        return api.fetch(video_id, languages=_PREFERRED_LANGS).to_raw_data(), None
    except NoTranscriptFound:
        pass
    except (TranscriptsDisabled, VideoUnavailable) as e:
        return None, type(e).__name__
    except Exception as e:
        return None, f"primary: {e}"

    try:
        transcripts = api.list(video_id)
    except Exception as e:
        return None, f"list: {e}"

    pools = (
        [t for t in transcripts if not t.is_generated],
        [t for t in transcripts if t.is_generated],
    )
    last_err = "no transcripts found"
    for pool in pools:
        for t in pool:
            try:
                return t.fetch().to_raw_data(), None
            except Exception as e:
                last_err = f"fetch {t.language_code}: {e}"
                continue
    return None, last_err


def _parse_json3(text: str) -> List[Dict]:
    data = json.loads(text)
    out: List[Dict] = []
    for ev in data.get("events", []) or []:
        segs = ev.get("segs")
        if not segs:
            continue
        line = "".join(s.get("utf8", "") for s in segs).strip()
        if not line:
            continue
        out.append(
            {
                "text": line,
                "start": (ev.get("tStartMs", 0) or 0) / 1000.0,
                "duration": (ev.get("dDurationMs", 0) or 0) / 1000.0,
            }
        )
    return out


def _try_ytdlp(video_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    import yt_dlp

    from config import get_ytdlp_browser

    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": list(_PREFERRED_LANGS),
            "subtitlesformat": "json3",
            "outtmpl": str(tmp / "%(id)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        # cookies.txt path wins over browser-cookies if both are set —
        # browsers on macOS often hit TCC sandboxing (Safari) or App-Bound
        # Encryption (Chrome 130+), while a manually exported cookies.txt
        # always works.
        from config import get_cookies_file
        cookies_file = get_cookies_file()
        if cookies_file:
            opts["cookiefile"] = cookies_file
        else:
            browser = get_ytdlp_browser()
            if browser:
                # yt-dlp reads cookies straight from the user's browser
                # so YouTube treats requests as coming from a logged-in
                # session — bypasses "Sign in to confirm you're not a
                # bot" / IP-block barriers that hit residential IPs.
                opts["cookiesfrombrowser"] = (browser,)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            return None, f"yt-dlp: {e}"

        for lang in _PREFERRED_LANGS:
            for pattern in (f"*.{lang}.json3", f"*.{lang}-*.json3"):
                for f in sorted(tmp.glob(pattern)):
                    try:
                        return _parse_json3(f.read_text(encoding="utf-8")), None
                    except Exception as e:
                        return None, f"yt-dlp parse {f.name}: {e}"
        for f in sorted(tmp.glob("*.json3")):
            try:
                return _parse_json3(f.read_text(encoding="utf-8")), None
            except Exception as e:
                return None, f"yt-dlp parse {f.name}: {e}"

    return None, "yt-dlp: no subtitles in preferred languages"


def fetch_transcript(video_id: str) -> List[Dict]:
    primary, primary_err = _try_primary(video_id)
    if primary is not None:
        return primary

    if primary_err in {"TranscriptsDisabled", "VideoUnavailable"}:
        raise TranscriptError(primary_err)

    ytdlp, ytdlp_err = _try_ytdlp(video_id)
    if ytdlp is not None:
        return ytdlp

    raise TranscriptError(
        f"primary={primary_err or 'unknown'} | {ytdlp_err or 'yt-dlp failed'}"
    )
