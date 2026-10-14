"""Shared file locations. Every stage reads/writes here so runs can resume."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"

POSTS_FILE = DATA / "posts.json"            # parse  -> list of saved posts
FETCHED_DIR = DATA / "fetched"              # fetch  -> <shortcode>.json + images
TRANSCRIPT_DIR = DATA / "transcripts"       # transcribe -> <shortcode>.json
AUDIO_DIR = DATA / "audio"                  # temporary reel audio
EXTRACTED_DIR = DATA / "extracted"          # extract -> <shortcode>.json
GEOCODE_CACHE = DATA / "geocode_cache.json"
PLACES_FILE = DATA / "places.json"          # geocode -> flat list of places
BROWSER_PROFILE = DATA / "browser-profile"  # your logged-in Instagram session
COOKIES_FILE = DATA / "cookies.txt"         # exported for yt-dlp

for d in (DATA, OUTPUT, FETCHED_DIR, TRANSCRIPT_DIR, AUDIO_DIR, EXTRACTED_DIR):
    d.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_posts() -> list[dict]:
    posts = read_json(POSTS_FILE)
    if posts is None:
        raise SystemExit("No posts yet. Run:  python run.py parse <path-to-your-export>")
    return posts
