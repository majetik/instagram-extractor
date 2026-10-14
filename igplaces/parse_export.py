"""Stage 1: read the Instagram data export and list every saved post.

Accepts the export .zip, the unzipped folder, or saved_posts.json / .html directly.
Instagram has changed the JSON layout several times, so rather than relying on
exact keys we walk the whole structure and pick up every post/reel URL, along
with the nearest "title" (the author's username) and "timestamp".
"""
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .paths import POSTS_FILE, write_json

URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(p|reels?|tv)/([A-Za-z0-9_-]+)"
)


def _is_saved_file(name: str) -> bool:
    n = name.replace("\\", "/").lower()
    return "saved" in n and n.endswith((".json", ".html"))


def _iter_sources(path: Path):
    """Yield (filename, text) for every saved-posts file found at `path`."""
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if _is_saved_file(name):
                    yield name, zf.read(name).decode("utf-8", errors="replace")
    elif path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file() and _is_saved_file(str(p))]
        for p in files:
            yield str(p), p.read_text(encoding="utf-8", errors="replace")
    elif path.is_file():
        yield str(path), path.read_text(encoding="utf-8", errors="replace")
    else:
        raise SystemExit(f"Can't find {path}")


def _walk(obj, title, ts, out):
    if isinstance(obj, dict):
        if isinstance(obj.get("title"), str) and obj["title"]:
            title = obj["title"]
        if isinstance(obj.get("timestamp"), int):
            ts = obj["timestamp"]
        for v in obj.values():
            _walk(v, title, ts, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, title, ts, out)
    elif isinstance(obj, str):
        for m in URL_RE.finditer(obj):
            out.append((m.group(1), m.group(2), title, ts))


def fix_text(s: str) -> str:
    """Instagram exports UTF-8 text as if it were Latin-1 ("Donâ€™t"). Undo that."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _labels(items: list) -> dict:
    """Flatten a label_values list into {label: value} plus {section title: [sub-dicts]}."""
    out: dict = {}
    for it in items:
        if "label" in it:
            out[it["label"]] = it.get("href") or it.get("value") or it.get("timestamp_value")
        elif "dict" in it:
            out[it.get("title") or "_"] = [_labels(d.get("dict", [])) for d in it["dict"]]
    return out


def _parse_label_values(entries: list, collection: str | None, found: list) -> None:
    """The 2025+ export format: entries with label_values (URL, Caption, Owner, Hashtags)."""
    for e in entries:
        lv = _labels(e.get("label_values", []))
        m = URL_RE.search(str(lv.get("URL") or ""))
        if not m:
            continue
        owner = (lv.get("Owner") or [{}])[0]
        found.append({
            "kind": m.group(1), "shortcode": m.group(2),
            "author": owner.get("Username"), "ts": e.get("timestamp"),
            "caption": fix_text(lv.get("Caption") or "").strip(),
            "hashtags": [fix_text(h["Name"]) for h in lv.get("Hashtags", []) if h.get("Name")],
            "collection": collection,
        })


def _parse_json(data, found: list) -> None:
    is_new_format = isinstance(data, list) and data and isinstance(data[0], dict) and "label_values" in data[0]
    if not is_new_format:
        old = []
        _walk(data, None, None, old)
        found.extend({"kind": k, "shortcode": sc, "author": a, "ts": ts} for k, sc, a, ts in old)
        return
    for e in data:
        lv = _labels(e.get("label_values", []))
        if "URL" in lv:
            _parse_label_values([e], None, found)
        else:  # a collection: Name + nested posts
            name = fix_text(str(lv.get("Name") or "")) or None
            nested = [{"timestamp": None, "label_values": d.get("dict", [])}
                      for it in e.get("label_values", []) if "dict" in it for d in it["dict"]]
            _parse_label_values(nested, name, found)


def run(export_path: str, collection: str | None = None) -> list[dict]:
    found: list[dict] = []
    for name, text in _iter_sources(Path(export_path)):
        before = len(found)
        if name.lower().endswith(".json"):
            _parse_json(json.loads(text), found)
        else:
            found.extend({"kind": k, "shortcode": sc} for k, sc in URL_RE.findall(text))
        print(f"  {name}: {len(found) - before} links")

    posts: dict[str, dict] = {}
    for f in found:
        kind = "reel" if f["kind"].startswith("reel") or f["kind"] == "tv" else "p"
        sc = f["shortcode"]
        post = posts.setdefault(sc, {
            "shortcode": sc,
            "url": f"https://www.instagram.com/{kind}/{sc}/",
            "kind": kind,
            "author": None,
            "saved_at": None,
            "caption": "",
            "hashtags": [],
            "collections": [],
        })
        post["author"] = post["author"] or f.get("author")
        post["caption"] = post["caption"] or f.get("caption") or ""
        post["hashtags"] = post["hashtags"] or f.get("hashtags") or []
        if f.get("collection") and f["collection"] not in post["collections"]:
            post["collections"].append(f["collection"])
        if f.get("ts") and not post["saved_at"]:
            post["saved_at"] = datetime.fromtimestamp(f["ts"], timezone.utc).isoformat()

    all_collections = sorted({c for p in posts.values() for c in p["collections"]})
    print("Collections: " + (", ".join(f"{c!r}" for c in all_collections) or "none"))
    if collection:
        wanted = collection.strip().lower()
        posts = {sc: p for sc, p in posts.items() if wanted in (c.lower() for c in p["collections"])}
        print(f"Keeping only the {collection!r} collection")
        if not posts:
            raise SystemExit(f"No posts in a collection named {collection!r}.")

    result = sorted(posts.values(), key=lambda p: p["saved_at"] or "", reverse=True)
    write_json(POSTS_FILE, result)
    reels = sum(p["kind"] == "reel" for p in result)
    captions = sum(bool(p["caption"]) for p in result)
    print(f"Found {len(result)} saved posts ({reels} reels, {len(result) - reels} photo posts), "
          f"{captions} with captions")
    if not result:
        print("Nothing found. Make sure you picked JSON format and included 'Saved' in the export.")
    return result
