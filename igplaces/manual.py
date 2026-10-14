"""Stage 4, no-API route: Claude does the extraction inside a Claude Code chat,
which is covered by a Claude subscription.

  prepare  bundles not-yet-extracted posts into compact batch files
           (data/manual/batch_NNN.md), trimmed to keep usage down.
  import   validates the results Claude wrote (batch_NNN.result.json) and
           saves them in the same format as the API route, so geocode/export
           don't care which route was used.
"""
import json
import shutil

from pydantic import ValidationError

from .paths import DATA, EXTRACTED_DIR, FETCHED_DIR, TRANSCRIPT_DIR, load_posts, read_json, write_json
from .schema import PostExtraction

MANUAL_DIR = DATA / "manual"
DONE_DIR = MANUAL_DIR / "done"
MANUAL_DIR.mkdir(exist_ok=True)
DONE_DIR.mkdir(exist_ok=True)

INSTRUCTIONS = """# Extraction batch {num}

For each post below, list the specific places it recommends or shows (restaurants, cafes,
bars, shops, hotels, sights, beaches, viewpoints...). List posts give one entry per place.
Use a city/neighborhood as the place only if no specific spot is named. Fill city and
country whenever they can be inferred. No identifiable place -> empty list. Don't invent.
Only open an image if the text doesn't identify the place.

Write the results to: {result_path}
as a JSON list: [{{"shortcode": "...", "summary": "one sentence", "places": [
  {{"name": "...", "category": "restaurant|cafe|bar|bakery|shop|market|hotel|attraction|museum|viewpoint|beach|nature|park|neighborhood|city|other",
    "address": null, "neighborhood": null, "city": "...", "region": null, "country": "...",
    "details": "tips, what to order, prices", "confidence": "high|medium|low"}}]}}]
Include every shortcode in this batch, even those with no places.
Add "needs_more": true when the places are probably only in the photos/video (e.g. "see the
carousel", "location at the end"), false when the post simply has no place (ad, general tip).
"""


def _clip(text: str | None, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " […]"


def _pending_shortcodes() -> set[str]:
    pending = set()
    for f in MANUAL_DIR.glob("batch_*.md"):
        pending |= {line.split("`")[1] for line in f.read_text(encoding="utf-8").splitlines()
                    if line.startswith("## `")}
    return pending


def is_unresolved(extracted: dict | None) -> bool:
    """True if the post still needs more info: no places, all low-confidence, or no city/country.
    An explicit needs_more flag (set during extraction) wins over the heuristic."""
    if not extracted:
        return True
    if "needs_more" in extracted:
        return bool(extracted["needs_more"])
    places = extracted.get("places") or []
    if not places:
        return True
    return all(p["confidence"] == "low" or not (p.get("city") or p.get("country")) for p in places)


def _fetched_ok(sc: str) -> dict | None:
    f = read_json(FETCHED_DIR / f"{sc}.json")
    return f if f and f.get("status") == "ok" else None


def prepare(batch_size: int = 25, max_batches: int | None = None) -> None:
    """Batch posts that are new, or that were unresolved from the caption and have since been fetched."""
    pending = _pending_shortcodes()
    todo = []
    for post in load_posts():
        sc = post["shortcode"]
        if sc in pending:
            continue
        ex = read_json(EXTRACTED_DIR / f"{sc}.json")
        fetched = _fetched_ok(sc)
        if fetched and fetched.get("is_video") and not (TRANSCRIPT_DIR / f"{sc}.json").exists():
            continue  # wait until the reel has been transcribed
        if ex is None or (ex.get("sources") == "caption" and fetched and is_unresolved(ex)):
            todo.append({**post, **(fetched or {})})

    existing = [int(f.stem.split("_")[1]) for f in list(MANUAL_DIR.glob("batch_*.md")) + list(DONE_DIR.glob("batch_*.md"))]
    num = max(existing, default=0)
    made = 0
    for start in range(0, len(todo), batch_size):
        if max_batches and made >= max_batches:
            break
        num += 1
        made += 1
        result_path = (MANUAL_DIR / f"batch_{num:03d}.result.json").as_posix()
        out = [INSTRUCTIONS.format(num=num, result_path=result_path)]
        for f in todo[start:start + batch_size]:
            sc = f["shortcode"]
            if "/reels/" in (f.get("final_url") or ""):
                # Reels open in the scrolling feed: anything after the first reel belongs
                # to other people's videos, so keep only the cover image and the top text.
                f = {**f, "images": (f.get("images") or [])[:1],
                     "page_text": (f.get("page_text") or "").split("\n… more")[0][:600],
                     "alt_texts": [a for a in f.get("alt_texts") or [] if a != "Audio image"][:1]}
            transcript = read_json(TRANSCRIPT_DIR / f"{sc}.json") or {}
            lines = [f"## `{sc}`  {f['url']}" + (f"  by @{f['author']}" if f.get("author") else "")]
            if f.get("locations"):
                lines.append("Location tag: " + "; ".join(l["name"] for l in f["locations"]))
            if f.get("caption") or f.get("og_description"):
                lines.append("Caption: " + _clip(f.get("caption") or f["og_description"], 2500))
            if f.get("hashtags"):
                lines.append("Hashtags: " + " ".join("#" + h for h in f["hashtags"][:15]))
            if f.get("alt_texts"):
                lines.append("Alt text: " + " | ".join(_clip(a, 300) for a in f["alt_texts"][:8]))
            if transcript.get("text"):
                lines.append("Transcript: " + _clip(transcript["text"], 2500))
            if f.get("page_text"):
                lines.append("Page text: " + _clip(f["page_text"], 1200).replace("\n", " / "))
            if f.get("images"):
                lines.append("Images: " + ", ".join((FETCHED_DIR / n).as_posix() for n in f["images"][:3]))
            out.append("\n".join(lines))
        (MANUAL_DIR / f"batch_{num:03d}.md").write_text("\n\n".join(out) + "\n", encoding="utf-8")
        print(f"  batch_{num:03d}.md  ({len(todo[start:start + batch_size])} posts)")
    left = len(todo) - min(len(todo), made * batch_size)
    print(f"Made {made} batches" + (f"; {left} posts still unbatched" if left else ""))


def import_results() -> None:
    imported = failed = 0
    for result_file in sorted(MANUAL_DIR.glob("batch_*.result.json")):
        batch_md = MANUAL_DIR / result_file.name.replace(".result.json", ".md")
        expected = set()
        if batch_md.exists():
            expected = {line.split("`")[1] for line in batch_md.read_text(encoding="utf-8").splitlines()
                        if line.startswith("## `")}
        try:
            items = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  {result_file.name}: not valid JSON ({e})")
            failed += 1
            continue

        errors, seen = [], set()
        for item in items:
            sc = item.get("shortcode")
            try:
                parsed = PostExtraction.model_validate(
                    {k: v for k, v in item.items() if k not in ("shortcode", "needs_more")})
            except ValidationError as e:
                errors.append(f"{sc}: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
                continue
            write_json(EXTRACTED_DIR / f"{sc}.json",
                       {"shortcode": sc, "model": "claude (in chat)", "status": "ok",
                        "sources": "caption+page" if _fetched_ok(sc) else "caption",
                        **({"needs_more": bool(item["needs_more"])} if "needs_more" in item else {}),
                        **parsed.model_dump()})
            seen.add(sc)
            imported += 1

        missing = expected - seen
        if errors or missing:
            failed += 1
            print(f"  {result_file.name}: {len(seen)} ok, {len(errors)} invalid, {len(missing)} missing")
            for err in errors[:10]:
                print(f"    {err}")
            if missing:
                print(f"    missing: {', '.join(sorted(missing))}")
        else:
            for f in (result_file, batch_md):
                if f.exists():
                    shutil.move(str(f), DONE_DIR / f.name)
            print(f"  {result_file.name}: {len(seen)} posts imported")
    print(f"Imported {imported} posts" + (f"; {failed} batch files need fixing" if failed else ""))
