"""Turns signals and events into the things Claude reads: chart spec, timeline, WATCH.md."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import math


def ts(t: float, precise: bool = False) -> str:
    t = max(0.0, t)
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    sec = f"{s:05.2f}" if precise else f"{int(s):02d}"
    return f"{h}:{m:02d}:{sec}" if h else f"{m:02d}:{sec}"


def chart_spec(info: dict, motion: list, levels: list, speech: list, cuts: list,
               events: list, width: int = 1536) -> dict:
    dur = info["duration"]
    bins = min(1200, max(100, int(dur * 4)))

    def binned(points: list[tuple[float, float]], reduce=max) -> list[list[float]]:
        buckets: list[list[float]] = [[] for _ in range(bins)]
        for t, v in points:
            buckets[min(bins - 1, int(t / dur * bins))].append(v)
        return [[round((i + 0.5) * dur / bins, 3), round(reduce(b), 4) if b else 0.0]
                for i, b in enumerate(buckets)]

    peak = max([m.peak for m in motion] + [1e-6])
    motion_pts = binned([(m.t, math.sqrt(m.peak / peak)) for m in motion])
    loud_pts = binned([(t, (db + 60) / 60) for t, db in levels])
    speech_pts = binned([(t, 1.0) for s in speech for t in _span(s["start"], s["end"], dur / bins)])
    series = [{"name": "motion", "color": "#4FC3F7", "points": motion_pts}]
    cam = [(m.t, min(1.0, math.hypot(*m.cam) / 1.0)) for m in motion if getattr(m, "cam", None)]
    if any(v > 0.1 for _, v in cam):
        series.append({"name": "camera", "color": "#BA68C8", "points": binned(cam)})
    if levels:
        series.append({"name": "loudness", "color": "#FFB74D", "points": loud_pts})
    if speech:
        series.append({"name": "voice", "color": "#81C784", "points": speech_pts})
    markers = [{"t": c, "color": "#FFF176"} for c in cuts]
    markers += [{"t": e["t"], "label": e["id"], "color": "#F06292"} for e in events if e.get("attend")]
    return {"duration": dur, "width": width, "series": series, "markers": markers,
            "title": "Whole video at a glance: motion in scene, camera, loudness, voice | yellow = cut, pink = looked at"}


def _span(a: float, b: float, step: float):
    t = a
    while t <= b:
        yield t
        t += max(step, 0.05)


def people_md(out: dict) -> list[str]:
    voices, faces, bodies = out.get("voices") or {}, out.get("faces") or [], out.get("bodies") or []
    if not (voices or faces or bodies):
        return []
    L = ["", "## People"]
    for name, v in voices.items():
        seen = sorted({t["face"] for t in out.get("turns", []) if t["speaker"].rstrip("?") == name and t.get("face")})
        on = f"; on screen as {', '.join(seen)}" if seen else ""
        L.append(f"- Voice **{name}**: {v['describe']}, talks {v['talks_for']}s{on}")
    for f in faces:
        L.append(f"- Face **{f['id']}**: {f['where']} of frame, {ts(f['start'])}–{ts(f['end'])}")
    for b in bodies:
        L.append(f"- Body **{b['id']}**: {b['where']} of frame, {ts(b['start'])}–{ts(b['end'])}")
    if faces and out.get("cuts"):
        L.append("- Face and body IDs restart after every cut, so one person can have several. Faces that talk "
                 "with the same voice are probably the same person.")
    L.append("- Voices are described, never identified: no names, gender or age from sound alone.")
    return L


def watch_md(info: dict, out: dict) -> str:
    """The index Claude reads first. Short on purpose: it points at images to look at."""
    L: list[str] = []
    title = info.get("title") or info["path"].rsplit("/", 1)[-1]
    L.append(f"# Watching: {title}")
    L.append("")
    d = out["dir"].rstrip("/") + "/"
    rel = lambda p: p[len(d):] if p and p.startswith(d) else p  # noqa: E731
    L.append(f"Files are relative to `{d}`")
    L.append("")
    L.append(f"- Duration {ts(info['duration'])} · {info['width']}x{info['height']} · {info['fps']} fps · "
             f"audio: {'yes' if info['has_audio'] else 'none'}")
    cov = out["coverage"]
    L.append(f"- Scanned every frame at {cov['motion_fps']} fps for motion and cuts, audio every 50 ms, "
             f"speech: {cov['speech']}, sound types: {cov['sounds']}")
    L.append(f"- {len(out['shots'])} shot(s), {len(out['cuts'])} cut(s), {len(out['events'])} event(s), "
             f"{len(out['windows'])} attention window(s)")
    L.append("")
    L.append("## Look in this order")
    L.append(f"1. `{rel(out['chart'])}`: the whole video as signals (where things happen)")
    L.append(f"2. `{rel(out['storyboard'])}`: {out['storyboard_n']} evenly spaced frames (what it looks like)")
    if out.get("shots_sheet"):
        L.append(f"3. `{rel(out['shots_sheet'])}`: one frame per shot")
    if out.get("face_sheet"):
        L.append(f"- `{rel(out['face_sheet'])}`: each tracked face up close over time (read expressions here)")
    for w in out["windows"]:
        extra = f" + strobe `{rel(w['strobe'])}` (faint = earlier, solid = later)" if w.get("strobe") else ""
        L.append(f"- **{w['id']}** {ts(w['start'], True)}–{ts(w['end'], True)} ({', '.join(w['events'])}): "
                 f"`{rel(w['sheet'])}`{extra}")
    L.append("")
    L.append("## Timeline")
    for e in out["events"]:
        span = f"{ts(e['t'], True)}" + (f"–{ts(e['end'], True)}" if e.get("end") and e["end"] - e["t"] > 0.15 else "")
        L.append(f"- `{e['id']}` {span} **{e['kind']}**: {e['detail']}")
    if not out["events"]:
        L.append("- nothing stood out: steady picture and sound throughout")
    L += people_md(out)
    voices = out.get("voices") or {}
    if out["transcript"] and len(voices) > 1 and out.get("turns"):
        L.append("")
        lang = out.get("language")
        L.append(f"## Speech by turn{f' (language: {lang})' if lang else ''}")
        L.append("Speakers are told apart by voice (and by moving lips when faces are tracked). "
                 "`?` = too short to judge, guessed from the nearest turn. Word times: `listen`.")
        if out.get("second_ear"):
            L.append("Kenyan speech: reconstruct each turn from it and its `ear 2` line (two models, "
                     "different mistakes), as a Sheng speaker would write it; say which words you're unsure of.")
        for t in out["turns"]:
            face = f" [{t['face']}]" if t.get("face") else ""
            over = f" (talking over {', '.join(t['overlap'])})" if t.get("overlap") else ""
            L.append(f"- {ts(t['start'], True)}–{ts(t['end'], True)} **{t['speaker']}**{face}{over}: {t['text']}")
            ear2 = [e["text"] for e in out.get("second_ear", []) if t["start"] - 0.3 <= e["start"] < t["end"]]
            if ear2:
                L.append(f"  - ear 2: {' '.join(ear2)}")
    elif out["transcript"]:
        L.append("")
        lang = out.get("language")
        L.append(f"## Speech{f' (language: {lang})' if lang else ''}")
        if out.get("second_ear"):
            L.append("Kenyan speech (Sheng/Swahili/English). Two models heard it and err in different places: "
                     "reconstruct what was said from both lines, as a Sheng speaker would write it, and "
                     "say which words you're unsure of. Don't quote either line raw as the speech.")
        for s in out["transcript"]:
            flag = ""
            if s.get("voice") is False:
                flag = " (NO VOICE HEARD HERE: probably invented by the speech model, ignore unless confirmed)"
            elif s.get("conf") is not None and s["conf"] < 0.5:
                flag = " (unsure)"
            L.append(f"- {ts(s['start'], True)}–{ts(s['end'], True)}{flag}: {s['text']}")
            ear2 = [e["text"] for e in out.get("second_ear", []) if s["start"] - 0.15 <= e["start"] < s["end"] - 0.15]
            if ear2:
                L.append(f"  - ear 2: {' '.join(ear2)}")
    if out.get("persistent_text"):
        L.append("")
        L.append("## Text on screen the whole time (titles, handles, watermarks)")
        L.append("- " + " | ".join(out["persistent_text"]))
    if out["text_on_screen"]:
        L.append("")
        L.append("## Text that comes and goes (OCR, first time seen)")
        for item in out["text_on_screen"]:
            L.append(f"- {ts(item['t'], True)}: {' | '.join(item['lines'])}")
    labelled = [s for s in out["scenes"] if s["labels"] or s["people"]]
    if labelled:
        L.append("")
        L.append("## Scene labels (Apple Vision, rough)")
        for s in labelled:
            who = f", people {s['people']}" if s["people"] else ""
            L.append(f"- {ts(s['t'])}: {', '.join(s['labels']) or '(none)'}{who}")
    L.append("")
    L.append("## Not covered automatically")
    L.append("- Frames between storyboard/attention images were only seen as signals. Use `look` for any range,")
    L.append("  `zoom` for detail, `listen` for audio in a range. Say what you did not look at.")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- the light path

BRIEF_EVENTS = 12          # most important moments listed in the brief
BRIEF_SPEECH_CHARS = 6000  # full transcript up to here; longer ones are clipped per turn
PEOPLE_KINDS = ("body", "face", "crosstalk")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def brief_md(info: dict, out: dict) -> str:
    """What most questions need, in about a page: the story in signals, what was said, what was
    on screen, who was there. Images are listed with what each one answers, and opened only
    when the question needs eyes. Everything else stays in WATCH.md."""
    d = out["dir"].rstrip("/") + "/"
    rel = lambda p: p[len(d):] if p and p.startswith(d) else p  # noqa: E731
    ev = [e for e in out["events"] if e["kind"] not in ("steady", "soundtrack")]
    L = [f"# {info.get('title') or info['path'].rsplit('/', 1)[-1]}",
         f"{ts(info['duration'])} · {info['width']}x{info['height']} · {len(out['shots'])} shot(s) · "
         f"{len(ev)} event(s) · files in `{d}`", ""]
    music = [e for e in out["events"] if e["kind"] == "soundtrack"]
    if music:
        a, b = music[0]["t"], max(e.get("end", e["t"]) for e in music)
        L.append("Music throughout." if b - a > 0.85 * info["duration"] else
                 f"Music: {ts(a)}–{ts(b)} ({music[0]['detail']})")
    visual = [e for e in ev if e["kind"] in ("motion", "cut", "camera")]

    def rank(e: dict) -> tuple:
        # people, then sounds and changes that carry text or a flash, then the rest; whole-frame
        # jumps and camera moves say little about content, so they go last
        dull = e["kind"] == "camera" or e["detail"].startswith("whole-frame") or (e["kind"] == "cut" and len(out["cuts"]) > 6)
        telling = "text now" in e["detail"] or "FLASH" in e["detail"] or e["kind"] in ("sound", "loud", "sound-type")
        return (e["kind"] not in PEOPLE_KINDS, dull, not telling, -e.get("score", 0))

    def describe(e: dict) -> str:
        d = re.sub(r"colour now ([a-z ]+) \(was ([a-z ]+)\)", r"it is \1, against \2", e["detail"])
        d = d.replace("; text now: No change", "")
        if "FLASH" in d:                                    # keep the part that says how long it stayed
            head, flash = d.split("FLASH", 1)
            d = _clip(head, 110) + " FLASH" + flash
        else:
            d = _clip(d, 150)
        if e["kind"] in ("sound", "loud", "sound-type") and not any(abs(v["t"] - e["t"]) <= 0.6 for v in visual):
            d += " (nothing visible changed)"
        return d

    top = sorted(ev, key=rank)[:BRIEF_EVENTS]
    if top:
        L += ["", f"## Key moments ({len(top)} of {len(ev)})"]
        for e in sorted(top, key=lambda e: e["t"]):
            see = f" → `{rel(next((w['sheet'] for w in out['windows'] if w['id'] == e.get('attend')), ''))}`" \
                if e.get("attend") else ""
            L.append(f"- {ts(e['t'], True)} {e['kind']}: {describe(e)}{see}")
    kinds: dict[str, int] = {}
    for e in ev:
        if e not in top:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    if kinds:
        L.append("- not listed: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda p: -p[1])))
    voices, turns = out.get("voices") or {}, out.get("turns") or []
    said = turns if len(voices) > 1 and turns else [s for s in out["transcript"] if s.get("voice", True)]
    if said:
        total = sum(len(s["text"]) for s in said)
        per = None if total <= BRIEF_SPEECH_CHARS else max(60, BRIEF_SPEECH_CHARS // len(said))
        L += ["", f"## Said{' (clipped; full text in WATCH.md)' if per else ''}"]
        L.append("Names and web addresses are spelled as heard; check them against the on-screen text.")
        if out.get("second_ear"):
            L.append("Kenyan speech: lines are rough. For exact words, read WATCH.md (it has a second model's "
                     "hearing under each line) and reconstruct from both.")
        merged: list[dict] = []
        for s in said:                                      # join phrases into paragraphs per speaker
            who = s.get("speaker", "").rstrip("?")
            short = len(s["text"].split()) <= 3              # a stray fragment: keep it with its neighbour
            same = merged and (merged[-1]["who"] == who or short)
            if same and s["start"] - merged[-1]["end"] < 2.5 and s["end"] - merged[-1]["start"] < 25:
                merged[-1]["text"] += " " + s["text"]
                merged[-1]["end"] = s["end"]
            else:
                merged.append({"who": who, "start": s["start"], "end": s["end"], "text": s["text"],
                               "face": s.get("face"), "over": s.get("overlap")})
        for m in merged:
            who = f" {m['who']}" if len(voices) > 1 and m["who"] else ""
            L.append(f"- {ts(m['start'])}{who}: {_clip(m['text'], per * 3) if per else m['text']}")
    for name, v in voices.items() if len(voices) > 1 else []:
        L.append(f"- voice {name}: {v['describe']}, {v['talks_for']}s")
    text = [" | ".join(x["lines"]) for x in out.get("text_on_screen") or []]
    if out.get("persistent_text") or text:
        L += ["", "## On screen"]
        if out.get("persistent_text"):
            L.append("- always: " + _clip(" | ".join(out["persistent_text"]), 200))
        seen: list[str] = []
        for x in out.get("text_on_screen") or []:
            line = " | ".join(x["lines"])
            if any(SequenceMatcher(None, line.lower(), s).ratio() > 0.55 for s in seen):
                continue                                    # the same caption, read slightly differently
            seen.append(line.lower())
            if len(seen) <= 10:
                L.append(f"- {ts(x['t'])}: {_clip(line, 120)}")
        if len(seen) > 10:
            L.append(f"- …{len(seen) - 10} more in WATCH.md")
    L += ["", "## Look only if the question needs eyes",
          f"- `{rel(out.get('glance') or out['storyboard'])}`: what it looks like, one small image",
          "- a key moment's sheet (the `→` path above) for what happened there"]
    if out.get("face_sheet"):
        L.append(f"- `{rel(out['face_sheet'])}`: faces up close, for expressions")
    L += ["- `WATCH.md`: every event, word times, all images; `watch.py look|zoom|listen` for anything else",
          "", "Seen as signals only unless you open an image. Say which you used."]
    return "\n".join(L) + "\n"
