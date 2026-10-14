"""Combine a post's fetched images into one contact sheet so they can be read in
a single look (much cheaper than opening each slide separately)."""
from pathlib import Path

from PIL import Image

from .paths import DATA, FETCHED_DIR

SHEET_DIR = DATA / "sheets"
SHEET_DIR.mkdir(exist_ok=True)


def make_sheet(shortcode: str, cols: int = 3, cell: int = 640) -> Path | None:
    files = sorted(FETCHED_DIR.glob(f"{shortcode}_*.*"), key=lambda p: int(p.stem.rsplit("_", 1)[1]))
    images = []
    for f in files:
        try:
            im = Image.open(f).convert("RGB")
        except Exception:
            continue
        # og:image is usually the first slide again at another size: skip near-duplicates.
        if images and abs(im.width / im.height - images[0].width / images[0].height) < 0.01 \
                and len(images) == 1 and f.stem.endswith("_1"):
            if im.resize((16, 16)).tobytes() == images[0].resize((16, 16)).tobytes():
                continue
        images.append(im)
    if not images:
        return None
    cols = min(cols, len(images))
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * int(cell * 1.25)), "white")
    for i, im in enumerate(images):
        im.thumbnail((cell, int(cell * 1.25)))
        sheet.paste(im, ((i % cols) * cell, (i // cols) * int(cell * 1.25)))
    out = SHEET_DIR / f"{shortcode}.jpg"
    sheet.save(out, quality=85)
    return out


if __name__ == "__main__":
    import sys
    for sc in sys.argv[1:]:
        print(make_sheet(sc))
