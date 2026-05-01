from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "yt-sub"
OUTPUT_DIR = Path.home() / "YT-sub" / "output"
CLIENT_SECRET_PATH = CONFIG_DIR / "client_secret.json"
TOKEN_PATH = CONFIG_DIR / "token.json"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
