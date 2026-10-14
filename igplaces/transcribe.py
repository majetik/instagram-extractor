"""Stage 3 (reels only): download each reel's audio and transcribe it locally
with Whisper. Lots of travel reels only say the place name out loud.

Uses the cookies saved by `login`/`fetch`, and runs entirely on your machine
(no API cost). The first run downloads the Whisper model (~500 MB for "small").
"""
import random
import time

from .paths import AUDIO_DIR, COOKIES_FILE, FETCHED_DIR, TRANSCRIPT_DIR, load_posts, read_json, write_json


def load_audio(path: str):
    """Decode to 16 kHz mono float32. We do this ourselves because faster-whisper's
    own decoder passes an argument that newer PyAV versions no longer accept."""
    import av
    import numpy as np

    chunks = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(path) as container:
        for frame in container.decode(container.streams.audio[0]):
            chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray() for f in resampler.resample(None))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0


def run(model_size: str = "small", limit: int | None = None, retry_failed: bool = False) -> None:
    from faster_whisper import WhisperModel
    from yt_dlp import YoutubeDL

    todo = []
    for post in load_posts():
        sc = post["shortcode"]
        fetched = read_json(FETCHED_DIR / f"{sc}.json")
        existing = read_json(TRANSCRIPT_DIR / f"{sc}.json")
        if fetched and fetched.get("status") == "ok" and fetched.get("is_video") \
                and (existing is None or (retry_failed and existing.get("error"))):
            todo.append(post)
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} reels to transcribe")
    if not todo:
        return
    if not COOKIES_FILE.exists():
        raise SystemExit("No saved session. Run  python run.py login  first.")

    print(f"Loading Whisper '{model_size}' model...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    for i, post in enumerate(todo, 1):
        sc = post["shortcode"]
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(AUDIO_DIR / f"{sc}.%(ext)s"),
            "cookiefile": str(COOKIES_FILE),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(post["url"], download=True)
                audio_path = ydl.prepare_filename(info)
            segments, meta = model.transcribe(load_audio(audio_path), vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            write_json(TRANSCRIPT_DIR / f"{sc}.json",
                       {"shortcode": sc, "language": meta.language, "text": text})
            print(f"[{i}/{len(todo)}] {sc} ({meta.language}) {text[:80]!r}")
        except Exception as e:
            # Record the failure so we don't retry forever; delete the file to retry.
            write_json(TRANSCRIPT_DIR / f"{sc}.json",
                       {"shortcode": sc, "text": "", "error": f"{type(e).__name__}: {e}"[:300]})
            print(f"[{i}/{len(todo)}] {sc} failed: {e}")
        finally:
            for f in AUDIO_DIR.glob(f"{sc}.*"):
                f.unlink(missing_ok=True)
        time.sleep(random.uniform(3, 6))
