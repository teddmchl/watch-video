"""People: who is on screen, who is talking, what their voices are like, and what their bodies do.

A person tells speakers apart by voice and by watching lips; reads body language from posture,
hands and head. This module does the same from cheap signals:
  - voices: pitch and timbre per speech segment (vsense voices), clustered into speakers S1, S2 ...
  - faces: tracked across frames (F1, F2 ...); a face whose mouth moves during a voice is talking
  - pose: body joints tracked (P1, P2 ...), turned into gestures: raised hand, wave, point,
    stand up / sit down, walk, touch face, arms crossed; head nods and shakes from face angles
"""

from __future__ import annotations

import json
import math
import statistics as st
import subprocess
from pathlib import Path

import media
from media import MediaError

EMB_SCALE = 0.55          # speaker-model cosine distance 0.34 (real voices split there) = SPEAKER_SPLIT
SPEAKER_SPLIT = 0.62      # timbre+pitch distance above which two voices are different people
MIN_SEGMENT = 0.5         # seconds of speech needed to judge a voice
WIN_LEN, WIN_HOP = 1.5, 0.75  # voice windows for hearing a change of speaker inside a stretch of talk
WIN_MARGIN = 0.15         # a window must be this much closer to another voice to count as that voice
ANCHOR_MIN = 2.0          # only stretches this long define what a voice sounds like
PILEUP_GAP = 0.04         # words this close together were stacked by the aligner
PAUSE_MIN = 0.2           # a gap this long between sounds is a pause (turns change at pauses)
PAUSE_DROP_DB = 18        # quieter than the usual speech level by this much counts as a pause
FACE_MATCH = 0.6          # face centres closer than this many face-widths are the same face
MIN_BODY = 0.15           # neck-to-hip under this share of frame height: too small for gestures
POSE_MATCH = 0.12         # neck positions closer than this (frame share) are the same body
TRACK_GAP = 1.0           # seconds a face or body may vanish and still be the same track
HAND_SWEEP = 0.15         # hand travels this share of the frame width sideways = a wave
HAND_GAP = 0.8            # a hand may vanish this long (motion blur) and still be the same gesture
EXPRESSION_MIN_FACE = 0.06  # faces narrower than this share of the frame are too small for landmarks
MOUTH_TALKING = 0.018     # spread of lip opening that means the lips are moving
CHUNK = 250               # images per vsense call


def people_fps(duration: float) -> int:
    return 5 if duration <= 180 else 3 if duration <= 900 else 1


# ---------------------------------------------------------------- voices and speakers


SKILL_DIR = Path(__file__).resolve().parent.parent
EMBED_PY = media.HOME / ".venv" / "bin" / "python"
EMBED_MODEL = media.HOME / "models" / "wespeaker_en_voxceleb_resnet34_LM.onnx"


def embeddings_available() -> bool:
    return EMBED_PY.exists() and EMBED_MODEL.exists()


def _embed(wav: Path, spans: list[tuple[float, float]]) -> list[list[float] | None]:
    """Speaker-model vectors for each span; empty list if the model isn't installed or fails."""
    if not embeddings_available() or not spans:
        return []
    try:
        res = subprocess.run([str(EMBED_PY), "-I", str(Path(__file__).with_name("embed.py")), str(EMBED_MODEL), str(wav)],
                             input=json.dumps(spans), capture_output=True, text=True, timeout=1800)
        return json.loads(res.stdout) if res.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _voice_distance(a: dict, b: dict) -> float:
    """With the speaker model: cosine distance scaled so 1.0 ~ clearly different people.
    Without it: timbre difference scaled by how much each voice varies, plus pitch."""
    if a.get("emb") and b.get("emb"):
        return (1 - sum(x * y for x, y in zip(a["emb"], b["emb"]))) / EMB_SCALE
    sd = [(x + y) / 2 + 1.0 for x, y in zip(a["mfcc_sd"], b["mfcc_sd"])]
    timbre = sum(abs(x - y) / s for x, y, s in zip(a["mfcc"], b["mfcc"], sd)) / len(sd)
    pitch = abs(math.log2(a["f0"] / b["f0"])) if a.get("f0") and b.get("f0") else 0.0
    return timbre + 1.2 * pitch


def _cluster(items: list[dict]) -> list[int]:
    """Average-linkage clustering; stops when the closest two groups sound like different people."""
    groups = [[i] for i in range(len(items))]
    dist = {(i, j): _voice_distance(items[i], items[j]) for i in range(len(items)) for j in range(i + 1, len(items))}

    def link(g: list[int], h: list[int]) -> float:
        return st.mean(dist[(min(i, j), max(i, j))] for i in g for j in h)

    while len(groups) > 1:
        best = min(((link(g, h), a, b) for a, g in enumerate(groups) for b, h in enumerate(groups) if a < b))
        if best[0] > SPEAKER_SPLIT:
            break
        _, a, b = best
        groups[a] += groups.pop(b)
    label = [0] * len(items)
    for k, g in enumerate(sorted(groups, key=lambda g: min(items[i]["start"] for i in g))):
        for i in g:
            label[i] = k
    return label


def describe_voice(feats: list[dict], segs: list[dict]) -> str:
    f0s = [f["f0"] for f in feats if f.get("f0")]
    parts = []
    if f0s:
        f0 = st.median(f0s)
        band = "low" if f0 < 140 else "high" if f0 > 200 else "middle"
        parts.append(f"{band} pitch (~{f0:.0f} Hz)")
        spread = st.median([f["f0_hi"] / f["f0_lo"] for f in feats if f.get("f0_lo")] or [1])
        parts.append("lively intonation" if spread > 1.6 else "flat intonation" if spread < 1.25 else "even intonation")
    words = sum(len(s.get("words", [])) for s in segs)
    secs = sum(s["end"] - s["start"] for s in segs)
    if words and secs:
        rate = words / secs
        parts.append(f"{'fast' if rate > 3.2 else 'slow' if rate < 1.8 else 'normal'} pace ({rate:.1f} words/s)")
    return ", ".join(parts) or "too little speech to describe"


def talk_chunks(levels: list[tuple[float, float]], speech: list[dict]) -> list[tuple[float, float]]:
    """Stretches of voice between pauses of 0.2 s or more: a change of speaker almost always
    comes after one. Measured against this recording's own speech level."""
    if not speech or not levels:
        return []
    inside = [db for t, db in levels if any(s["start"] <= t <= s["end"] for s in speech)]
    if not inside:
        return []
    ref = sorted(inside)[int(0.7 * (len(inside) - 1))]
    thr = ref - PAUSE_DROP_DB
    chunks: list[list[float]] = []
    for t, db in levels:
        if db < thr:
            continue
        if chunks and t - chunks[-1][1] <= PAUSE_MIN:
            chunks[-1][1] = t + 0.05
        else:
            chunks.append([t, t + 0.05])
    keep = []
    for a, b in chunks:
        if b - a >= 0.15 and any(s["start"] - 0.3 <= a <= s["end"] + 0.3 for s in speech):
            keep.append((round(a, 2), round(b, 2)))
    return keep


def _voice_feats(wav: Path, spans: list[tuple[float, float]]) -> list[dict]:
    out: list[dict] = []
    for k in range(0, len(spans), 400):                      # keep the argument list short
        arg = ",".join(f"{a:.2f}-{b:.2f}" for a, b in spans[k:k + 400])
        out += media.vsense_json("voices", "--wav", str(wav), "--spans", arg) or []
    embs = _embed(wav, spans)
    if len(embs) == len(out):
        for f, e in zip(out, embs):
            if e:
                f["emb"] = e
    return out


def _centroid(feats: list[dict]) -> dict:
    n = len(feats)
    c: dict = {"mfcc": [sum(f["mfcc"][k] for f in feats) / n for k in range(len(feats[0]["mfcc"]))],
               "mfcc_sd": [sum(f["mfcc_sd"][k] for f in feats) / n for k in range(len(feats[0]["mfcc_sd"]))]}
    f0s = [f["f0"] for f in feats if f.get("f0")]
    c["f0"] = st.median(f0s) if f0s else None
    if all(f.get("emb") for f in feats):
        v = [sum(f["emb"][k] for f in feats) for k in range(len(feats[0]["emb"]))]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        c["emb"] = [x / norm for x in v]
    return c


def _ranked(f: dict, cents: dict[int, dict]) -> list[tuple[float, int]]:
    return sorted((_voice_distance(f, c), k) for k, c in cents.items())


def _segment(wav: Path, chunks: list[tuple[float, float]]):
    """Learn each voice from the longer stretches only (short ones are too noisy to define a
    voice), then label everything against those voices: short stretches by nearest voice, long
    ones window by window, so a hand-over with no pause still becomes two turns."""
    anchors = [c for c in chunks if c[1] - c[0] >= ANCHOR_MIN] or [c for c in chunks if c[1] - c[0] >= MIN_SEGMENT]
    feats = _voice_feats(wav, anchors) if anchors else []
    usable = [(c, f) for c, f in zip(anchors, feats) if f.get("mfcc") and f.get("frames", 0) >= 20]
    if not usable:
        return [], []
    labels = _cluster([f for _, f in usable])
    cents = {k: _centroid([f for (_, f), lab in zip(usable, labels) if lab == k]) for k in set(labels)}
    known = {c: lab for (c, _), lab in zip(usable, labels)}

    others = [c for c in chunks if c not in known and c[1] - c[0] >= 0.4]
    for c, f in zip(others, _voice_feats(wav, others) if others else []):
        if f.get("mfcc") and f.get("frames", 0) >= 10:
            known[c] = _ranked(f, cents)[0][1]

    long_ = [c for c in chunks if c[1] - c[0] >= 2 * WIN_LEN and c in known]
    spans = [(round(a + k * WIN_HOP, 2), round(a + k * WIN_HOP + WIN_LEN, 2))
             for a, b in long_ for k in range(int((b - a - WIN_LEN) / WIN_HOP) + 1)]
    wfeats = dict(zip(spans, _voice_feats(wav, spans))) if spans and len(cents) > 1 else {}

    pieces: list[tuple[float, float, int]] = []
    for c in chunks:
        if c not in known:
            continue
        base = known[c]
        wins = [(w, wfeats[w]) for w in spans if c[0] <= w[0] and w[1] <= c[1] + 0.01 and w in wfeats]
        labs = []
        for w, f in wins:
            r = _ranked(f, cents) if f.get("mfcc") else []
            labs.append(r[0][1] if len(r) > 1 and r[1][0] - r[0][0] >= WIN_MARGIN else base)
        # a different voice must hold for 2+ windows in a row to count (one window is noise)
        cuts, cur, k = [], base, 0
        while k < len(labs):
            j = k
            while j + 1 < len(labs) and labs[j + 1] == labs[k]:
                j += 1
            if labs[k] != cur and j - k + 1 >= 2:
                cuts.append((round(wins[k][0][0] + WIN_HOP / 2, 2), labs[k]))
                cur = labs[k]
            k = j + 1
        edges = [(c[0], base)] + cuts
        for (x, lab), nxt in zip(edges, edges[1:] + [(c[1], None)]):
            if nxt[0] - x >= 0.2:
                pieces.append((x, nxt[0], lab))
    return pieces, [(c, f, lab) for (c, f), lab in zip(usable, labels)]


def _pileups(words: list[dict]) -> list[float]:
    """Times where the aligner stacks 3+ words on one instant. It does that when it can't
    follow the audio, and the usual reason in conversation is two people talking at once."""
    out, i = [], 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and abs(words[j + 1]["t"] - words[i]["t"]) <= PILEUP_GAP:
            j += 1
        if j - i >= 2:
            out.append(words[i]["t"])
        i = j + 1
    return out


def _overlaps(turns: list[dict], words: list[dict]) -> list[dict]:
    """Crosstalk: a word pile-up right at a change of speaker. Whoever was talking on either
    side of the change were talking over each other there."""
    spans = []
    for t in _pileups(words):
        for prev, nxt in zip(turns, turns[1:]):
            if prev["speaker"].rstrip("?") != nxt["speaker"].rstrip("?") and abs(nxt["start"] - t) <= 1.5:
                spans.append({"start": round(min(t, nxt["start"]) - 0.5, 2), "end": round(max(t, nxt["start"]) + 0.5, 2),
                              "who": sorted({prev["speaker"].rstrip("?"), nxt["speaker"].rstrip("?")})})
                break
    return spans


def speakers(wav: Path, speech: list[dict], levels: list[tuple[float, float]]) -> dict:
    """Who speaks when, word by word, as turns (S1, S2 ...), and what each voice is like.
    Never guesses gender or identity."""
    chunks = talk_chunks(levels, speech)
    pieces, learned = _segment(wav, chunks)
    if not pieces:
        return {"voices": {}, "turns": [], "overlaps": []}
    marks = [((a + b) / 2, a, b, f"S{k + 1}") for a, b, k in pieces]

    def who(t: float) -> str:
        hit = next((m[3] for m in marks if m[1] - 0.15 <= t <= m[2] + 0.15), None)
        return hit or min(marks, key=lambda m: abs(m[0] - t))[3] + "?"

    words = [dict(w, seg=n) for n, s in enumerate(speech) if s.get("voice", True) for w in s.get("words", [])]
    turns: list[dict] = []
    for w in words:
        sp = who(w["t"])
        if turns and turns[-1]["speaker"].rstrip("?") == sp.rstrip("?") and w["t"] - turns[-1]["last"] < 2.0:
            turns[-1]["text"] += " " + w["w"]
            turns[-1]["last"] = turns[-1]["end"] = w["t"]
            if not sp.endswith("?"):
                turns[-1]["speaker"] = sp
        else:
            turns.append({"start": w["t"], "end": w["t"], "last": w["t"], "speaker": sp, "text": w["w"]})
    for t, nxt in zip(turns, turns[1:] + [None]):
        end = next((b for a, b, _ in pieces if a - 0.15 <= t["last"] <= b + 0.15), t["last"] + 0.4)
        t["end"] = round(min(end, nxt["start"]) if nxt else end, 2)
        t.pop("last")
    for s in speech:                       # whole segments keep their main speaker for `listen`
        mid = [who(w["t"]).rstrip("?") for w in s.get("words", [])]
        if mid:
            s["speaker"] = max(set(mid), key=mid.count)
    voices = {}
    for k in sorted({lab for _, _, lab in learned}):
        name = f"S{k + 1}"
        mine = [f for _, f, lab in learned if lab == k]
        segs = [{"start": t["start"], "end": t["end"], "words": t["text"].split()} for t in turns
                if t["speaker"].rstrip("?") == name]
        voices[name] = {"describe": describe_voice(mine, segs),
                        "talks_for": round(sum(b - a for a, b, lab in pieces if lab == k), 1)}
    overlaps = _overlaps(turns, words)
    for t in turns:
        both = [o for o in overlaps if o["start"] < t["end"] and o["end"] > t["start"]]
        if both:
            t["overlap"] = sorted({x for o in both for x in o["who"]} - {t["speaker"].rstrip("?")})
    return {"voices": voices, "turns": turns, "overlaps": overlaps}


# ---------------------------------------------------------------- faces and bodies over time


def sample(video: Path, out: Path, fps: int) -> list[tuple[float, Path]]:
    d = out / "people"
    d.mkdir(parents=True, exist_ok=True)
    if not any(d.glob("*.jpg")):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale=640:-2",
                        "-q:v", "4", str(d / "%06d.jpg")], check=True, timeout=3600)
    files = sorted(d.glob("*.jpg"))
    return [(round(i / fps, 2), f) for i, f in enumerate(files)]


def _batched(cmd: str, files: list[Path]) -> list[dict]:
    res: list[dict] = []
    for k in range(0, len(files), CHUNK):
        res += media.vsense_json(cmd, *map(str, files[k:k + CHUNK])) or []
    return res


def _centre(box: list[float]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def track(frames: list[tuple[float, list[dict]]], key, match, cuts: list[float] | None = None) -> list[list[tuple[float, dict]]]:
    """Greedy tracking: attach each detection to the closest live track, else start a new one.
    Nothing carries across a cut: the next shot is a new view, even of the same person."""
    tracks: list[list[tuple[float, dict]]] = []
    cuts = cuts or []
    for t, dets in frames:
        live = [tr for tr in tracks if t - tr[-1][0] <= TRACK_GAP
                and not any(tr[-1][0] < c <= t for c in cuts)]
        used: set[int] = set()
        for d in dets:
            p = key(d)
            if p is None:
                continue
            best = min(((match(key(tr[-1][1]), p, d), n) for n, tr in enumerate(live) if n not in used),
                       default=(None, None))
            if best[0] is not None and best[0] < 1.0:
                live[best[1]].append((t, d))
                used.add(best[1])
            else:
                tracks.append([(t, d)])
                live.append(tracks[-1])
                used.add(len(live) - 1)
    return [tr for tr in tracks if len(tr) >= 3]


def where(x: float) -> str:
    return "left" if x < 0.36 else "right" if x > 0.64 else "centre"


def face_tracks(faces: list[dict], times: list[float], cuts: list[float] | None = None) -> list[dict]:
    frames = [(t, r.get("faces", [])) for t, r in zip(times, faces)]
    raw = track(frames, lambda d: _centre(d["box"]),
                lambda a, b, d: math.dist(a, b) / max(0.02, d["box"][2]) / FACE_MATCH, cuts)
    out = []
    for n, tr in enumerate(raw, 1):
        xs = [_centre(d["box"])[0] for _, d in tr]
        out.append({"id": f"F{n}", "start": tr[0][0], "end": tr[-1][0], "where": where(st.median(xs)),
                    "size": round(st.median(d["box"][2] for _, d in tr), 2), "samples": tr})
    return out


def _series(tr: list[tuple[float, dict]], field: str, a: float, b: float) -> list[float]:
    return [d[field] for t, d in tr if a <= t <= b and d.get(field) is not None]


def who_talks(speech: list[dict], faces: list[dict], voice: list[tuple[float, float]]) -> None:
    """A face whose lips move while a voice is heard is the one talking; none means off-screen."""
    for s in speech:
        cands = []
        for f in faces:
            m = _series(f["samples"], "mouth", s["start"], s["end"])
            if len(m) >= 3:
                cands.append((st.pstdev(m), f))
        if not cands:
            continue
        spread, f = max(cands, key=lambda c: c[0])
        if spread >= MOUTH_TALKING:
            s["face"] = f["id"]
        else:
            s["face"] = "off-screen"


def _swings(vals: list[float], amp: float) -> int:
    """Direction reversals bigger than amp: 2+ quick ones are a nod or a shake."""
    turns, last_ext, direction = 0, vals[0] if vals else 0, 0
    for v in vals[1:]:
        d = v - last_ext
        if direction >= 0 and d <= -amp or direction <= 0 and d >= amp:
            turns += direction != 0
            direction = -1 if d < 0 else 1
            last_ext = v
        elif direction * d > 0:
            last_ext = v
    return turns


def head_events(faces: list[dict], fps: int) -> list[dict]:
    if fps < 5:
        return []                                           # too sparse to see a nod
    ev = []
    for f in faces:
        tr = f["samples"]
        for k in range(0, len(tr), max(1, fps // 2)):
            win = [d for t, d in tr if tr[k][0] <= t <= tr[k][0] + 1.6]
            for field, what in (("pitch", "nods"), ("yaw", "shakes their head")):
                vals = [d[field] for d in win if d.get(field) is not None]
                if len(vals) >= 5 and _swings(vals, 0.07) >= 2:
                    stamped = [(t, d[field]) for t, d in tr if tr[k][0] <= t <= tr[k][0] + 1.6 and d.get(field) is not None]
                    t0 = next((t for t, v in stamped if abs(v - stamped[0][1]) >= 0.035), tr[k][0])
                    if not any(e["face"] == f["id"] and e["what"] == what and t0 - e["end"] < 1 for e in ev):
                        ev.append({"face": f["id"], "what": what, "t": t0, "end": t0 + 1.6})
    return [{"kind": "body", "t": e["t"], "end": round(e["end"], 2), "score": 0.55,
             "detail": f"{e['face']} ({_face_where(faces, e['face'])}) {e['what']}"} for e in ev]


def expression_events(faces: list[dict], fps: int) -> list[dict]:
    """Smiles, raised eyebrows, eyes closed: each face against its own resting face (the median),
    since faces differ. Only for faces big enough for landmarks to be trusted."""
    ev = []
    min_run = max(2, round(0.4 * fps))
    for f in faces:
        tr = f["samples"]
        if f["size"] < EXPRESSION_MIN_FACE or len(tr) < 10:
            continue
        base = {k: st.median(v) for k in ("smile", "brow", "eyes", "mouth_w")
                if (v := [d[k] for _, d in tr if d.get(k) is not None])}
        if len(base) < 4:
            continue

        def states(d: dict) -> set[str]:
            out = set()
            if d.get("smile") is not None and d["smile"] - base["smile"] > 0.025 and d.get("mouth_w", 0) > 1.06 * base["mouth_w"]:
                out.add("smiles")
            if d.get("brow") is not None and d["brow"] - base["brow"] > 0.03:
                out.add("raises their eyebrows")
            if d.get("eyes") is not None and base["eyes"] > 0 and d["eyes"] < 0.45 * base["eyes"]:
                out.add("closes their eyes")
            return out

        runs: dict[str, list[float]] = {}
        for t, d in tr + [(math.inf, {})]:
            now = states(d) if d else set()
            for name in list(runs):
                if name not in now:
                    ts_ = runs.pop(name)
                    long_enough = len(ts_) >= (max(min_run, round(0.6 * fps)) if name == "closes their eyes" else min_run)
                    if long_enough:
                        ev.append({"kind": "face", "t": ts_[0], "end": ts_[-1], "score": 0.5,
                                   "detail": f"{f['id']} ({f['where']} of frame) {name} for {ts_[-1] - ts_[0] + 1 / fps:.1f}s"})
            for name in now:
                runs.setdefault(name, []).append(t)
    return ev


def face_sheet(video: Path, out: Path, faces: list[dict], events: list[dict], per_face: int = 6) -> Path | None:
    """Close-ups of each tracked face over time, so expressions can be read by looking."""
    tiles = []
    for f in faces[:6]:
        boxes = f["boxes"]
        moments = [e["t"] for e in events if e["detail"].startswith(f["id"] + " ")]
        times = sorted(set(moments[:per_face // 2] + [boxes[int(i * (len(boxes) - 1) / max(1, per_face - 1))][0]
                                                       for i in range(per_face)]))[:per_face]
        for t in times:
            bt, x, y, w, h = min(boxes, key=lambda b: abs(b[0] - t))
            pad = 0.45
            cx, cy, side = x + w / 2, y + h / 2, max(w, h) * (1 + 2 * pad)
            tiles.append((f["id"], bt, (max(0, cx - side / 2), max(0, cy - side / 2), side)))
    if not tiles:
        return None
    crops_dir = out / "faces"
    crops_dir.mkdir(exist_ok=True)
    labels = []
    for fid, t, (x0, y0, side) in tiles:
        full = media.frame(video, t, crops_dir / f"full_{t:09.3f}.jpg", width=None)
        crop = crops_dir / f"{fid}_{t:09.3f}.jpg"
        if not crop.exists():
            media.run(["ffmpeg", "-y", "-v", "error", "-i", str(full), "-vf",
                       f"crop='min(iw*{side:.4f},ih)':'min(iw*{side:.4f},ih)':iw*{x0:.4f}:ih*{y0:.4f},scale=256:256",
                       str(crop)])
        mm, ss = divmod(t, 60)
        labels.append(f"{fid} {int(mm):02d}:{ss:05.2f}={crop}")
    sheet = out / "faces.jpg"
    media.vsense("sheet", "--out", str(sheet), "--cols", "6", "--tile", "256",
                 "--title", "Faces up close (read expressions here)", *labels)
    return sheet


def _face_where(faces: list[dict], fid: str) -> str:
    return next((f"{f['where']} of frame" for f in faces if f["id"] == fid), "")


def _j(d: dict, name: str):
    v = d.get(name)
    return (v[0], v[1]) if v else None


def _angle(a, b, c) -> float:
    v1, v2 = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    n = math.hypot(*v1) * math.hypot(*v2)
    return math.degrees(math.acos(max(-1, min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / n)))) if n else 0


def _neck(d: dict):
    return _j(d, "neck") or _j(d, "nose") or _j(d, "root")


def pose_states(d: dict) -> set[str]:
    """What one body is doing in one frame (sides are the person's own left and right)."""
    s: set[str] = set()
    nose, neck = _j(d, "nose"), _neck(d)
    lsh, rsh = _j(d, "lsh"), _j(d, "rsh")
    shw = math.dist(lsh, rsh) if lsh and rsh else 0.08
    for side, sh, el, wr in (("left", lsh, _j(d, "lel"), _j(d, "lwr")), ("right", rsh, _j(d, "rel"), _j(d, "rwr"))):
        if not wr:
            continue
        top = nose[1] if nose else (neck[1] - shw if neck else None)
        if top is not None and wr[1] < top - 0.02:
            s.add(f"raises their {side} hand")
        elif (sh and el and _angle(sh, el, wr) > 150 and abs(wr[0] - sh[0]) > 1.5 * shw
              and body_size(d) and shw > 0.35 * body_size(d)):     # side-on, arms look extended
            s.add(f"points to the {'left' if wr[0] < sh[0] else 'right'} of frame")
        if nose and math.dist(wr, nose) < 0.6 * shw:
            s.add("touches their face")
    lwr, rwr, lel, rel = _j(d, "lwr"), _j(d, "rwr"), _j(d, "lel"), _j(d, "rel")
    if lwr and rwr and lel and rel and math.dist(lwr, rel) < 0.5 * shw and math.dist(rwr, lel) < 0.5 * shw:
        s.add("crosses their arms")
    return s


def body_size(d: dict) -> float:
    """Neck-to-hip length as a share of frame height (0 if unknown)."""
    neck, hips = _neck(d), [p for p in (_j(d, "lhip"), _j(d, "rhip")) if p]
    return abs(st.mean(h[1] for h in hips) - neck[1]) if neck and hips else 0.0


def body_events(poses: list[dict], times: list[float], fps: int,
                cuts: list[float] | None = None) -> tuple[list[dict], list[dict]]:
    frames = [(t, r.get("people", [])) for t, r in zip(times, poses)]
    tracks = track(frames, _neck, lambda a, b, d: math.dist(a, b) / POSE_MATCH, cuts)
    bodies, ev = [], []
    min_frames = max(2, round(0.4 * fps))
    for n, tr in enumerate(tracks, 1):
        pid = f"P{n}"
        x = st.median(_neck(d)[0] for _, d in tr)
        bodies.append({"id": pid, "start": tr[0][0], "end": tr[-1][0], "where": where(x)})
        label = f"{pid} ({where(x)} of frame)"
        sizes = [z for _, d in tr if (z := body_size(d))]
        if not sizes or st.median(sizes) < MIN_BODY:
            continue                     # too small (or too cut off) to read gestures reliably
        runs: dict[str, list[float]] = {}
        for t, d in tr + [(math.inf, {})]:
            states = pose_states(d) if d else set()
            for name in list(runs):
                if name not in states:
                    ts = runs.pop(name)
                    if len(ts) >= min_frames:
                        what = name
                        if "raises" in name:
                            wr = "lwr" if "left" in name else "rwr"
                            xs = [dd[wr][0] for tt, dd in tr if ts[0] <= tt <= ts[-1] and dd.get(wr)]
                            if len(xs) >= 4 and _swings(xs, 0.02) >= 2:
                                what = name.replace("raises", "waves")
                        ev.append({"kind": "body", "t": ts[0], "end": ts[-1], "score": 0.6,
                                   "detail": f"{label} {what} for {ts[-1] - ts[0] + 1 / fps:.1f}s"})
            for name in states:
                runs.setdefault(name, []).append(t)
        ev += _travel(tr, label)
    return bodies, ev


def _face_at(faces: list[dict], t: float):
    hits = [f for f in faces if f["start"] - 0.3 <= t <= f["end"] + 0.3]
    return hits[0]["id"] if len(hits) == 1 else None


def hand_events(poses: list[dict], faces_raw: list[dict], times: list[float], faces: list[dict],
                fps: int, cuts: list[float] | None = None) -> list[dict]:
    """Gestures from hands alone (webcams and close-ups show no hips, so body pose finds no
    one), judged against the face. Each stretch of a visible hand is read as one gesture:
    a big side-to-side sweep is a wave, a hand mostly over the face is touching it, a hand
    held at face height is raised. A fast hand blurs out for a frame or two; gaps under
    HAND_GAP are bridged."""
    episodes: list[list[tuple]] = []                             # (t, x, on_face, high, above)
    cuts = cuts or []
    for t, pr, fr in zip(times, poses, faces_raw):
        boxes = [f["box"] for f in fr.get("faces", [])]
        if not boxes:
            continue                         # no face to judge against (backs, crowds): say nothing
        for h in pr.get("hands", [])[:1]:                         # the most confident hand
            cx, cy = h["c"]
            x, y, w, hh = min(boxes, key=lambda b: math.dist((cx, cy), _centre(b)))
            on_face = x - 0.1 * w <= cx <= x + 1.1 * w and y - 0.1 * hh <= cy <= y + 1.3 * hh   # down to the chin
            near = h["box"][2] > 1.3 * w             # bigger than the face: right in front of the lens
            sample = (t, cx, on_face, cy < y + hh or near, cy < y and not near)
            last = episodes[-1][-1][0] if episodes else None
            if last is not None and t - last <= HAND_GAP and not any(last < c <= t for c in cuts):
                episodes[-1].append(sample)
            else:
                episodes.append([sample])
    ev = []
    for ep in episodes:
        if len(ep) < max(2, round(0.4 * fps)):
            continue
        t0, t1 = ep[0][0], ep[-1][0]
        up = [e for e in ep if e[3]]                 # judge the gesture while the hand is up,
        xs = [e[1] for e in up] or [0.0]             # so lowering the arm afterwards isn't a wave
        on = sum(e[2] for e in ep) / len(ep)
        high = len(up) / len(ep)
        above = sum(e[4] for e in up) / max(1, len(up))
        if high >= 0.5 and above >= 0.6:
            what = "raises a hand above their head (pointing or reaching up?)"
        elif high >= 0.5 and (max(xs) - min(xs) > HAND_SWEEP or _swings(xs, 0.03) >= 2):
            what = "waves"                                        # a wave happens at face height
        elif on >= 0.6:
            what = "touches their face"
        elif high >= 0.6:
            what = "raises a hand"
        else:
            continue                                              # a hand low in shot: nothing to say
        who = _face_at(faces, t0)
        ev.append({"kind": "body", "t": t0, "end": t1, "score": 0.65,
                   "detail": f"{who or 'someone'} {what} for {t1 - t0 + 1 / fps:.1f}s"})
    return ev


def _travel(tr: list[tuple[float, dict]], label: str) -> list[dict]:
    """Standing up, sitting down, walking across: big moves of the hips or neck within 2-4 s."""
    ev, k = [], 0
    while k < len(tr):
        t0, d0 = tr[k]
        hips0 = [p for p in (_j(d0, "lhip"), _j(d0, "rhip")) if p]
        neck0 = _neck(d0)
        for t1, d1 in tr[k + 1:]:
            if t1 - t0 > 4:
                break
            hips1 = [p for p in (_j(d1, "lhip"), _j(d1, "rhip")) if p]
            neck1 = _neck(d1)
            what = None
            if hips0 and hips1 and t1 - t0 <= 2.5 and neck0 and neck1:
                torso = abs(st.mean(h[1] for h in hips0) - neck0[1]) or 0.2
                dy = st.mean(h[1] for h in hips1) - st.mean(h[1] for h in hips0)
                # the new height has to hold for a second, or it's a detection jumping about
                after = [st.mean(p[1] for p in hh) for tt, dd in tr if t1 <= tt <= t1 + 1.0
                         if (hh := [p for p in (_j(dd, "lhip"), _j(dd, "rhip")) if p])]
                base_y = st.mean(h[1] for h in hips0)
                held = len(after) >= 3 and all(abs((y - base_y) - dy) < 0.3 * abs(dy) for y in after)
                if held and dy < -0.6 * torso:
                    what = "stands up"
                elif held and dy > 0.6 * torso:
                    what = "sits or crouches down"
            if not what and neck0 and neck1 and abs(neck1[0] - neck0[0]) > 0.25:
                what = f"walks to the {'left' if neck1[0] < neck0[0] else 'right'}"
            if what:
                # start when the move actually begins, not when the search window opened
                still = [t for t, d in tr if t0 <= t <= t1 and _neck(d) and neck0 and math.dist(_neck(d), neck0) < 0.03]
                t_start = max(still) if still else t0
                ev.append({"kind": "body", "t": t_start, "end": t1, "score": 0.6, "detail": f"{label} {what}"})
                k = next((n for n, (t, _) in enumerate(tr) if t > t1), len(tr)) - 1
                break
        k += 1
    return ev


def scan(video: Path, info: dict, out: Path, speech: list[dict], voice, cuts: list[float] | None = None) -> dict:
    """Faces, bodies, gestures and who-is-talking for a video with people in it."""
    fps = people_fps(info["duration"])
    samples = sample(video, out, fps)
    if not samples:
        return {}
    times, files = [t for t, _ in samples], [f for _, f in samples]
    try:
        faces_raw = _batched("faces", files)
        poses_raw = _batched("pose", files)
    except MediaError as exc:
        return {"error": str(exc)}
    faces = face_tracks(faces_raw, times, cuts)
    who_talks(speech, faces, voice)
    bodies, ev = body_events(poses_raw, times, fps, cuts)
    if not ev or not bodies:
        ev += hand_events(poses_raw, faces_raw, times, faces, fps, cuts)
    ev += head_events(faces, fps)
    ev += expression_events(faces, fps)
    for f in faces:
        f["boxes"] = [[t, *d["box"]] for t, d in f.pop("samples")]
    sheet = None
    try:
        sheet = face_sheet(video, out, faces, ev)
    except MediaError as exc:
        print(f"warning: face close-ups unavailable: {exc}")
    return {"fps": fps, "faces": faces, "bodies": bodies, "events": sorted(ev, key=lambda e: e["t"]),
            "face_sheet": str(sheet) if sheet else None}
