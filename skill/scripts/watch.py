#!/usr/bin/env python3
"""watch-video: let Claude watch a video the way a person does.

  watch.py scan   <file|url> [--out DIR] [--budget 8] [--lang auto] [--no-speech] [--no-people]
  watch.py look   DIR --start S --end E [--n 12] [--strobe]
  watch.py zoom   DIR --t T [--box x,y,w,h] [--stack 9] (normalized, top-left origin)
  watch.py frame  DIR --t T                            (one full-resolution frame)
  watch.py listen DIR --start S --end E               (speech, sounds, loudness in a range)
  watch.py live   screen|camera|<url|file> [--chunk 10] [--for SECONDS]   (watch as it happens)

`scan` is peripheral vision (every frame and every 50 ms of audio, as cheap signals) plus a
first round of attention (contact sheets of the moments that matter). The others are
attention on demand: go back and look closer, like rewinding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import events as EV  # noqa: E402
import media  # noqa: E402
import people as PPL  # noqa: E402
import report  # noqa: E402
import seeing as SEE  # noqa: E402
import signals as S  # noqa: E402
from media import MediaError  # noqa: E402

CACHE = Path.home() / ".cache" / "watch-video"
SCENE_LABEL_CONF = 0.6
GLANCE_TILES, GLANCE_TILE = 8, 224      # the overview image: 8 small frames, about 600 tokens


# ---------------------------------------------------------------- helpers


def out_dir_for(source: str, given: str | None) -> Path:
    if given:
        return Path(given).expanduser().resolve()
    key = source
    p = Path(source).expanduser()
    if p.exists():
        st = p.stat()
        key = f"{p.resolve()}:{st.st_size}:{st.st_mtime}"
    stem = Path(source.split("?")[0].rstrip("/")).stem[:40] or "video"
    return CACHE / f"{stem}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


def load_state(d: Path) -> dict:
    f = Path(d).expanduser() / "state.json"
    if not f.exists():
        raise MediaError(f"no scan in {d}; run `watch.py scan` first")
    return json.loads(f.read_text())


def story_times(info: dict) -> list[float]:
    n = 16 if info["duration"] > 16 else max(4, int(info["duration"]))
    return [round((i + 0.5) * info["duration"] / n, 2) for i in range(n)]


def _soft(future, default, what: str):
    """Audio helpers are optional: report and continue instead of losing the whole scan."""
    if future is None:
        return default
    try:
        return future.result()
    except MediaError as exc:
        print(f"warning: {what} unavailable: {exc}", file=sys.stderr)
        return default


# ---------------------------------------------------------------- scan


def scan(args) -> None:
    out = out_dir_for(args.source, args.out)
    out.mkdir(parents=True, exist_ok=True)
    video = media.fetch(args.source, out) if media.is_url(args.source) else Path(args.source).expanduser().resolve()
    if not video.exists():
        raise MediaError(f"no such file: {video}")
    info = media.probe(video)
    fps = S.analysis_fps(info["duration"], info["fps"])
    _, _, cols, rows = S.grid_shape(info["width"], info["height"])
    print(f"scanning {report.ts(info['duration'])} of video at {fps} fps ...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_motion = pool.submit(S.motion_scan, video, info, fps)
        f_cuts = pool.submit(S.scene_cuts, video, fps)
        wav = media.extract_audio(video, out / "audio.wav") if info["has_audio"] else None
        f_levels = pool.submit(S.loudness, wav, out) if wav else None
        f_sounds = pool.submit(media.vsense_json, "sounds", str(wav)) if wav else None
        f_speech = (pool.submit(media.transcribe, wav, out / "speech", args.lang)
                    if wav and not args.no_speech else None)
        motion, cuts = f_motion.result(), f_cuts.result()
        levels = f_levels.result() if f_levels else []
        sound_windows = _soft(f_sounds, [], "sound types")
        speech_res = _soft(f_speech, {"language": None, "segments": []}, "speech")

    speech = [x for x in speech_res["segments"] if EV.is_words(x["text"])]
    if sound_windows:
        voice = S.voice_spans(sound_windows, levels)
        speech = S.check_transcript(speech, voice)
    else:
        voice = [(x["start"], x["end"]) for x in speech]

    # who is talking, turn by turn, and what each voice is like
    talk = {"voices": {}, "turns": [], "overlaps": []}
    if wav and speech:
        try:
            talk = PPL.speakers(wav, speech, levels)
        except MediaError as exc:
            print(f"warning: speaker turns unavailable: {exc}", file=sys.stderr)

    # look around every visual change: text before/after, colour, flashes
    motion_ev, cuts = EV.promote_hidden_cuts(S.motion_events(motion, cuts, fps, cols, rows), cuts)
    SEE.read_around_changes(video, info, motion_ev, out)
    ocr_times = sorted(set(story_times(info) + [c + 0.2 for c in cuts]))
    ocr_imgs = media.frames(video, ocr_times, out / "frames", width=min(1920, info["width"]) or None)
    pairs = list(zip(ocr_times, SEE.ocr_lines(ocr_imgs)))
    pairs += [(e["end"], e["text_after"]) for e in motion_ev if e.get("text_after")]
    pairs += [(max(0.0, e["t"] - 0.05), e["text_before"]) for e in motion_ev if e.get("text_before")]
    pairs.sort(key=lambda p: p[0])
    text_on_screen, persistent = SEE.summarise_text(pairs)
    SEE.annotate_changes(motion_ev, persistent, pairs)
    motion_ev = SEE.confirm_flashes(motion_ev, S.flash_candidates(motion_ev))

    # scene labels for the storyboard frames; people present -> faces, bodies, gestures
    story_t = story_times(info)
    story_imgs = media.frames(video, story_t, out / "frames")
    try:
        tag_res = media.vsense_json("tags", *map(str, story_imgs)) or []
    except MediaError as exc:
        print(f"warning: scene labels unavailable: {exc}", file=sys.stderr)
        tag_res = []
    scenes = [{"t": t, "labels": [l for l, c in r["labels"] if c >= SCENE_LABEL_CONF][:4], "people": r["people"]}
              for t, r in zip(story_t, tag_res)]
    crowd = {}
    if not args.no_people and any(r.get("people") or r.get("faces") for r in tag_res):
        print("people on screen: tracking faces, bodies and who is talking ...", file=sys.stderr)
        crowd = PPL.scan(video, info, out, speech, voice, cuts)
        if talk["turns"] and crowd.get("faces"):
            for t in talk["turns"]:
                segs = [x for x in speech if x["start"] - 0.2 <= t["start"] <= x["end"] + 0.2 and x.get("face")]
                if segs:
                    t["face"] = segs[0]["face"]

    events = EV.build_events(motion_ev, cuts, S.audio_onsets(levels), voice,
                             S.sound_segments(sound_windows or [], levels, voice),
                             S.camera_moves(motion, cuts), S.loudness_peaks(levels),
                             S.steady_motion(motion, fps),
                             crowd.get("events", []) + [
                                 {"kind": "crosstalk", "t": o["start"], "end": o["end"], "score": 0.6,
                                  "detail": f"{' and '.join(o['who'])} talk at the same time (words here may be garbled)"}
                                 for o in talk.get("overlaps", [])])
    windows = EV.attention_windows(events, info["duration"], args.budget)
    shots = list(zip([0.0] + cuts, cuts + [info["duration"]]))

    # first look: storyboard, shots, attention windows
    n_story = len(story_t)
    SEE.sheet(video, story_t, out / "storyboard.jpg", "Storyboard: evenly spaced")
    shots_sheet = None
    if 1 < len(shots) <= 40:
        shots_sheet = str(SEE.sheet(video, EV.shot_frames(shots, motion), out / "shots.jpg",
                                    "One frame per shot (its busiest moment)"))
    (out / "attend").mkdir(exist_ok=True)
    for w in windows:
        n = 12 if w["end"] - w["start"] <= 4 else 16
        w["sheet"] = str(SEE.sheet(video, SEE.evenly(w["start"], w["end"], n), out / "attend" / f"{w['id']}.jpg",
                               f"{w['id']} {report.ts(w['start'], True)}-{report.ts(w['end'], True)}: {', '.join(w['events'])}"))
        if w["has_motion"]:
            w["strobe"] = str(SEE.strobe(video, w["start"], w["end"], out / "attend" / f"{w['id']}_strobe.jpg", cuts))


    spec = report.chart_spec(info, motion, levels, [{"start": a, "end": b} for a, b in voice], cuts, events)
    (out / "chart.json").write_text(json.dumps(spec))
    media.vsense("plot", "--out", str(out / "chart.png"), "--spec", str(out / "chart.json"))

    state = {
        "video": str(video), "info": info, "events": events, "windows": windows, "cuts": cuts,
        "shots": shots, "transcript": speech, "language": speech_res.get("language"),
        "second_ear": [e for e in speech_res.get("second_ear", [])            # only where a voice was heard
                       if not voice or S.in_any((e["start"] + e["end"]) / 2, voice, pad=0.5)],
        "sounds": sound_windows, "text_on_screen": text_on_screen, "persistent_text": persistent,
        "scenes": scenes, "camera": [e for e in events if e["kind"] == "camera"],
        "levels": levels, "voice": voice, "dir": str(out),
        "chart": str(out / "chart.png"), "storyboard": str(out / "storyboard.jpg"), "storyboard_n": n_story,
        "shots_sheet": shots_sheet, "turns": talk["turns"], "voices": talk["voices"],
        "faces": crowd.get("faces", []), "bodies": crowd.get("bodies", []), "face_sheet": crowd.get("face_sheet"),
        "coverage": {"motion_fps": fps,
                     "speech": ("Sheng mode: whisper large-v3-turbo (Swahili + Sheng hint) with a "
                                "Swahili-tuned second ear" if speech_res.get("mode") == "sheng"
                                else "whisper small") if f_speech else "off",
                     "sounds": "Apple SoundAnalysis" if f_sounds else "off",
                     "people": f"faces and body pose at {crowd['fps']} fps" if crowd.get("fps") else "off"},
    }
    # the light path: one small overview image and a one-page brief
    picks = EV.shot_frames(shots, motion) if 1 < len(shots) else story_t
    step = max(1, -(-len(picks) // GLANCE_TILES))
    picks = picks[::step][:GLANCE_TILES]
    glance_dir = out / "frames"
    imgs = media.frames(video, picks, glance_dir, width=GLANCE_TILE)
    media.vsense("sheet", "--out", str(out / "glance.jpg"), "--cols", "4", "--tile", str(GLANCE_TILE),
                 "--title", "At a glance", *[f"{report.ts(t)}={p}" for t, p in zip(picks, imgs)])
    state["glance"] = str(out / "glance.jpg")
    (out / "state.json").write_text(json.dumps(state))
    (out / "WATCH.md").write_text(report.watch_md(info, state))
    (out / "BRIEF.md").write_text(report.brief_md(info, state))
    print(out / "WATCH.md")
    print(out / "BRIEF.md")


# ---------------------------------------------------------------- attention on demand


def look(args) -> None:
    st = load_state(args.dir)
    d, video = Path(args.dir).expanduser(), Path(st["video"])
    start, end = max(0.0, args.start), min(st["info"]["duration"], args.end)
    if end <= start:
        raise MediaError("end must be after start")
    tag = f"{start:08.2f}-{end:08.2f}"
    (d / "looks").mkdir(exist_ok=True)
    out = SEE.sheet(video, SEE.evenly(start, end, args.n), d / "looks" / f"look_{tag}.jpg",
                f"look {report.ts(start, True)}-{report.ts(end, True)} ({args.n} frames)")
    print(out)
    if args.strobe:
        print(SEE.strobe(video, start, end, d / "looks" / f"strobe_{tag}.jpg", st["cuts"]))
        if any(c["t"] < end and c.get("end", c["t"]) > start for c in st.get("camera", [])):
            print("note: the camera moves in this range, so the strobe background will smear")


def zoom(args) -> None:
    st = load_state(args.dir)
    d, video, info = Path(args.dir).expanduser(), Path(st["video"]), st["info"]
    if args.box:
        x, y, w, h = (float(v) for v in args.box.split(","))
    else:
        hit = [e for e in st["events"] if e["kind"] == "motion" and e["t"] - 0.3 <= args.t <= e["end"] + 0.3]
        x, y, w, h = hit[0]["box"] if hit else (0.25, 0.25, 0.5, 0.5)
        # pad the motion box so the thing and its surroundings are both visible
        x, y, w, h = max(0, x - 0.05), max(0, y - 0.05), min(1, w + 0.1), min(1, h + 0.1)
    full = media.frame(video, args.t, d / "frames" / f"full_{args.t:09.3f}.jpg", width=None)
    out = d / "zooms" / f"zoom_{args.t:09.3f}_{x:.2f}_{y:.2f}_{w:.2f}_{h:.2f}.jpg"
    out.parent.mkdir(exist_ok=True)
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(full), "-vf",
               f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=1024:-2:flags=lanczos", str(out)])
    print(out)
    lines = SEE.ocr_lines([out])[0]
    if lines:
        print("text:", " | ".join(lines))
    if args.stack:
        stacked = stack_zoom(video, info, d, args.t, (x, y, w, h), args.stack)
        print(stacked, f"(sharpened from {args.stack} neighbouring frames)")
        lines = SEE.ocr_lines([stacked])[0]
        if lines:
            print("text (stacked):", " | ".join(lines))


def stack_zoom(video: Path, info: dict, d: Path, t: float, box: tuple, n: int) -> Path:
    """Multi-frame zoom: neighbouring frames each catch the detail at a slightly different
    sub-pixel offset; aligning and averaging them recovers detail no single frame has
    (and averages away compression noise). Works for still or slowly moving things."""
    x, y, w, h = box
    step = 1 / max(1.0, info["fps"])
    times = [round(max(0.0, t + (k - n // 2) * step), 3) for k in range(n)]
    crop_dir = d / "zooms" / f"stack_{t:09.3f}"
    crop_dir.mkdir(parents=True, exist_ok=True)
    crops = []
    for k, tt in enumerate(times):
        full = media.frame(video, tt, crop_dir / f"full_{k:02d}.png", width=None)
        c = crop_dir / f"crop_{k:02d}.png"
        media.run(["ffmpeg", "-y", "-v", "error", "-i", str(full), "-vf",
                   f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}", str(c)])
        crops.append(c)
    crop_px = max(1, int(info["width"] * w))
    scale = max(2, min(4, 1024 // crop_px))
    out = d / "zooms" / f"stack_{t:09.3f}_{x:.2f}_{y:.2f}_{w:.2f}_{h:.2f}.png"
    media.vsense("stack", "--out", str(out), "--scale", str(scale), *map(str, crops))
    return out


def frame_cmd(args) -> None:
    st = load_state(args.dir)
    d = Path(args.dir).expanduser()
    print(media.frame(Path(st["video"]), args.t, d / "frames" / f"full_{args.t:09.3f}.jpg", width=None))


def listen(args) -> None:
    """Everything known about the sound in a range, including a loudness strip a person could 'hear'."""
    st = load_state(args.dir)
    a, b = args.start, args.end
    for s_ in st["transcript"]:
        if s_["end"] >= a and s_["start"] <= b:
            flag = "" if s_.get("voice", True) else " (no voice heard: likely invented)"
            who = f" {s_['speaker']}" if s_.get("speaker") else ""
            print(f"speech{who} {report.ts(s_['start'], True)}–{report.ts(s_['end'], True)}{flag}: {s_['text']}")
            ear2 = [e["text"] for e in st.get("second_ear", []) if s_["start"] - 0.15 <= e["start"] < s_["end"] - 0.15]
            if ear2:
                print(f"  ear 2: {' '.join(ear2)}")
            words = [w for w in s_.get("words", []) if a - 0.5 <= w["t"] <= b + 0.5]
            if words:
                print("  words: " + " ".join(f"{w['w']}@{w['t']:.2f}" for w in words))
    for e in st["events"]:
        if e["kind"] in ("sound", "sound-type", "soundtrack", "loud") and e.get("end", e["t"]) >= a and e["t"] <= b:
            print(f"event  {e['id']} {report.ts(e['t'], True)} {e['kind']}: {e['detail']}")
    lv = [(t, db) for t, db in st.get("levels", []) if a <= t <= b]
    if lv:
        bars = " ▁▂▃▄▅▆▇█"
        strip = "".join(bars[max(0, min(8, int((db + 60) / 60 * 8)))] for _, db in lv[::2])
        print(f"loudness every 0.1 s ({report.ts(a, True)} → {report.ts(b, True)}, -60..0 dB):")
        print(f"  {strip}")
        print(f"  min {min(d for _, d in lv):.0f} dB, max {max(d for _, d in lv):.0f} dB")
    for w in st.get("sounds") or []:
        if a <= w["start"] <= b:
            print(f"class  {report.ts(w['start'], True)}: " + ", ".join(f"{l} {c:.2f}" for l, c in w["top"]))


def live_cmd(args) -> None:
    import live
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out).expanduser().resolve() if args.out else CACHE / f"live-{stamp}"
    live.live(args.source, out, args.chunk, args.seconds, args.budget)


def main() -> int:
    p = argparse.ArgumentParser(description="Watch a video like a person.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("source"); s.add_argument("--out")
    s.add_argument("--budget", type=int, default=8); s.add_argument("--lang", default="auto")
    s.add_argument("--no-speech", action="store_true")
    s.add_argument("--no-people", action="store_true", help="skip face, body and gesture tracking")
    l_ = sub.add_parser("look"); l_.add_argument("dir"); l_.add_argument("--start", type=float, required=True)
    l_.add_argument("--end", type=float, required=True); l_.add_argument("--n", type=int, default=12)
    l_.add_argument("--strobe", action="store_true")
    z = sub.add_parser("zoom"); z.add_argument("dir"); z.add_argument("--t", type=float, required=True)
    z.add_argument("--box")
    z.add_argument("--stack", type=int, default=0, metavar="N",
                   help="also align and average N neighbouring frames (small text, low-res video)")
    f = sub.add_parser("frame"); f.add_argument("dir"); f.add_argument("--t", type=float, required=True)
    v = sub.add_parser("live"); v.add_argument("source", help="screen, camera, a stream URL or a file")
    v.add_argument("--out"); v.add_argument("--chunk", type=int, default=10)
    v.add_argument("--for", dest="seconds", type=float, default=0, help="stop after this many seconds")
    v.add_argument("--budget", type=int, default=2)
    a = sub.add_parser("listen"); a.add_argument("dir"); a.add_argument("--start", type=float, required=True)
    a.add_argument("--end", type=float, required=True)
    args = p.parse_args()
    try:
        {"scan": scan, "look": look, "zoom": zoom, "frame": frame_cmd, "listen": listen, "live": live_cmd}[args.cmd](args)
    except MediaError as exc:
        print(f"watch-video: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
