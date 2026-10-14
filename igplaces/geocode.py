"""Stage 5: turn place names into map coordinates.

Uses Google Places if GOOGLE_MAPS_API_KEY is set (best for restaurants/shops),
otherwise OpenStreetMap's free Nominatim service (1 request/second limit).
If a specific place can't be found, falls back to its city and marks it approximate.
Every lookup is cached, so re-runs are instant.
"""
import os
import time

import requests

from .paths import EXTRACTED_DIR, GEOCODE_CACHE, PLACES_FILE, load_posts, read_json, write_json

USER_AGENT = "igplaces/1.0 (personal saved-posts map)"
_last_nominatim = 0.0


def _nominatim(query: str) -> dict | None:
    global _last_nominatim
    wait = 1.1 - (time.time() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim = time.time()
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": query, "format": "jsonv2", "limit": 1, "accept-language": "en"},
                     headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    hits = r.json()
    if not hits:
        return None
    h = hits[0]
    return {"lat": float(h["lat"]), "lng": float(h["lon"]), "address": h.get("display_name"),
            "maps_url": None, "source": "osm"}


def _google(query: str, key: str) -> dict | None:
    r = requests.post("https://places.googleapis.com/v1/places:searchText",
                      json={"textQuery": query, "maxResultCount": 1},
                      headers={"X-Goog-Api-Key": key,
                               "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.googleMapsUri"},
                      timeout=20)
    r.raise_for_status()
    places = r.json().get("places") or []
    if not places:
        return None
    p = places[0]
    return {"lat": p["location"]["latitude"], "lng": p["location"]["longitude"],
            "address": p.get("formattedAddress"), "maps_url": p.get("googleMapsUri"), "source": "google"}


def _join(*parts) -> str:
    return ", ".join(p for p in parts if p)


def _merge_duplicates(places: list[dict]) -> list[dict]:
    """The same spot is often saved from several posts: keep one entry per
    (name, city) with every source post and the most precise location."""
    import re
    rank = {"exact": 0, "area": 1, "approximate": 2, "not_found": 3}
    conf = {"high": 0, "medium": 1, "low": 2}
    merged: dict[tuple, dict] = {}
    for p in places:
        key = (re.sub(r"[^a-z0-9]", "", p["name"].lower()), (p.get("city") or "").lower())
        p = {**p, "post_urls": [p["post_url"]]}
        if key not in merged:
            merged[key] = p
            continue
        m = merged[key]
        m["post_urls"] = list(dict.fromkeys(m["post_urls"] + p["post_urls"]))
        if p["details"] and p["details"] not in m["details"]:
            m["details"] = f"{m['details']} {p['details']}".strip()
        if rank[p["precision"]] < rank[m["precision"]]:
            for k in ("lat", "lng", "found_address", "maps_url", "precision", "address"):
                m[k] = p[k]
        if conf[p["confidence"]] < conf[m["confidence"]]:
            m["confidence"] = p["confidence"]
    out = list(merged.values())
    for p in out:
        p["saved_count"] = len(p["post_urls"])
    return out


def run() -> None:
    google_key = os.getenv("GOOGLE_MAPS_API_KEY")
    print("Geocoding with " + ("Google Places" if google_key else "OpenStreetMap (free, ~1 lookup/sec)"))
    cache: dict = read_json(GEOCODE_CACHE, {})

    def lookup(query: str, use_google: bool):
        key = ("g:" if use_google else "o:") + query.lower()
        if key not in cache:
            try:
                cache[key] = _google(query, google_key) if use_google else _nominatim(query)
            except requests.RequestException as e:
                print(f"    lookup failed for {query!r}: {e}")
                return None  # don't cache failures
            if len(cache) % 20 == 0:
                write_json(GEOCODE_CACHE, cache)
        return cache[key]

    places = []
    posts = {p["shortcode"]: p for p in load_posts()}
    for sc, post in posts.items():
        extracted = read_json(EXTRACTED_DIR / f"{sc}.json")
        if not extracted:
            continue
        for pl in extracted.get("places", []):
            specific = [_join(pl["name"], pl["address"], pl["city"], pl["country"]),
                        _join(pl["name"], pl["city"], pl["region"], pl["country"])]
            if pl["address"]:  # street addresses often match even when the shop name isn't in the map data
                specific.append(_join(pl["address"], pl["city"], pl["country"]))
            area = _join(pl["neighborhood"], pl["city"], pl["region"], pl["country"])
            def in_country(h):  # reject matches on the wrong side of the world
                return h and (not pl["country"] or not h.get("address")
                              or pl["country"].lower() in h["address"].lower())

            hit, precision = None, None
            for q in dict.fromkeys(specific):
                hit = lookup(q, bool(google_key))
                hit = hit if in_country(hit) else None
                if hit:
                    precision = "exact" if pl["category"] not in ("city", "neighborhood") else "area"
                    break
            if not hit and area:
                hit = lookup(area, False)
                precision = "approximate" if hit else None

            places.append({
                **pl,
                "shortcode": sc,
                "post_url": post["url"],
                "post_author": post.get("author"),
                "saved_at": post.get("saved_at"),
                "post_summary": extracted.get("summary", ""),
                "lat": hit["lat"] if hit else None,
                "lng": hit["lng"] if hit else None,
                "found_address": hit["address"] if hit else None,
                "maps_url": hit.get("maps_url") if hit else None,
                "precision": precision or "not_found",
            })
            print(f"  {pl['name'][:40]:40} {precision or 'not found'}")

    write_json(GEOCODE_CACHE, cache)
    places = _merge_duplicates(places)
    write_json(PLACES_FILE, places)
    found = sum(p["lat"] is not None for p in places)
    print(f"{len(places)} places, {found} on the map ({len(places) - found} not found)")
