"""Stage 6: write the results as a spreadsheet (CSV), a Google My Maps file (KML)
and an interactive map you can open in any browser (map.html)."""
import csv
import json
from pathlib import Path
from xml.sax.saxutils import escape

from .paths import OUTPUT, PLACES_FILE, read_json

COLUMNS = ["name", "category", "city", "region", "country", "neighborhood", "address", "details",
           "confidence", "precision", "lat", "lng", "found_address", "maps_url",
           "saved_count", "post_urls", "post_author", "post_summary", "saved_at"]


def _maps_link(p: dict) -> str:
    if p.get("maps_url"):
        return p["maps_url"]
    from urllib.parse import quote
    q = ", ".join(x for x in (p["name"], p.get("address"), p.get("city"), p.get("country")) if x)
    return f"https://www.google.com/maps/search/?api=1&query={quote(q)}"


def write_csv(places: list[dict], path: Path) -> None:
    # utf-8-sig so Excel shows accented place names correctly
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for p in places:
            w.writerow({**p, "maps_url": _maps_link(p),
                        "post_urls": " ".join(p.get("post_urls") or [p["post_url"]])})


def write_mymaps_csv(places: list[dict], path: Path) -> None:
    """A Google My Maps-friendly CSV: Latitude/Longitude first (auto-detected),
    only rows that have coordinates, and fewer columns so pins stay readable."""
    cols = ["Latitude", "Longitude", "Name", "Category", "Details", "City", "Address",
            "Precision", "Google Maps", "Instagram"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in places:
            if p["lat"] is None:
                continue
            w.writerow([p["lat"], p["lng"], p["name"], p["category"], p.get("details") or "",
                        p.get("city") or "", p.get("found_address") or p.get("address") or "",
                        p["precision"], _maps_link(p),
                        " ".join(p.get("post_urls") or [p["post_url"]])])


def write_kml(places: list[dict], path: Path) -> None:
    by_cat: dict[str, list[dict]] = {}
    for p in places:
        if p["lat"] is not None:
            by_cat.setdefault(p["category"], []).append(p)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Instagram saved places</name>']
    for cat, items in sorted(by_cat.items()):
        out.append(f"<Folder><name>{escape(cat.title())}</name>")
        for p in items:
            desc = "<br>".join(escape(x) for x in (p.get("details"), p.get("found_address"), p["post_url"]) if x)
            out.append(f"<Placemark><name>{escape(p['name'])}</name>"
                       f"<description><![CDATA[{desc}]]></description>"
                       f"<Point><coordinates>{p['lng']},{p['lat']},0</coordinates></Point></Placemark>")
        out.append("</Folder>")
    out.append("</Document></kml>")
    path.write_text("\n".join(out), encoding="utf-8")


def write_map(places: list[dict], path: Path) -> None:
    here = Path(__file__).parent
    vendor = here / "vendor"
    # Leaflet is bundled inline so the map works offline and in previews that block CDNs.
    css = "\n".join((vendor / f).read_text(encoding="utf-8")
                    for f in ("leaflet.css", "MarkerCluster.css", "MarkerCluster.Default.css"))
    js = "\n".join((vendor / f).read_text(encoding="utf-8").replace("</script", "<\\/script")
                   for f in ("leaflet.js", "markercluster.js"))
    data = [{**p, "maps_url": _maps_link(p)} for p in places]
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (here / "map_template.html").read_text(encoding="utf-8")
    # Plain str.replace (not re.sub) so backslashes in the library code stay intact.
    html = html.replace("__VENDOR_CSS__", css).replace("__VENDOR_JS__", js)
    path.write_text(html.replace("__PLACES_JSON__", payload), encoding="utf-8")


def run() -> None:
    places = read_json(PLACES_FILE)
    if places is None:
        raise SystemExit("No places yet. Run the geocode step first.")
    write_csv(places, OUTPUT / "places.csv")
    write_mymaps_csv(places, OUTPUT / "places_mymaps.csv")
    write_kml(places, OUTPUT / "places.kml")
    write_map(places, OUTPUT / "map.html")
    print(f"Wrote {len(places)} places to:\n  {OUTPUT / 'places.csv'}\n  {OUTPUT / 'places.kml'}\n  {OUTPUT / 'map.html'}")
