"""Stage 2: open each saved post in a real browser (your logged-in session)
and capture its caption, location tag, alt text, page text and images.

Runs slowly on purpose (a few seconds between posts) so Instagram treats it like
normal browsing. Results are cached per post, so you can stop and resume anytime.
"""
import random
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .paths import BROWSER_PROFILE, COOKIES_FILE, FETCHED_DIR, load_posts, read_json, write_json

MAX_IMAGES = 6
MAX_CONSECUTIVE_ERRORS = 5

# Runs inside the page. Grabs everything that might mention a place.
SNAPSHOT_JS = r"""() => {
  const meta = n => (document.querySelector(`meta[property="${n}"]`) ||
                     document.querySelector(`meta[name="${n}"]`))?.content || null;
  const main = document.querySelector('main') || document.body;
  const code = (location.pathname.match(/\/(?:p|reels?|tv)\/([^/]+)/) || [])[1] || '';
  // Thumbnails of *other* posts (the "more posts" grid, the next reels) link elsewhere.
  const isOther = el => {
    const a = el.closest('a[href]');
    return a && /\/(p|reels?|tv)\//.test(a.href) && !a.href.includes(code);
  };
  const locations = [...document.querySelectorAll('a[href*="/explore/locations/"]')]
    .map(a => ({name: a.innerText.trim(), href: a.href}))
    .filter(l => l.name && l.name !== 'Locations');
  const imgs = [...main.querySelectorAll('img')].filter(i => !isOther(i));
  const alts = imgs.map(i => (i.alt || '').trim())
    .filter(a => a && !/profile picture|^audio image$/i.test(a));
  const images = imgs.filter(i => Math.max(i.naturalWidth, i.width) >= 300)
    .map(i => i.currentSrc || i.src).filter(s => s && s.startsWith('http'));
  return {
    og_title: meta('og:title'), og_description: meta('og:description'),
    og_image: meta('og:image'), og_type: meta('og:type'),
    page_text: (main.innerText || '').split(/\nMore posts from /)[0].slice(0, 6000),
    locations, alt_texts: alts, image_urls: images,
    has_video: !!main.querySelector('video'), final_url: location.href,
  };
}"""


def _open_context(p, headless: bool):
    return p.chromium.launch_persistent_context(
        str(BROWSER_PROFILE),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )


def _save_cookies(context) -> None:
    """Export the session cookies in Netscape format so yt-dlp can fetch reel audio."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in context.cookies():
        if "instagram.com" not in c["domain"]:
            continue
        lines.append("\t".join([
            c["domain"],
            "TRUE" if c["domain"].startswith(".") else "FALSE",
            c["path"],
            "TRUE" if c["secure"] else "FALSE",
            str(int(c["expires"])) if c["expires"] and c["expires"] > 0 else "0",
            c["name"],
            c["value"],
        ]))
    COOKIES_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _has_session(ctx) -> bool:
    return any(c["name"] == "sessionid" and "instagram.com" in c["domain"] for c in ctx.cookies())


def login(timeout_min: int = 10) -> None:
    """Open a browser window so you can log in to Instagram yourself, once.
    Closes on its own a few seconds after the login succeeds."""
    with sync_playwright() as p:
        ctx = _open_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/")
        print("Log in to Instagram in the browser window that opened...", flush=True)
        deadline = time.time() + timeout_min * 60
        while not _has_session(ctx):
            if time.time() > deadline:
                ctx.close()
                raise SystemExit("Timed out waiting for login.")
            page.wait_for_timeout(2000)
        page.wait_for_timeout(5000)  # let "save login info?" etc. settle
        _save_cookies(ctx)
        ctx.close()
    print("Saved your session. You won't need to log in again unless it expires.")


def collection(name: str, user: str | None = None) -> dict[str, str]:
    """Open one of your saved collections and return the shortcodes of every post in it."""
    from .parse_export import URL_RE

    with sync_playwright() as p:
        ctx = _open_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if _is_logged_out(page) or not _has_session(ctx):
            ctx.close()
            raise SystemExit("Not logged in. Run  python run.py login  first.")
        if not user:
            user = page.evaluate("""() => {
              const a = [...document.querySelectorAll('a[href]')].find(a => /^profile$/i.test(a.innerText.trim()));
              return a ? new URL(a.href).pathname.split('/').filter(Boolean)[0] : null; }""")
        if not user:
            ctx.close()
            raise SystemExit("Couldn't work out your username. Pass it with --user yourname")
        print(f"Opening saved collections for @{user}")
        page.goto(f"https://www.instagram.com/{user}/saved/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        links = page.evaluate("""() => [...document.querySelectorAll('a[href*="/saved/"]')]
            .map(a => ({href: a.href, text: a.innerText.trim()}))""")
        wanted = name.strip().lower()
        match = next((l for l in links if wanted in l["text"].lower()), None) \
            or next((l for l in links if wanted.replace(" ", "-") in l["href"].lower()), None)
        if not match:
            names = sorted({l["text"].split("\n")[0] for l in links if l["text"]})
            ctx.close()
            raise SystemExit(f"No collection called {name!r}. Found: {names}")

        page.goto(match["href"], wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        found: dict[str, str] = {}  # shortcode -> "p" | "reel"
        idle = 0
        while idle < 4:
            hrefs = page.evaluate("() => [...document.querySelectorAll('main a[href]')].map(a => a.href)")
            before = len(found)
            for h in hrefs:
                if (m := URL_RE.search(h)) and m.group(2) not in found:
                    found[m.group(2)] = "p" if m.group(1) == "p" else "reel"
            idle = 0 if len(found) > before else idle + 1
            print(f"  {len(found)} posts so far", flush=True)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(random.randint(1500, 2500))
        _save_cookies(ctx)
        ctx.close()
    return found


def _is_logged_out(page) -> bool:
    return "/accounts/login" in page.url or page.locator('input[name="username"]').count() > 0


def _snapshot_post(page) -> dict:
    # Expand a truncated caption if there's a "more" button.
    try:
        page.locator("main").get_by_text("more", exact=True).first.click(timeout=1500)
        page.wait_for_timeout(400)
    except Exception:
        pass

    snap = page.evaluate(SNAPSHOT_JS)
    alts, images = list(snap["alt_texts"]), list(snap["image_urls"])

    # Step through gallery slides so we see every photo, not just the first.
    for _ in range(12):
        nxt = page.locator('main button[aria-label="Next"]')
        if nxt.count() == 0 or len(images) >= MAX_IMAGES * 2:
            break
        try:
            nxt.first.click(timeout=1500)
        except Exception:
            break
        page.wait_for_timeout(700)
        more = page.evaluate(SNAPSHOT_JS)
        alts += more["alt_texts"]
        images += more["image_urls"]

    snap["alt_texts"] = list(dict.fromkeys(alts))
    snap["image_urls"] = list(dict.fromkeys(images))
    return snap


def _download_images(context, shortcode: str, urls: list[str]) -> list[str]:
    saved = []
    for url in urls:
        if len(saved) >= MAX_IMAGES:
            break
        try:
            resp = context.request.get(url, timeout=20000)
            ctype = resp.headers.get("content-type", "")
            if not resp.ok or not ctype.startswith("image/"):
                continue
            ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(ctype, "jpg")
            name = f"{shortcode}_{len(saved)}.{ext}"
            (FETCHED_DIR / name).write_bytes(resp.body())
            saved.append(name)
        except Exception:
            continue
    return saved


def run(limit: int | None = None, headless: bool = False, retry_failed: bool = False,
        only_unresolved: bool = False) -> None:
    posts = load_posts()
    if only_unresolved:
        from .manual import is_unresolved
        from .paths import EXTRACTED_DIR
        posts = [p for p in posts if (ex := read_json(EXTRACTED_DIR / f"{p['shortcode']}.json")) is not None
                 and is_unresolved(ex)]
        print(f"{len(posts)} extracted posts still need their photos/video checked")
    todo = []
    for post in posts:
        cached = read_json(FETCHED_DIR / f"{post['shortcode']}.json")
        if cached is None or (retry_failed and cached.get("status") == "error"):
            todo.append(post)
    if limit:
        todo = todo[:limit]
    print(f"{len(posts) - len(todo)} already fetched, {len(todo)} to go")
    if not todo:
        return

    with sync_playwright() as p:
        ctx = _open_context(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        errors_in_a_row = 0

        for i, post in enumerate(todo, 1):
            sc = post["shortcode"]
            record = {"shortcode": sc, "url": post["url"], "kind": post["kind"],
                      "author": post.get("author"), "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            try:
                page.goto(post["url"], wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_selector("main", timeout=15000)
                except PWTimeout:
                    pass
                page.wait_for_timeout(random.randint(1500, 2500))

                if _is_logged_out(page):
                    raise SystemExit("\nInstagram logged you out. Run  python run.py login  and then fetch again.")

                body = page.locator("body").inner_text()
                if any(s in body for s in ("Sorry, this page isn't available", "Post isn't available")):
                    record["status"] = "unavailable"
                else:
                    snap = _snapshot_post(page)
                    record.update(snap)
                    record["images"] = _download_images(
                        ctx, sc, ([snap["og_image"]] if snap["og_image"] else []) + snap["image_urls"])
                    record["is_video"] = (post["kind"] == "reel" or snap["has_video"]
                                          or "video" in (snap["og_type"] or ""))
                    record["status"] = "ok"
                errors_in_a_row = 0
            except SystemExit:
                raise
            except Exception as e:
                record["status"] = "error"
                record["error"] = f"{type(e).__name__}: {e}"[:500]
                errors_in_a_row += 1

            write_json(FETCHED_DIR / f"{sc}.json", record)
            loc = record.get("locations") or []
            print(f"[{i}/{len(todo)}] {sc} {record['status']}"
                  + (f"  📍 {loc[0]['name']}" if loc else ""))

            if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                print("\nSeveral errors in a row. Instagram may be rate-limiting you. "
                      "Take a break (an hour or so) and run fetch again; it resumes where it stopped.")
                break
            time.sleep(random.uniform(4, 9))

        _save_cookies(ctx)
        ctx.close()
