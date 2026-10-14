# Instagram saved posts → places map

Turns your saved Instagram posts into a spreadsheet and an interactive map of the restaurants, shops and travel spots they mention.

```
parse → fetch → transcribe → extract → geocode → export
export     your     reel audio   Claude     map        CSV, KML,
file       browser  (Whisper)    reads it   coords     map.html
```

Each step saves its results in `data/`. If a step stops partway (Instagram rate limit, closed laptop), run it again and it continues where it left off.

## One-time setup (Windows)

1. Install **Python 3.12** from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** in the installer.
2. In a terminal opened in this folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   playwright install chromium
   ```
3. Only if you use the paid API route: copy `.env.example` to `.env` and add your Anthropic API key. You don't need this for the chat route below.

## Get your export

Instagram → **Settings → Accounts Center → Your information and permissions → Download your information**. Choose **Some of your information → Saved**, format **JSON**, then download the .zip when it's ready. This usually takes from a few minutes to a few hours.

## Run it

```
python run.py parse path\to\instagram-export.zip
python run.py login            # a browser opens: log in to Instagram yourself
python run.py collection "my collection" --user <your_username>   # optional: keep only one saved collection
python run.py fetch --limit 10 # try a few first
python run.py fetch            # then the rest (~5–10 s per post)
python run.py transcribe       # reels: audio → text, runs locally
python run.py prepare          # bundles posts into data/manual/batch_NNN.md
                               # → ask Claude in Claude Code to process the batches
python run.py import           # checks and loads Claude's results
python run.py geocode
python run.py export
```

Open `output/map.html` in your browser. `output/places.csv` opens in Excel or Google Sheets. To use Google My Maps (which then shows up in the Google Maps app under **You → Maps**), import `output/places_mymaps.csv` or `output/places.kml`.

`python run.py status` shows how far each step has got.

### Online map

To view the map on any device, publish it as a GitHub Pages site. One-time setup (needs the GitHub CLI, `gh`):

```
mkdir site
git -C site init
copy output\map.html site\index.html
gh repo create my-places-map --public --source site --push
```

Then in the new repo go to **Settings → Pages**, pick the `main` branch, and the map goes live at `https://<you>.github.io/my-places-map/`. After any re-run of `export`, update it with:

```
python run.py publish
```

This copies only `output/map.html` into the `site/` folder (its own git repo) and pushes it. Nothing else is uploaded; `data/` contains your Instagram session and must stay private.

## Things to know

- **Instagram limits.** `fetch` goes slowly through your own logged-in browser so it looks like normal browsing. If it hits several errors in a row, it stops: wait an hour and run it again. Automated access is technically against Instagram's terms, so use it for personal use only, at this gentle pace.
- **Extraction routes.** The chat route (`prepare` → Claude → `import`) is covered by a Claude subscription but counts against its usage limits, so expect it to take a few sessions for hundreds of posts. Each batch is kept short, and Claude only opens photos when the text doesn't name the place. The API route (`extract`) runs on its own but is billed separately. Both save results in the same format, and you can mix them.
- **API cost.** Running `extract` with Claude Opus 5.5 costs very roughly $4–8 per 100 posts, and photos make up most of that. To spend much less, set `IGP_MODEL=claude-haiku-5-5` (well under $1 per 100 posts) or `IGP_EFFORT=low` in `.env`. Run `extract --limit 20` first to check quality and cost.
- **Geocoding.** By default this uses OpenStreetMap, which is free but misses some small shops and restaurants. A `GOOGLE_MAPS_API_KEY` (Places API (New)) gives much better matches. If a specific place can't be found, it's pinned at its city and shown as a dashed "approximate" marker.
- **Redo a step.** Delete the files for a post under `data/<step>/`, or use `extract --redo` / `fetch --retry-failed`.
