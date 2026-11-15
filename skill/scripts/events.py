"""Merging every sense into one timeline, and choosing where attention goes."""

from __future__ import annotations

import re

import signals as S

WINDOW_MAX = 4.0     # seconds; attention is narrow, like a person replaying one moment
MUSIC_LABELS = {"music", "singing", "humming", "rapping", "choir", "whistling", "saxophone", "guitar",
                "acoustic_guitar", "electric_guitar", "bass_guitar", "piano", "electric_piano",
                "keyboard_musical", "synthesizer", "organ", "drum", "drum_kit", "percussion", "violin_fiddle",
                "cello", "trumpet", "flute", "glockenspiel", "marimba_xylophone", "harp", "ukulele",
                "orchestra", "bell", "tabla", "brass_instrument", "plucked_string_instrument", "wind_instrument"}


def is_words(text: str) -> bool:
    """whisper writes [MUSIC], (applause), ♪ or bare punctuation for non-speech."""
    stripped = re.sub(r"\[[^\]]*\]|\([^)]*\)|[♪♫*.,!?…\-\s]", "", text)
    return len(stripped) >= 2


def build_events(motion_ev: list[dict], cuts: list[float], onsets: list[dict],
                 voice: list[tuple[float, float]], sounds: list[dict],
                 camera: list[dict], loud: list[dict], steady: list[dict] | None = None,
                 body: list[dict] | None = None) -> list[dict]:
    events = list(motion_ev) + list(camera) + list(loud) + list(steady or []) + list(body or [])
    events += [{"kind": "cut", "t": c, "detail": "hard cut to a new shot", "score": 0.4} for c in cuts]
    for group in S.onset_clusters(onsets):
        first = group[0]
        if S.in_any(first["t"], voice, pad=0.4):
            continue                                   # syllables and someone starting to talk
        events.append({"kind": "sound", "t": first["t"], "end": round(group[-1]["t"] + group[-1]["length"], 2),
                       "detail": S.describe_onsets(group), "score": 0.75})
    for s in sounds:
        musical = s["label"] in MUSIC_LABELS
        if not musical and s["conf"] < S.SOUND_EVENT_CONF:
            continue
        events.append({"kind": "soundtrack" if musical else "sound-type", "t": s["start"], "end": s["end"],
                       "detail": ", ".join(s["labels"]), "score": 0.25 if musical else 0.55})
    events.sort(key=lambda e: e["t"])
    for i, e in enumerate(events, 1):
        e["id"] = f"E{i}"
    return events


def attention_windows(events: list[dict], duration: float, budget: int) -> list[dict]:
    """Pick moments to look at closely, the way a person watches start to finish.

    First pass: the best event in each of `budget` equal slices of the video (coverage).
    Second pass: fill unused slots with the best remaining events anywhere (importance).
    """
    pool = [e for e in events if e["kind"] not in ("soundtrack", "steady")
            and not (e["kind"] == "camera" and "shakes" in e["detail"])]
    slice_len = duration / max(1, budget)
    chosen: list[dict] = []
    for i in range(budget):
        here = [e for e in pool if i * slice_len <= e["t"] < (i + 1) * slice_len]
        if here:
            chosen.append(max(here, key=lambda e: e["score"]))
    for e in sorted(pool, key=lambda e: -e["score"]):
        if len(chosen) >= budget:
            break
        if e not in chosen:
            chosen.append(e)
    windows: list[dict] = []
    for e in sorted(chosen, key=lambda e: -e["score"]):
        if any(w["start"] <= e["t"] <= w["end"] for w in windows):
            continue
        start = max(0.0, e["t"] - 0.5)
        end = min(duration, max(e.get("end", e["t"]) + 0.7, start + 1.2), start + WINDOW_MAX)
        windows.append({"start": round(start, 2), "end": round(end, 2)})
    windows.sort(key=lambda w: w["start"])
    for i, w in enumerate(windows, 1):
        w["id"] = f"W{i}"
        inside = [e for e in events if w["start"] <= e["t"] <= w["end"]
                  and e["kind"] not in ("soundtrack", "steady")]
        for e in inside:
            e.setdefault("attend", w["id"])
        w["events"] = [e["id"] for e in inside]
        # a strobe only helps when something travels across the scene
        # (and only with a still camera: during a pan the background smears)
        w["has_motion"] = (any(e["kind"] == "motion" and "in place" not in e["detail"]
                               and not e["detail"].startswith("whole-frame") for e in inside)
                           and not any(e["kind"] == "camera" for e in inside))
    return windows


def promote_hidden_cuts(motion_ev: list[dict], cuts: list[float]) -> tuple[list[dict], list[float]]:
    """A one-frame change of the whole picture is a cut, even when busy footage around it made
    the colour-based cut detector hesitate."""
    keep, extra = [], []
    for e in motion_ev:
        if e["detail"].startswith("whole-frame") and e.get("instant") and not any(abs(e["t"] - c) < 0.3 for c in cuts):
            extra.append(round(e["end"], 2))
        else:
            keep.append(e)
    return keep, sorted(cuts + extra)


def shot_frames(shots: list[tuple[float, float]], motion: list) -> list[float]:
    """For each shot, the moment with the most going on (a mid-shot frame is often empty)."""
    picks = []
    for a, b in shots:
        inner = [m for m in motion if a + 0.3 <= m.t <= b - 0.3]
        best = max(inner, key=lambda m: m.frac, default=None)
        picks.append(round(best.t, 2) if best and best.frac > 0.001 else round((a + b) / 2, 2))
    return picks
