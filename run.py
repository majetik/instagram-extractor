"""Instagram saved posts -> places spreadsheet + map.

    python run.py parse <export.zip | folder | saved_posts.json>
    python run.py login
    python run.py fetch [--limit N]
    python run.py transcribe
    python run.py prepare          # extraction by Claude in chat (subscription)
    python run.py import           #   ...then load what Claude wrote
    python run.py extract          # OR extraction via the paid API
    python run.py geocode
    python run.py export
    python run.py status

Every step caches its results, so you can stop and re-run any step safely.
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def status() -> None:
    from igplaces.paths import EXTRACTED_DIR, FETCHED_DIR, PLACES_FILE, TRANSCRIPT_DIR, load_posts, read_json

    posts = load_posts()
    counts: dict[str, int] = {}
    extracted = places = 0
    for p in posts:
        f = read_json(FETCHED_DIR / f"{p['shortcode']}.json")
        key = f["status"] if f else "not fetched"
        counts[key] = counts.get(key, 0) + 1
        e = read_json(EXTRACTED_DIR / f"{p['shortcode']}.json")
        if e and e.get("status") == "ok":
            extracted += 1
            places += len(e["places"])
    print(f"Saved posts:   {len(posts)}")
    print("Fetched:       " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    print(f"Transcribed:   {len(list(TRANSCRIPT_DIR.glob('*.json')))} reels")
    print(f"Extracted:     {extracted} posts -> {places} places")
    geo = read_json(PLACES_FILE)
    if geo:
        print(f"Geocoded:      {sum(g['lat'] is not None for g in geo)}/{len(geo)} places on the map")


def publish() -> None:
    """Copy only the finished map into site/ (its own git repo) and push it.
    Nothing else from the project is published: data/ holds your Instagram session."""
    import shutil
    import subprocess
    from igplaces.paths import OUTPUT, ROOT

    site = ROOT / "site"
    if not (site / ".git").exists():
        raise SystemExit("No site/ repo yet. Create one once with (see README, 'Online map'):\n"
                         "  mkdir site && git -C site init && copy output\\map.html site\\index.html\n"
                         "  gh repo create <name> --public --source site --push   (then enable Pages in the repo settings)")
    shutil.copyfile(OUTPUT / "map.html", site / "index.html")
    git = lambda *a: subprocess.run(["git", "-C", str(site), *a], check=True)
    if subprocess.run(["git", "-C", str(site), "diff", "--quiet"]).returncode == 0:
        print("Map unchanged, nothing to publish.")
        return
    git("add", "index.html")
    git("commit", "-q", "-m", "Update map")
    git("push", "-q")
    print("Published. The GitHub Pages site updates in a minute or two.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="read your Instagram data export")
    p.add_argument("export_path")
    p.add_argument("--collection", help="only keep posts from this saved collection")
    sub.add_parser("login", help="log in to Instagram in a browser window (once)")
    p = sub.add_parser("collection", help="keep only posts in one of your saved collections (read live from Instagram)")
    p.add_argument("name")
    p.add_argument("--user", help="your Instagram username, if it can't be detected")
    p = sub.add_parser("fetch", help="visit each saved post and capture its content")
    p.add_argument("--limit", type=int)
    p.add_argument("--headless", action="store_true", help="hide the browser window")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--only-unresolved", action="store_true",
                   help="only posts whose caption didn't identify the place")
    p = sub.add_parser("transcribe", help="transcribe reel audio locally with Whisper")
    p.add_argument("--model", default="small", help="tiny | base | small | medium (bigger = slower, more accurate)")
    p.add_argument("--limit", type=int)
    p.add_argument("--retry-failed", action="store_true")
    p = sub.add_parser("extract", help="use Claude to pull out places and details")
    p.add_argument("--limit", type=int)
    p.add_argument("--redo", action="store_true", help="re-extract posts that were already done")
    p = sub.add_parser("prepare", help="(no-API route) bundle posts into batches for Claude in chat")
    p.add_argument("--size", type=int, default=25, help="posts per batch")
    p.add_argument("--batches", type=int, help="only make this many batches")
    sub.add_parser("import", help="(no-API route) load the results Claude wrote in chat")
    sub.add_parser("geocode", help="find map coordinates for each place")
    sub.add_parser("export", help="write places.csv, places.kml and map.html")
    sub.add_parser("status", help="show progress of each step")
    sub.add_parser("publish", help="push output/map.html to the GitHub Pages site (site/ folder)")
    args = ap.parse_args()

    if args.cmd == "parse":
        from igplaces import parse_export
        parse_export.run(args.export_path, collection=args.collection)
    elif args.cmd == "login":
        from igplaces import fetch
        fetch.login()
    elif args.cmd == "collection":
        from igplaces import fetch
        from igplaces.paths import DATA, POSTS_FILE, load_posts, read_json, write_json
        all_file = DATA / "posts_all.json"
        all_posts = read_json(all_file) or load_posts()
        write_json(all_file, all_posts)
        wanted = fetch.collection(args.name, user=args.user)
        by_sc = {p["shortcode"]: p for p in all_posts}
        kept = [by_sc.get(sc) or {"shortcode": sc, "url": f"https://www.instagram.com/{kind}/{sc}/",
                                  "kind": kind, "author": None, "saved_at": None,
                                  "caption": "", "hashtags": [], "collections": []}
                for sc, kind in wanted.items()]
        for p in kept:
            if args.name not in p["collections"]:
                p["collections"].append(args.name)
        write_json(POSTS_FILE, kept)
        missing = sum(sc not in by_sc for sc in wanted)
        print(f"Kept {len(kept)} posts from {args.name!r}"
              + (f" ({missing} weren't in the export, so they have no caption yet)" if missing else ""))
    elif args.cmd == "fetch":
        from igplaces import fetch
        fetch.run(limit=args.limit, headless=args.headless, retry_failed=args.retry_failed,
                  only_unresolved=args.only_unresolved)
    elif args.cmd == "transcribe":
        from igplaces import transcribe
        transcribe.run(model_size=args.model, limit=args.limit, retry_failed=args.retry_failed)
    elif args.cmd == "extract":
        from igplaces import extract
        extract.run(limit=args.limit, redo=args.redo)
    elif args.cmd == "prepare":
        from igplaces import manual
        manual.prepare(batch_size=args.size, max_batches=args.batches)
    elif args.cmd == "import":
        from igplaces import manual
        manual.import_results()
    elif args.cmd == "geocode":
        from igplaces import geocode
        geocode.run()
    elif args.cmd == "export":
        from igplaces import export
        export.run()
    elif args.cmd == "status":
        status()
    elif args.cmd == "publish":
        publish()


if __name__ == "__main__":
    main()
