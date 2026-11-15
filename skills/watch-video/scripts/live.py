"""Live watching: follow a screen, a camera, a live stream or a file in (near) real time.

ffmpeg records the source in short chunks; each finished chunk gets a full scan and its events
are printed as one line each, with wall-clock times, and appended to LIVE.md. Run it in the
background and follow the output (for Claude Code: Monitor on the output file), so Claude is
told when something happens instead of re-reading everything.

  watch.py live screen            the main display (needs Screen Recording permission)
  watch.py live camera            the default camera (needs Camera permission)
  watch.py live <url|file>        a live stream, or a file played at real speed (for testing)
"""

from __future__ import annotations

import datetime as dt
import json
from difflib import SequenceMatcher
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import media
from media import MediaError

SKIP_KINDS = {"steady", "soundtrack"}
MAX_EVENTS = 5            # per chunk: what a person would actually remark on
MAX_TEXT = 3


LIVE_FPS = 10              # enough to see a screen or a room; keeps the encoder ahead of real time
STALL_GRACE = 15          # seconds past one chunk with nothing recorded = capture isn't getting frames


def capture_cmd(source: str, chunk: int, out: Path) -> list[str]:
    # macOS devices stamp frames on a microsecond clock that ffmpeg reads as a million fps;
    # wall-clock stamps plus a fixed output rate make chunks close on time.
    device = ["-f", "avfoundation", "-use_wallclock_as_timestamps", "1"]
    if source == "screen":
        inp = [*device, "-pixel_format", "bgr0", "-capture_cursor", "1", "-framerate", str(LIVE_FPS),
               "-i", "Capture screen 0:none"]                    # no microphone unless asked
    elif source == "camera":
        inp = [*device, "-pixel_format", "nv12", "-framerate", "30", "-i", "default:default"]
    elif media.is_url(source):
        inp = ["-i", source]
    else:
        inp = ["-re", "-i", str(Path(source).expanduser().resolve())]
    return ["ffmpeg", "-y", "-v", "error", "-nostdin", *inp,
            "-vf", f"fps={LIVE_FPS},scale='min(1280,iw)':-2,format=yuv420p", "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-force_key_frames", f"expr:gte(t,n_forced*{chunk})",
            "-c:a", "aac", "-f", "segment", "-segment_time", str(chunk), "-segment_format", "mpegts",
            "-reset_timestamps", "1", str(out / "chunk_%05d.ts")]


def stop(proc: subprocess.Popen) -> None:
    """Interrupt like Ctrl-C so ffmpeg closes the current chunk; kill it if it won't go."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def say(line: str, log: Path) -> None:
    print(line, flush=True)
    with log.open("a") as f:
        f.write(line + "\n")


def condense(st: dict, said: list[str]) -> list[tuple[float, str]]:
    """What a person would remark on: events in time order, but small in-place flickers
    (typing, a blinking cursor) grouped per screen area, and screen text said once, briefly."""
    lines, flicker = [], {}
    for e in st["events"]:
        if e["kind"] in SKIP_KINDS:
            continue
        if e["kind"] == "motion" and e["detail"].startswith("small change") and "in place" in e["detail"] \
                and "FLASH" not in e["detail"] and "text now" not in e["detail"]:
            area = e["detail"].split(",")[1].strip()
            flicker.setdefault(area, []).append(e["t"])
            continue
        detail = e["detail"] if len(e["detail"]) <= 200 else e["detail"][:200] + " …"
        lines.append((e["t"], f"{e['kind']}: {detail}", e.get("score", 0)))
    # attention budget: people first (a gesture beats a flicker), then the biggest changes
    events = sorted(lines, key=lambda p: (not p[1].startswith(("body", "face", "crosstalk")), -p[2]))
    lines = [(t, s) for t, s, _ in events[:MAX_EVENTS]]
    if len(events) > MAX_EVENTS:
        lines.append((events[MAX_EVENTS][0], f"…and {len(events) - MAX_EVENTS} smaller visual changes (see WATCH.md)"))
    if flicker:
        where = ", ".join(f"{a} ×{len(ts_)}" for a, ts_ in sorted(flicker.items(), key=lambda p: -len(p[1]))[:4])
        first = min(min(ts_) for ts_ in flicker.values())
        lines.append((first, f"motion: small in-place changes (typing, a cursor, a counter?) at {where}"))
    lines += [(x["start"], f"{x['speaker']} says: {x['text']}") for x in st.get("turns") or []]
    if not st.get("turns"):
        lines += [(x["start"], f"speech: {x['text']}") for x in st["transcript"] if x.get("voice", True)]
    shown = 0
    for x in st.get("text_on_screen") or []:
        text = " | ".join(x["lines"])
        if any(SequenceMatcher(None, text[:300], s[:300]).ratio() > 0.6 for s in said[-30:]):
            continue                                       # the same text, scrolled or still typing
        said.append(text)
        if shown < MAX_TEXT:
            lines.append((x["t"], f"text: {text if len(text) <= 160 else text[:160] + ' …'}"))
        shown += 1
    if shown > MAX_TEXT:
        lines.append((st["info"]["duration"], f"…and {shown - MAX_TEXT} more new pieces of text (see WATCH.md)"))
    return sorted(lines, key=lambda p: p[0])


def scan_chunk(path: Path, k: int, base: dt.datetime, budget: int, log: Path, said: list[str],
               people: bool = True) -> float:
    """Scan one finished chunk; print its events in time order. Returns the chunk's length."""
    import watch  # local import: watch imports this module for its CLI
    out = path.with_suffix("")
    # stream chunks (.ts) start at 1.4 s and seek badly; a quick copy into mp4 fixes both
    mp4 = path.with_suffix(".mp4")
    try:
        media.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-c", "copy", "-avoid_negative_ts", "make_zero",
                   "-movflags", "+faststart", str(mp4)], timeout=120)
        path.unlink()                                            # keep one copy of each chunk
        path = mp4
    except MediaError as exc:
        say(f"- chunk {k}: could not read it ({exc})", log)
        return 0.0
    args = SimpleNamespace(source=str(path), out=str(out), budget=budget, lang="auto",
                           no_speech=False, no_people=not people)   # a screen's "people" are photos and icons
    try:
        watch.scan(args)
    except MediaError as exc:
        say(f"- chunk {k}: scan failed ({exc})", log)
        return media.probe(path)["duration"]
    st = json.loads((out / "state.json").read_text())
    clock = lambda t: (base + dt.timedelta(seconds=t)).strftime("%H:%M:%S")  # noqa: E731
    say(f"\n## {clock(0)}–{clock(st['info']['duration'])} (chunk {k}: `{out}/WATCH.md`)", log)
    lines = condense(st, said)
    for t, line in lines:
        say(f"- {clock(t)} {line}", log)
    if not lines:
        say("- nothing happened", log)
    return st["info"]["duration"]


def live(source: str, out: Path, chunk: int, seconds: float, budget: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    log, errlog = out / "LIVE.md", out / "ffmpeg.log"
    started = dt.datetime.now()
    log.write_text(f"# Live: {source} (started {started:%Y-%m-%d %H:%M:%S}, {chunk}s chunks)\n")
    with errlog.open("w") as err:
        proc = subprocess.Popen(capture_cmd(source, chunk, out), stdin=subprocess.DEVNULL, stderr=err)
    print(f"watching {source} live; log: {log}", file=sys.stderr, flush=True)
    offset = 0.0                                                  # seconds recorded before this chunk
    seen: set[str] = set()                                        # chunks already scanned (by name)
    said: list[str] = []                                          # recent screen text, to avoid repeats
    done, deadline = 0, time.time() + seconds if seconds else float("inf")
    try:
        while True:
            chunks = [c for c in sorted(out.glob("chunk_*.ts")) if c.stat().st_size > 0]
            finished = proc.poll() is not None
            ready = chunks if finished else chunks[:-1]          # the newest is still being written
            if time.time() > deadline and proc.poll() is None:
                stop(proc)                                        # stop recording on time, even mid-scan
                continue
            for c in ready:
                if c.name in seen:
                    continue
                seen.add(c.name)
                offset += scan_chunk(c, done, started + dt.timedelta(seconds=offset), budget, log, said,
                                     people=source != "screen")
                done += 1
            if finished:
                if done == 0:
                    raise MediaError(f"capture failed: {errlog.read_text()[-400:] or 'no frames recorded'}")
                break
            if not chunks and time.time() - started.timestamp() > chunk + STALL_GRACE:
                stop(proc)
                hint = {"screen": "allow Screen & System Audio Recording for Claude in System Settings",
                        "camera": "the app running this can't use the camera. The Claude app can't ask for it, "
                                  "so run camera mode from Terminal (it will ask once), or allow Camera for "
                                  "your terminal app in System Settings"}.get(source, "")
                raise MediaError(f"no frames after {chunk + STALL_GRACE}s" + (f": {hint}" if hint else ""))
            if time.time() > deadline:
                stop(proc)
                continue                                          # scan what was recorded, then stop
            time.sleep(1)
    finally:
        stop(proc)
    say(f"\n(stopped {dt.datetime.now():%H:%M:%S})", log)
    print(log)
