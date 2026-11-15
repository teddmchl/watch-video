"""Thin wrappers around ffmpeg, yt-dlp, whisper-cli and the vsense helper.

Every function raises MediaError with a readable message instead of failing silently.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
VSENSE = SKILL_DIR / "bin" / "vsense"
VSENSE_DIR = SKILL_DIR / "scripts" / "vsense"
WHISPER_DTW = "small"   # alignment-head preset; must match the model below
WHISPER_MODEL = SKILL_DIR / "models" / "ggml-small.bin"


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], *, timeout: float | None = None, binary: bool = False,
        cwd: Path | None = None) -> str | bytes:
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, cwd=cwd)
    except FileNotFoundError as exc:
        raise MediaError(f"missing tool: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"timed out: {' '.join(cmd[:3])} ...") from exc
    if res.returncode != 0:
        err = res.stderr.decode(errors="replace").strip().splitlines()[-5:]
        raise MediaError(f"{cmd[0]} failed: {' | '.join(err)}")
    return res.stdout if binary else res.stdout.decode(errors="replace")


def ensure_vsense() -> Path:
    """Compile the Swift helper if the binary is missing or older than its source."""
    sources = sorted(VSENSE_DIR.glob("*.swift"))
    newest = max(s.stat().st_mtime for s in sources)
    if not VSENSE.exists() or VSENSE.stat().st_mtime < newest:
        VSENSE.parent.mkdir(exist_ok=True)
        run(["swiftc", "-O", "-swift-version", "5", *map(str, sources), "-o", str(VSENSE)], timeout=600)
    return VSENSE


def vsense(*args: str, timeout: float = 600) -> str:
    """Apple's Neural Engine occasionally refuses a request (e5rt errors); retry before failing."""
    binary = str(ensure_vsense())
    for attempt in range(3):
        try:
            return run([binary, *args], timeout=timeout)
        except MediaError:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def vsense_json(*args: str, timeout: float = 600):
    return json.loads(vsense(*args, timeout=timeout) or "null")


# ---------------------------------------------------------------- sources


def is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source))


def fetch(url: str, out_dir: Path) -> Path:
    """Download a link with yt-dlp, capped at 720p (enough to see, cheap to scan)."""
    if not shutil.which("yt-dlp"):
        raise MediaError("yt-dlp is not installed (brew install yt-dlp)")
    existing = sorted(out_dir.glob("source.*"))
    if existing:
        return existing[0]
    run(
        [
            "yt-dlp", "--no-playlist", "--quiet", "--no-warnings",
            "-f", "bv*[height<=720]+ba/b[height<=720]/b",
            "--merge-output-format", "mp4",
            "-o", str(out_dir / "source.%(ext)s"), url,
        ],
        timeout=3600,
    )
    found = sorted(out_dir.glob("source.*"))
    if not found:
        raise MediaError("yt-dlp finished but no file was written")
    return found[0]


def probe(video: Path) -> dict:
    raw = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)])
    info = json.loads(raw)
    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    astreams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not vstreams:
        raise MediaError(f"no video stream in {video}")
    v = vstreams[0]
    num, _, den = v.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    duration = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    if duration <= 0:
        raise MediaError("could not read video duration")
    return {
        "path": str(video),
        "duration": round(duration, 3),
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "fps": round(fps, 3),
        "has_audio": bool(astreams),
        "title": info.get("format", {}).get("tags", {}).get("title"),
    }


# ---------------------------------------------------------------- frames


def frame(video: Path, t: float, out: Path, width: int | None = 640) -> Path:
    if out.exists():
        return out
    scale = ["-vf", f"scale={width}:-2"] if width else []
    last_error: MediaError | None = None
    # Seeking at or past the last frame yields nothing; step back a little and retry.
    for back in (0.0, 0.1, 0.3, 1.0):
        try:
            run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t - back):.3f}", "-i", str(video),
                 "-frames:v", "1", *scale, "-q:v", "3", str(out)],
                timeout=120,
            )
        except MediaError as exc:
            last_error = exc
            continue
        if out.exists():
            return out
    raise MediaError(f"no frame at {t:.2f}s ({last_error})")


def frames(video: Path, times: list[float], out_dir: Path, width: int | None = 640) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"w{width}" if width else "full"
    targets = [(t, out_dir / f"f_{t:09.3f}_{tag}.jpg") for t in times]
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda p: frame(video, p[0], p[1], width), targets))


# ---------------------------------------------------------------- audio


def extract_audio(video: Path, out: Path) -> Path:
    if not out.exists():
        run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out)],
            timeout=1800)
    return out


def _words(tokens: list[dict]) -> list[dict]:
    """Join sub-word tokens into words, each timed by DTW (centiseconds) or the token offset."""
    words: list[dict] = []
    for tok in tokens:
        text = re.sub(r"<\|[^|]*\|>", "", tok.get("text", ""))      # model control tokens
        if text.startswith("[_") or not text.strip():
            continue
        dtw = tok.get("t_dtw", -1)
        t = dtw / 100 if dtw is not None and dtw >= 0 else tok["offsets"]["from"] / 1000
        if text.startswith(" ") or not words:
            words.append({"w": text.strip(), "t": round(t, 2), "p": round(tok.get("p", 0), 2)})
        else:
            words[-1]["w"] += text
    return words


def _whisper(model: Path, dtw: str, wav: Path, out_base: Path, language: str, prompt: str | None = None) -> dict:
    """One whisper pass: timed segments, each with a confidence and DTW-timed words."""
    if not model.exists():
        raise MediaError(f"whisper model missing: {model}")
    if not shutil.which("whisper-cli"):
        raise MediaError("whisper-cli is not installed (brew install whisper-cpp)")
    out_json = out_base.with_suffix(".json")
    if not out_json.exists():
        run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", language,
             "-ojf", "-of", str(out_base), "-np", "-ml", "40", "-sow",
             # DTW aligns every token to the audio: word times to ~0.1 s (needs flash attention off)
             "-dtw", dtw, "-nfa", *(["--prompt", prompt] if prompt else [])], timeout=7200)
    data = json.loads(out_json.read_text(errors="replace"))
    segments = []
    for seg in data.get("transcription", []):
        text = seg.get("text", "").strip()
        if not text:
            continue
        probs = [tok.get("p", 0.0) for tok in seg.get("tokens", []) if not tok.get("text", "").startswith("[_")]
        segments.append({
            "start": seg["offsets"]["from"] / 1000,
            "end": seg["offsets"]["to"] / 1000,
            "text": text,
            "conf": round(sum(probs) / len(probs), 2) if probs else None,
            "words": _words(seg.get("tokens", [])),
        })
    return {"language": data.get("result", {}).get("language"), "segments": segments}


def transcribe(wav: Path, out_base: Path, language: str = "auto") -> dict:
    """Speech to timed segments. Kenyan speech (Swahili, Sheng, code-switching) gets "Sheng mode":
    a larger model forced to Swahili with a Sheng vocabulary hint, plus a Swahili-tuned model as a
    second ear, because neither alone gets Sheng right and they err in different places."""
    import sheng  # local: only needed here
    first = _whisper(WHISPER_MODEL, WHISPER_DTW, wav, out_base, "sw" if language == "sw" else language)
    if not (language == "sw" or sheng.sounds_kenyan(first)) or not sheng.available():
        return first
    hint = sheng.hint()
    main = _whisper(sheng.MAIN_MODEL, sheng.MAIN_DTW, wav, out_base.with_name(out_base.name + "_sheng"), "sw", hint)
    second = _whisper(sheng.EAR2_MODEL, sheng.EAR2_DTW, wav, out_base.with_name(out_base.name + "_ear2"), "sw", hint) \
        if sheng.EAR2_MODEL.exists() else {"segments": []}
    main["language"] = "sw (Sheng mode)"
    main["mode"] = "sheng"
    # the second ear, word by word, so it can be lined up with any stretch of the main transcript
    main["second_ear"] = [{"start": w["t"], "end": w["t"], "text": w["w"]}
                          for x in second["segments"] for w in x.get("words", [])]
    main["first_pass"] = first.get("language")
    return main
