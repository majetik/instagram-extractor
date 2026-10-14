"""Open output/map.html in a headless browser and report JS errors and marker counts."""
from playwright.sync_api import sync_playwright

from .paths import OUTPUT

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto((OUTPUT / "map.html").as_uri())
    page.wait_for_timeout(2000)
    print("Leaflet loaded:", page.evaluate("typeof L !== 'undefined'"))
    print("List entries:", page.locator("#list li").count())
    print("Header:", page.locator("#count").inner_text())
    print("JS errors:", errors or "none")
    browser.close()
