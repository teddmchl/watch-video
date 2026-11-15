"""Attention: turning moments into things to look at, and reading what is on screen."""

from __future__ import annotations

import colorsys
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import media
import report
from media import MediaError

SHEET_COLS, SHEET_TILE = 4, 384
STROBE_WIDTH = 960
OCR_CONF_FAST = 0.5
OCR_CONF = 0.6          # Vision reports 0.5 for guesses (half-hidden watermarks, stylised logos)
PERSISTENT_SHARE = 0.7  # text on screen in this share of samples counts as "always on"
MAX_COLOUR_PROBES = 60


def evenly(start: float, end: float, n: int) -> list[float]:
    if n <= 1 or end <= start:
        return [round(start, 3)]
    step = (end - start) / (n - 1)
    return [round(start + i * step, 3) for i in range(n)]


def sheet(video: Path, times: list[float], out: Path, title: str) -> Path:
    imgs = media.frames(video, times, out.parent / "frames")
    media.vsense("sheet", "--out", str(out), "--cols", str(SHEET_COLS), "--tile", str(SHEET_TILE),
                 "--title", title, *[f"{report.ts(t, True)}={p}" for t, p in zip(times, imgs)])
    return out


def strobe(video: Path, start: float, end: float, out: Path, cuts: list[float] | None = None) -> Path:
    """One image of a whole movement. Clipped to a single shot: across a cut it would be nonsense."""
    mid = (start + end) / 2
    for c in cuts or []:
        if start < c <= mid:
            start = c + 0.05
        elif mid < c < end:
            end = c - 0.05
    imgs = media.frames(video, evenly(start, end, 16), out.parent / "frames", width=STROBE_WIDTH)
    media.vsense("strobe", "--out", str(out), *map(str, imgs))
    return out


# ---------------------------------------------------------------- colour


def colour_name(rgb: list[float]) -> str:
    r, g, b = (max(0.0, min(255.0, v)) / 255 for v in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.18 or v < 0.12:
        if v < 0.12:
            return "black"
        return "white" if v > 0.9 else "light grey" if v > 0.65 else "grey" if v > 0.35 else "dark grey"
    hue = h * 360
    for limit, name in ((15, "red"), (40, "orange"), (70, "yellow"), (165, "green"), (195, "cyan"),
                        (255, "blue"), (290, "purple"), (345, "pink"), (361, "red")):
        if hue < limit:
            return f"dark {name}" if v < 0.4 else name
    return "red"


def change_colour(before: Path, after: Path, box: list[float]) -> dict | None:
    pad = [max(0.0, box[0] - 0.02), max(0.0, box[1] - 0.02), min(1.0, box[2] + 0.04), min(1.0, box[3] + 0.04)]
    try:
        return media.vsense_json("color", "--before", str(before), "--after", str(after),
                                 "--box", ",".join(f"{v:.4f}" for v in pad))
    except MediaError:
        return None


# ---------------------------------------------------------------- text


def ocr_lines(paths: list[Path]) -> list[list[str]]:
    """OCR is a bonus sense: if Vision fails, carry on without it rather than lose the scan."""
    if not paths:
        return []
    res: list = []
    for attempt in range(3):
        try:
            res = media.vsense_json("ocr", *map(str, paths)) or []
        except MediaError as exc:
            print(f"warning: OCR unavailable: {exc}", file=sys.stderr)
            return [[] for _ in paths]
        # Apple's text reader sometimes returns nothing at all while its model is still loading
        # (seen right after the helper is first built). Empty for every image: wait and ask again.
        if any(r["lines"] for r in res) or len(paths) < 3:
            break
        time.sleep(1.5)
    # the fast model (used when the accurate one won't load) scores everything 0.3, 0.5 or 1.0
    return [[ln["text"] for ln in r["lines"]
             if ln.get("conf", 1) >= (OCR_CONF_FAST if r.get("level") == "fast" else OCR_CONF)] for r in res]


def norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def similar(a: str, b: str, ratio: float = 0.8) -> bool:
    """OCR jitters (ZOZO vs ZAZO), so compare fuzzily, but numbers must match exactly:
    "Score 7" and "Score 8" are different things to a person."""
    na, nb = norm_text(a), norm_text(b)
    if re.sub(r"\D", "", na) != re.sub(r"\D", "", nb):
        return False
    return SequenceMatcher(None, na, nb).ratio() >= ratio


def similar_in(line: str, lines: list[str]) -> bool:
    return not norm_text(line) or any(similar(line, o) for o in lines)


def is_persistent(line: str, persistent: list[str]) -> bool:
    """Part of an always-on line (a watermark half covered by the picture still counts)."""
    n = norm_text(line)
    if len(n) < 3:
        return True
    for p in persistent:
        pn = norm_text(p)
        if re.sub(r"\D", "", n) and re.sub(r"\D", "", n) not in re.sub(r"\D", "", pn):
            continue
        if n in pn or SequenceMatcher(None, n, pn).ratio() >= 0.7:
            return True
        m = SequenceMatcher(None, n, pn).find_longest_match(0, len(n), 0, len(pn))
        if m.size >= max(3, 0.6 * len(n)):
            return True
    return False


def summarise_text(pairs: list[tuple[float, list[str]]]) -> tuple[list[dict], list[str]]:
    """Split OCR into always-on text (titles, watermarks) and text that comes and goes."""
    samples = [lines for _, lines in pairs]
    persistent: list[str] = []
    for lines in samples:
        for line in lines:
            if similar_in(line, persistent) or len(norm_text(line)) < 3:
                continue
            share = sum(similar_in(line, other) for other in samples) / max(1, len(samples))
            if share >= PERSISTENT_SHARE:
                persistent.append(line)
    changes, shown = [], []
    for t, lines in pairs:
        new = [x for x in lines if not is_persistent(x, persistent) and not similar_in(x, shown[-6:])]
        if new:
            changes.append({"t": t, "lines": new})
            shown.extend(new)
    return changes, persistent


def read_around_changes(video: Path, info: dict, motion_ev: list[dict], out: Path) -> None:
    """Grab the frame just before and just after each change and read the screen in both,
    like glancing at a sign. Brief text (a code, a caption, a price) is what sampling misses."""
    if not motion_ev:
        return
    width = min(1920, info["width"]) or None
    before = media.frames(video, [max(0.0, e["t"] - 0.05) for e in motion_ev], out / "frames", width=width)
    after = media.frames(video, [e["end"] for e in motion_ev], out / "frames", width=width)
    mids = media.frames(video, [round((e["t"] + e["end"]) / 2, 3) for e in motion_ev], out / "frames", width=640)
    lines = ocr_lines(before + after)
    n = len(motion_ev)
    for e, b, a, bf, af, mf in zip(motion_ev, lines[:n], lines[n:], before, after, mids):
        e["text_before"], e["text_after"] = b, a
        e["frame_before"], e["frame_after"], e["frame_mid"] = str(bf), str(af), str(mf)


def _seen_near(line: str, samples: list[tuple[float, list[str]]], start: float, end: float) -> bool:
    return any(start <= t <= end and similar_in(line, lines) for t, lines in samples)


def annotate_changes(motion_ev: list[dict], persistent: list[str],
                     samples: list[tuple[float, list[str]]] | None = None) -> None:
    """Add what text appeared or vanished and what colour the changed thing is.

    OCR can miss a line in one frame, so "text now" must also be absent from every other read in
    the 3 s before, and "text gone" absent from every read in the 3 s after.
    """
    samples = samples or []
    ranked = sorted(motion_ev, key=lambda e: -e["score"])[:MAX_COLOUR_PROBES]
    for e in motion_ev:
        b = [x for x in e.get("text_before", []) if not is_persistent(x, persistent)]
        a = [x for x in e.get("text_after", []) if not is_persistent(x, persistent)]
        appeared = [x for x in a if not similar_in(x, b) and not _seen_near(x, samples, e["t"] - 3, e["t"] - 0.01)]
        gone = [x for x in b if not similar_in(x, a) and not _seen_near(x, samples, e["end"] + 0.01, e["end"] + 3)]
        if appeared:
            e["detail"] += f"; text now: {' | '.join(appeared)}"
            e["score"] = round(e["score"] + 0.1, 3)
        if gone:
            e["detail"] += f"; text gone: {' | '.join(gone)}"
        if e in ranked and "frame_before" in e and not e["detail"].startswith("whole-frame"):
            # something that travels may be gone by the end: take its colour mid-way
            moving = "in place" not in e["detail"]
            target = e["frame_mid"] if moving else e["frame_after"]
            c = change_colour(Path(e["frame_before"]), Path(target), e["box"])
            if c and c["share"] > 0.005:
                new, old = colour_name(c["new"]), colour_name(c["old"])
                e["colour"] = new
                e["detail"] += f"; colour now {new}" + (f" (was {old})" if old != new else "")


def confirm_flashes(motion_ev: list[dict], candidates: list[tuple[int, int]]) -> list[dict]:
    """Two instant changes in one spot are a flash only if the spot goes back to how it was."""
    merged_into: dict[int, int] = {}
    for i, j in candidates:
        a, b = motion_ev[i], motion_ev[j]
        if "frame_before" not in a or "frame_after" not in b:
            continue
        box = [min(a["box"][0], b["box"][0]), min(a["box"][1], b["box"][1]),
               max(a["box"][0] + a["box"][2], b["box"][0] + b["box"][2]) - min(a["box"][0], b["box"][0]),
               max(a["box"][1] + a["box"][3], b["box"][1] + b["box"][3]) - min(a["box"][1], b["box"][1])]
        c = change_colour(Path(a["frame_before"]), Path(b["frame_after"]), box)
        if c is None or c["share"] > 0.01:
            continue                                 # it changed state (7 -> 8), not a flash
        stay = b["t"] - a["t"]
        what = f"a {a['colour']} thing" if a.get("colour") else "something"
        a["detail"] = (a["detail"].replace(", instant (one-frame change)", "")
                       + f"; FLASH: {what} appeared, stayed {stay:.2f}s, then vanished")
        a["end"] = b["end"]
        a["score"] = round(max(a["score"], b["score"]) + 0.1, 3)
        merged_into[j] = i
    return [e for k, e in enumerate(motion_ev) if k not in merged_into]
