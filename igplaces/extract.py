"""Stage 4: send each post's caption, location tag, alt text, transcript and
photos to Claude and get back a structured list of the places it mentions.
"""
import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
from pydantic import ValidationError

from .paths import EXTRACTED_DIR, FETCHED_DIR, TRANSCRIPT_DIR, load_posts, read_json, write_json
from .schema import PostExtraction

MODEL = os.getenv("IGP_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("IGP_EFFORT", "medium")
WORKERS = int(os.getenv("IGP_WORKERS", "4"))
MAX_IMAGES = 4

SCHEMA = PostExtraction.model_json_schema()

SYSTEM = """You extract travel places from saved Instagram posts so they can be put on a map.

You get whatever was captured from the post: caption, Instagram location tag, image alt text, \
visible page text (may include comments), an audio transcript for reels, and some of the photos. \
Read text that appears in the photos too (signs, menus, on-screen captions).

Rules:
- List every specific place the post recommends or shows: restaurants, cafes, bars, shops, hotels, \
sights, beaches, viewpoints, hikes and so on. List-style posts ("10 best cafes in Lisbon") produce one entry per place.
- Only use the city/area itself as a place (category "city" or "neighborhood") when the post is about \
the destination in general and names no specific spots.
- The location tag is strong evidence but can be vague (a whole country) or wrong; prefer specific names from the content.
- Fill city and country whenever you can reasonably infer them, since they are needed to find the place on a map.
- Comments from other users may answer "where is this?"; use them, with lower confidence.
- Don't invent places. If the post has no identifiable place (a meme, a recipe, a workout), return an empty list.
- Write details in English, concisely."""


def _media_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")


def _build_content(fetched: dict, transcript: dict | None) -> list[dict]:
    content: list[dict] = []
    for name in (fetched.get("images") or [])[:MAX_IMAGES]:
        path = FETCHED_DIR / name
        if path.exists() and path.stat().st_size < 4_500_000:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": _media_type(name),
                "data": base64.b64encode(path.read_bytes()).decode()}})

    parts = [f"Post URL: {fetched['url']}", f"Type: {'reel/video' if fetched.get('is_video') else 'photo post'}"]
    if fetched.get("author"):
        parts.append(f"Posted by: @{fetched['author']}")
    if fetched.get("locations"):
        parts.append("Instagram location tag: " + "; ".join(l["name"] for l in fetched["locations"]))
    if fetched.get("og_title"):
        parts.append(f"Title: {fetched['og_title']}")
    if fetched.get("og_description"):
        parts.append(f"Caption (from page metadata):\n{fetched['og_description']}")
    if fetched.get("alt_texts"):
        parts.append("Image alt text:\n" + "\n".join(f"- {a}" for a in fetched["alt_texts"][:12]))
    if transcript and transcript.get("text"):
        parts.append(f"Audio transcript ({transcript.get('language', '?')}):\n{transcript['text'][:6000]}")
    if fetched.get("page_text"):
        parts.append(f"Visible page text (caption and comments):\n{fetched['page_text']}")
    content.append({"type": "text", "text": "\n\n".join(parts)})
    return content


def extract_one(client: anthropic.Anthropic, sc: str) -> dict:
    fetched = read_json(FETCHED_DIR / f"{sc}.json")
    transcript = read_json(TRANSCRIPT_DIR / f"{sc}.json")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": _build_content(fetched, transcript)}],
    )
    result = {"shortcode": sc, "model": response.model}
    if response.stop_reason == "refusal":
        return {**result, "status": "refused", "places": []}
    if response.stop_reason == "max_tokens":
        return {**result, "status": "error", "error": "response cut off (max_tokens)", "places": []}
    texts = [b.text for b in response.content if b.type == "text"]
    parsed = PostExtraction.model_validate_json(texts[-1])
    return {**result, "status": "ok", **parsed.model_dump()}


def run(limit: int | None = None, redo: bool = False) -> None:
    todo = []
    for post in load_posts():
        sc = post["shortcode"]
        fetched = read_json(FETCHED_DIR / f"{sc}.json")
        if not fetched or fetched.get("status") != "ok":
            continue
        existing = read_json(EXTRACTED_DIR / f"{sc}.json")
        if redo or existing is None or existing.get("status") == "error":
            todo.append(sc)
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} posts to extract with {MODEL} (effort={EFFORT})")
    if not todo:
        return

    client = anthropic.Anthropic(max_retries=5)
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(extract_one, client, sc): sc for sc in todo}
        for fut in as_completed(futures):
            sc = futures[fut]
            done += 1
            try:
                result = fut.result()
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
                pool.shutdown(cancel_futures=True)
                raise SystemExit(f"Claude API rejected the key: {e}. Check ANTHROPIC_API_KEY in .env")
            except (anthropic.APIError, ValidationError, json.JSONDecodeError) as e:
                result = {"shortcode": sc, "status": "error", "error": f"{type(e).__name__}: {e}"[:500], "places": []}
            write_json(EXTRACTED_DIR / f"{sc}.json", result)
            names = ", ".join(p["name"] for p in result.get("places", [])) or "-"
            print(f"[{done}/{len(todo)}] {sc} {result['status']}: {names[:100]}")
