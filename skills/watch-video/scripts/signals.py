"""Peripheral vision: cheap signals over EVERY frame and every 50 ms of audio.

Nothing here looks at pictures in detail. It decides where attention should go, the way
a person notices movement or a bang without staring at the whole screen.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import subprocess

from media import MediaError, ensure_vsense, run

DIFF_THRESHOLD = 24        # grey-level change that counts as "this pixel changed"
CELL_ACTIVE = 0.1          # share of a grid cell's pixels that must change
FRAC_FLOOR = 0.002         # share of the whole frame that must change
SEGMENT_GAP = 0.25         # seconds; active runs closer than this merge
ONSET_JUMP_DB = 12.0       # loudness jump over the previous second
ONSET_FLOOR_DB = -38.0
SILENCE_DB = -45.0
CUT_JUMP = 0.08           # mean colour change for a hard cut
PEAK_ACTIVE = 0.08        # a grid cell this changed means something happened there (a digit changing)
CAMERA_MOVING = 0.12      # screen-widths per second before we call it a camera move
QUIET_DB = -50.0          # below this, sound-type labels are classifier noise
SOUND_CONF = 0.6
SOUND_EVENT_CONF = 0.75   # a non-music sound label must be this sure to become an event


def analysis_fps(duration: float, source_fps: float) -> float:
    """Scan densely for short videos, more sparsely for long ones (still far above 1 fps)."""
    cap = 15.0 if duration <= 600 else 8.0 if duration <= 3600 else 4.0
    return min(cap, source_fps or cap)


def grid_shape(width: int, height: int) -> tuple[int, int, int, int]:
    aw = 320
    ah = max(2, int(round(aw * height / max(1, width) / 2)) * 2)
    cols = 32
    rows = max(2, int(round(cols * height / max(1, width))))
    return aw, ah, cols, rows


@dataclass
class MotionFrame:
    t: float
    frac: float                      # share of frame changed since previous sample
    peak: float                      # most-changed grid cell share
    centroid: tuple[float, float] | None
    cells: list[int] = field(default_factory=list)   # indices of active cells
    cam: tuple[float, float] = (0.0, 0.0)            # camera velocity, screens per second


def motion_scan(video: Path, info: dict, fps: float) -> list[MotionFrame]:
    """Every sampled frame vs the one before, after undoing camera movement (vsense motion).

    Without the compensation a pan makes the whole frame "move" and hides what moves in the scene.
    """
    aw, ah, cols, rows = grid_shape(info["width"], info["height"])
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={aw}:{ah},format=gray",
         "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        res = subprocess.run(
            [str(ensure_vsense()), "motion", "--w", str(aw), "--h", str(ah), "--fps", str(fps),
             "--cols", str(cols), "--rows", str(rows), "--threshold", str(DIFF_THRESHOLD)],
            stdin=dec.stdout, capture_output=True, timeout=7200, check=False)
    finally:
        if dec.stdout:
            dec.stdout.close()
        dec.wait()
    if res.returncode != 0:
        raise MediaError(f"motion scan failed: {res.stderr.decode(errors='replace')[-300:]}")
    out: list[MotionFrame] = []
    for line in res.stdout.decode().splitlines():
        r = json.loads(line)
        out.append(MotionFrame(t=r["t"], frac=r["frac"], peak=r["peak"],
                               centroid=tuple(r["c"]) if "c" in r else None,
                               cells=r["cells"], cam=tuple(r["cam"])))
    return out


def camera_moves(frames: list[MotionFrame], cuts: list[float]) -> list[dict]:
    """Stretches where the camera pans, tilts or shakes."""
    runs: list[list[MotionFrame]] = []
    for f in frames:
        if math.hypot(*f.cam) < CAMERA_MOVING or any(abs(f.t - c) < 0.15 for c in cuts):
            continue
        if runs and f.t - runs[-1][-1].t <= 0.3:
            runs[-1].append(f)
        else:
            runs.append([f])
    moves = []
    for r in runs:
        if r[-1].t - r[0].t < 0.4:
            continue
        vx = statistics.mean(f.cam[0] for f in r)
        vy = statistics.mean(f.cam[1] for f in r)
        flips = sum(1 for a, b in zip(r, r[1:]) if a.cam[0] * b.cam[0] < 0 or a.cam[1] * b.cam[1] < 0)
        if flips >= max(3, len(r) // 3):
            what = "camera shakes"
        else:
            parts = []
            if abs(vx) >= CAMERA_MOVING * 0.6:
                parts.append(f"pans {'right' if vx > 0 else 'left'}")
            if abs(vy) >= CAMERA_MOVING * 0.6:
                parts.append(f"tilts {'down' if vy > 0 else 'up'}")
            what = "camera " + (" and ".join(parts) or "moves")
        moves.append({"kind": "camera", "t": round(r[0].t, 2), "end": round(r[-1].t, 2),
                      "detail": f"{what} ({math.hypot(vx, vy):.2f} screen/s)", "score": 0.3})
    return moves


def scene_cuts(video: Path, fps: float) -> list[float]:
    """Hard cuts from colour thumbnails: a big one-frame jump that its neighbours don't share.

    Colour, not brightness, because a cut between two scenes of similar brightness
    (green room -> blue room) is obvious to a person but nearly invisible in greyscale.
    """
    raw = run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale=16:9,format=rgb24",
               "-f", "rawvideo", "-"], binary=True, timeout=7200)
    size = 16 * 9 * 3
    thumbs = [raw[i * size:(i + 1) * size] for i in range(len(raw) // size)]
    diffs = [0.0] + [sum(abs(a - b) for a, b in zip(thumbs[k - 1], thumbs[k])) / (255 * size)
                     for k in range(1, len(thumbs))]
    cuts = []
    for k in range(1, len(diffs)):
        before = diffs[max(1, k - 5):k] or [0.0]
        after = diffs[k + 1:k + 6] or [0.0]
        local = max(statistics.median(before), statistics.median(after), 0.01)
        if diffs[k] >= CUT_JUMP and diffs[k] >= 3 * local:
            cuts.append(round(k / fps, 2))
    return cuts


def loudness(wav: Path, work: Path) -> list[tuple[float, float]]:
    """RMS level in dB for every 50 ms of audio."""
    log = work / "rms.txt"
    if not log.exists():
        run(["ffmpeg", "-v", "error", "-i", str(wav.resolve()), "-af",
             "asetnsamples=n=800:p=0,astats=metadata=1:reset=1,"
             "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=rms.txt",
             "-f", "null", "-"], timeout=3600, cwd=work)
        if not log.exists():
            log.write_text("")
    out, t = [], None
    for line in log.read_text().splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([\d.]+)", line)
            t = float(m.group(1)) if m else None
        elif "RMS_level=" in line and t is not None:
            val = line.split("=", 1)[1].strip()
            db = -100.0 if "inf" in val else max(-100.0, float(val))
            out.append((round(t, 3), db))
    return out


def audio_onsets(levels: list[tuple[float, float]]) -> list[dict]:
    """Sudden sounds, each with how long it rang (short = knock, clap, click; long = alarm, roar)."""
    onsets, last = [], -10.0
    for i, (t, db) in enumerate(levels):
        prev = [d for _, d in levels[max(0, i - 20):i]]
        if len(prev) < 5:
            continue
        base = statistics.median(prev)
        if db > ONSET_FLOOR_DB and db - base >= ONSET_JUMP_DB and t - last > 0.18:
            j = i
            while j + 1 < len(levels) and levels[j + 1][1] > base + 6 and levels[j + 1][0] - t < 5:
                j += 1
            onsets.append({"t": t, "db": round(db, 1), "jump": round(db - base, 1),
                           "length": round(levels[j][0] - t + 0.05, 2)})
            last = t
    return onsets


def onset_clusters(onsets: list[dict], gap: float = 0.8) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for o in onsets:
        if groups and o["t"] - groups[-1][-1]["t"] <= gap:
            groups[-1].append(o)
        else:
            groups.append([o])
    return groups


def describe_onsets(group: list[dict]) -> str:
    loudest = max(group, key=lambda o: o["db"])
    sharp = all(o["length"] <= 0.25 for o in group)
    kind = "sharp" if sharp else "sustained"
    hint = " (knock, clap, tap, footstep, hit?)" if sharp else ""
    if len(group) == 1:
        o = group[0]
        return f"{kind} sound lasting {o['length']:.2f}s, {o['jump']:.0f} dB louder than just before{hint}"
    gaps = ", ".join(f"{b['t'] - a['t']:.2f}s" for a, b in zip(group, group[1:]))
    return (f"{len(group)} {kind} sounds in a row (gaps {gaps}), up to {loudest['jump']:.0f} dB "
            f"louder than just before{hint}")


def loudness_peaks(levels: list[tuple[float, float]], top: int = 4) -> list[dict]:
    """The loudest stretches (crowd roar, shouting, music swell), measured over 1 s."""
    if len(levels) < 40:
        return []
    dbs = [d for _, d in levels]
    smooth = [statistics.mean(dbs[max(0, i - 10):i + 10]) for i in range(len(dbs))]
    typical = statistics.median(smooth)
    if typical < -50:
        return []          # a mostly silent video: every sound is "loud", so this adds nothing
    peaks = []
    for i in range(1, len(smooth) - 1):
        if smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1] and smooth[i] >= typical + 5 \
                and smooth[i] > -30:
            peaks.append((smooth[i], levels[i][0]))
    chosen: list[tuple[float, float]] = []
    for v, t in sorted(peaks, reverse=True):
        if all(abs(t - ct) > 3 for _, ct in chosen):
            chosen.append((v, t))
        if len(chosen) >= top:
            break
    return [{"kind": "loud", "t": round(max(0.0, t - 0.5), 2), "end": round(t + 0.5, 2),
             "detail": f"loudest stretch: {v - typical:.0f} dB above this video's typical level", "score": 0.4}
            for v, t in sorted(chosen, key=lambda p: p[1])]


def silences(levels: list[tuple[float, float]], min_len: float = 1.5) -> list[tuple[float, float]]:
    out, start = [], None
    for t, db in levels:
        if db < SILENCE_DB and start is None:
            start = t
        elif db >= SILENCE_DB and start is not None:
            if t - start >= min_len:
                out.append((start, t))
            start = None
    if start is not None and levels and levels[-1][0] - start >= min_len:
        out.append((start, levels[-1][0]))
    return out


def voice_spans(windows: list[dict], levels: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Where a voice is actually audible: Apple's speech class, tightened to loud-enough audio.

    Used to check whisper, which can invent fluent sentences over music or silence.
    """
    raw: list[list[float]] = []
    for w in windows:
        if any(label == "speech" and conf >= 0.5 for label, conf in w["top"]):
            if raw and w["start"] - raw[-1][1] <= 0.5:
                raw[-1][1] = w["end"]
            else:
                raw.append([w["start"], w["end"]])
    spans = []
    for a, b in raw:
        loud = [t for t, db in levels if a - 0.5 <= t <= b + 0.5 and db > -40]
        spans.append((round(min(loud), 2), round(max(loud) + 0.05, 2)) if loud else (a, b))
    return spans


def check_transcript(segments: list[dict], spans: list[tuple[float, float]]) -> list[dict]:
    """Snap whisper segments to audible voice and flag text with no voice under it."""
    checked = []
    for s in segments:
        hits = [(a, b) for a, b in spans if b > s["start"] and a < s["end"]]
        if hits:
            checked.append({**s, "start": max(s["start"], min(a for a, _ in hits)),
                            "end": min(s["end"], max(b for _, b in hits)), "voice": True})
        else:
            checked.append({**s, "voice": False})
    return checked


def sound_segments(windows: list[dict], levels: list[tuple[float, float]] | None = None,
                   voice: list[tuple[float, float]] | None = None) -> list[dict]:
    """Merge 1 s sound-class windows into stretches (speech itself is left to whisper).

    Music flips between labels (music, saxophone, singing, ...) every second; a person hears one
    stretch of music, so adjacent windows merge and keep their most common labels. Labels over
    near-silence, and weak labels under a voice (a voice often reads as "insect"), are dropped.
    """
    levels, voice = levels or [], voice or []
    segs: list[dict] = []
    for w in windows:
        lv = [d for t, d in levels if w["start"] <= t <= w["end"]]
        if lv and max(lv) < QUIET_DB:
            continue
        in_voice = any(a < w["end"] and b > w["start"] for a, b in voice)
        labels = [(l, c) for l, c in w["top"]
                  if l != "speech" and c >= SOUND_CONF and not (in_voice and c < 0.85)]
        if not labels:
            continue
        if segs and w["start"] - segs[-1]["end"] <= 0.5:
            seg = segs[-1]
            seg["end"] = w["end"]
        else:
            seg = {"start": w["start"], "end": w["end"], "counts": {}, "best": {}}
            segs.append(seg)
        for l, c in labels:
            seg["counts"][l] = seg["counts"].get(l, 0) + 1
            seg["best"][l] = max(seg["best"].get(l, 0.0), c)
    out = []
    for seg in segs:
        ranked = sorted(seg["counts"], key=lambda l: (-seg["counts"][l], -seg["best"][l]))[:4]
        out.append({"start": seg["start"], "end": seg["end"], "labels": ranked,
                    "label": ranked[0], "conf": round(seg["best"][ranked[0]], 2)})
    return out


# ------------------------------------------------------------------ visual events


def _active(f: MotionFrame, noise: float, peak_noise: float) -> bool:
    return f.frac >= max(FRAC_FLOOR, noise * 4) or f.peak >= max(PEAK_ACTIVE, peak_noise * 3)


def _low(values: list[float], q: float = 0.3) -> float:
    vals = sorted(values)
    return vals[int(q * (len(vals) - 1))] if vals else 0.0


def _local_baselines(frames: list[MotionFrame], fps: float, span: float = 4.0) -> list[tuple[float, float]]:
    """Background motion around each moment (30th percentile over +-4 s).

    A steady drift lasting the whole window sets the baseline and stops counting as an event,
    while a 2 s movement in that window stays above it.
    """
    k = max(3, int(span * fps))
    fr = [f.frac for f in frames]
    pk = [f.peak for f in frames]
    return [(_low(fr[max(0, i - k):i + k + 1]), _low(pk[max(0, i - k):i + k + 1])) for i in range(len(frames))]


def steady_motion(frames: list[MotionFrame], fps: float, min_len: float = 3.0) -> list[dict]:
    """Ongoing movement (a slow drift, someone walking, a fan) that is background, not an event."""
    runs: list[list[MotionFrame]] = []
    for f in frames:
        if f.peak < 0.05:
            continue
        if runs and f.t - runs[-1][-1].t <= 0.5:
            runs[-1].append(f)
        else:
            runs.append([f])
    out = []
    for r in runs:
        if r[-1].t - r[0].t < min_len:
            continue
        cents = [f.centroid for f in r if f.centroid]
        cx = statistics.median(c[0] for c in cents) if cents else 0.5
        cy = statistics.median(c[1] for c in cents) if cents else 0.5
        out.append({"kind": "steady", "t": round(r[0].t, 2), "end": round(r[-1].t, 2),
                    "detail": f"ongoing movement around {_where(cx, cy)} for {r[-1].t - r[0].t:.0f}s",
                    "score": 0.2})
    return out


def _where(x: float, y: float) -> str:
    h = "left" if x < 0.33 else "right" if x > 0.67 else "centre"
    v = "top" if y < 0.33 else "bottom" if y > 0.67 else "middle"
    return "centre" if (h, v) == ("centre", "middle") else f"{v}-{h}"


def _direction(path: list[tuple[float, float]]) -> str:
    """Describe a centroid path, including there-and-back movements that end where they began."""
    if len(path) < 2:
        return "in place"
    x0, y0 = path[0]
    far = max(path, key=lambda p: math.hypot(p[0] - x0, p[1] - y0))
    reach = math.hypot(far[0] - x0, far[1] - y0)
    net = math.hypot(path[-1][0] - x0, path[-1][1] - y0)

    def leg(dx: float, dy: float) -> str:
        parts = []
        if abs(dx) >= 0.06:
            parts.append("right" if dx > 0 else "left")
        if abs(dy) >= 0.06:
            parts.append("down" if dy > 0 else "up")
        return "-".join(parts) or "slightly"

    if reach < 0.08:
        return "in place"
    if net < reach * 0.5:
        return f"moves {leg(far[0] - x0, far[1] - y0)} then back ({reach:.2f} screen out)"
    return f"moves {leg(path[-1][0] - x0, path[-1][1] - y0)}"


def _split_long_runs(runs: list[list[MotionFrame]], long: float = 3.0) -> list[list[MotionFrame]]:
    """Inside a long stretch of movement, keep only the moments that stand out from it.

    The stretch itself is reported by steady_motion; a ball flying past a slowly drifting
    object must not be swallowed by the drift.
    """
    out: list[list[MotionFrame]] = []
    for r in runs:
        if r[-1].t - r[0].t <= long:
            out.append(r)
            continue
        mf = statistics.median(f.frac for f in r)
        mp = statistics.median(f.peak for f in r)
        sub: list[list[MotionFrame]] = []
        for f in r:
            if f.frac >= max(FRAC_FLOOR, 4 * mf) or f.peak >= max(PEAK_ACTIVE, 2.5 * mp):
                if sub and f.t - sub[-1][-1].t <= SEGMENT_GAP:
                    sub[-1].append(f)
                else:
                    sub.append([f])
        out.extend(sub)
    return out


def motion_events(frames: list[MotionFrame], cuts: list[float], fps: float,
                  cols: int, rows: int) -> list[dict]:
    if not frames:
        return []
    base = _local_baselines(frames, fps)
    runs: list[list[MotionFrame]] = []
    for f, (noise, peak_noise) in zip(frames, base):
        if not _active(f, noise, peak_noise):
            continue
        if runs and f.t - runs[-1][-1].t <= SEGMENT_GAP:
            runs[-1].append(f)
        else:
            runs.append([f])
    events = []
    step = 1 / fps
    for run_ in _split_long_runs(runs):
        start, end = run_[0].t - step, run_[-1].t
        near_cut = any(abs(c - run_[0].t) <= 1.5 * step for c in cuts)
        if near_cut and len(run_) <= 2:
            continue                                   # the cut itself, not motion
        cells = sorted({c for f in run_ for c in f.cells})
        if cells:
            xs = [c % cols for c in cells]
            ys = [c // cols for c in cells]
            box = [min(xs) / cols, min(ys) / rows, (max(xs) + 1 - min(xs)) / cols, (max(ys) + 1 - min(ys)) / rows]
        else:
            box = [0.0, 0.0, 1.0, 1.0]
        path = [f.centroid for f in run_ if f.centroid]
        dur = max(end - start, step)
        area = box[2] * box[3]
        peak = max(f.frac for f in run_)
        dist = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))
        speed = dist / dur if len(path) > 1 else 0.0          # screen-widths per second, along the path
        scale = "whole-frame" if peak > 0.35 else "large" if area > 0.25 else "small"
        where = _where(box[0] + box[2] / 2, box[1] + box[3] / 2)
        direction = _direction(path)
        desc = f"{scale} change, {where}, {direction}"
        if speed > 0.8 and direction != "in place":
            desc += f", FAST ({speed:.1f} screen/s)"
        if len(run_) <= 2:
            desc += ", instant (one-frame change)"
        elif dur <= 0.6:
            desc += f", over {dur:.2f}s"
        score = 0.45 + min(0.35, speed / 3) + (0.2 if dur <= 0.6 else 0) + min(0.1, peak)
        events.append({
            "kind": "motion", "t": round(start, 2), "end": round(end, 2), "detail": desc,
            "box": [round(v, 3) for v in box], "peak": round(peak, 4), "speed": round(speed, 2),
            "instant": len(run_) <= 2 and direction == "in place", "score": round(score, 3),
        })
    return events


def _overlap(a: list[float], b: list[float]) -> bool:
    return not (a[0] + a[2] < b[0] or b[0] + b[2] < a[0] or a[1] + a[3] < b[1] or b[1] + b[3] < a[1])


def flash_candidates(events: list[dict], max_gap: float = 3.0) -> list[tuple[int, int]]:
    """Pairs of instant changes in the same spot: maybe something appeared, stayed, then vanished.

    The caller confirms by checking that the spot looks the same after the second change as
    before the first (otherwise it is a change of state, like a score going from 7 to 8).
    """
    pairs, used = [], set()
    for i, e in enumerate(events):
        if i in used or not e.get("instant"):
            continue
        for j in range(i + 1, len(events)):
            f = events[j]
            if f["t"] - e["end"] > max_gap:
                break
            if j not in used and f.get("instant") and _overlap(e["box"], f["box"]):
                pairs.append((i, j))
                used.update((i, j))
                break
    return pairs


def in_any(t: float, spans: list[tuple[float, float]], pad: float = 0.0) -> bool:
    return any(a - pad <= t <= b + pad for a, b in spans)
