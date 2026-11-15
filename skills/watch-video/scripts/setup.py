"""First-run setup: check tools, download models (with checksums), build the helper.

  watch.py setup               base: one 488 MB speech model
  watch.py setup --speakers    + speaker model and a small Python env (~250 MB)
  watch.py setup --sheng       + two models for Kenyan speech (~840 MB)
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import media
from media import MediaError

HF = "https://huggingface.co"
BASE = [("ggml-small.bin", f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
         "55356645c2b361a969dfd0ef2c5a50d530afd8d5")]
SPEAKERS = [("wespeaker_en_voxceleb_resnet34_LM.onnx",
             "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
             "wespeaker_en_voxceleb_resnet34_LM.onnx", "32177cb72e8d3367ab3c9d5fba48ea1ed6a1d171")]
SHENG = [("ggml-large-v3-turbo-q5_0.bin", f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin", None),
         ("ggml-swahili-small-q8_0.bin", f"{HF}/malinali-app/whisper-swahili-small-ggml/resolve/main/ggml-model-q8_0.bin", None)]


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(name: str, url: str, digest: str | None) -> None:
    dest = media.HOME / "models" / name
    if not dest.exists():
        print(f"downloading {name} ...", flush=True)
        part = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "watch-video"})) as r, part.open("wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        part.rename(dest)
    if digest and sha1(dest) != digest:
        raise MediaError(f"checksum mismatch for {name}; delete {dest} and run setup again")


def setup(speakers: bool, sheng: bool) -> None:
    if sys.platform != "darwin":
        raise MediaError("watch-video needs macOS (it uses Apple's Vision and SoundAnalysis)")
    missing = [t for t, _ in (("ffmpeg", 0), ("whisper-cli", 0), ("swiftc", 0)) if not shutil.which(t)]
    if missing:
        raise MediaError("missing tools: " + ", ".join(missing) + ". Install with: brew install ffmpeg whisper-cpp yt-dlp"
                         " (and xcode-select --install for swiftc)")
    (media.HOME / "models").mkdir(parents=True, exist_ok=True)
    for item in BASE + (SPEAKERS if speakers else []) + (SHENG if sheng else []):
        fetch(*item)
    if speakers and not (media.HOME / ".venv").exists():
        subprocess.run([sys.executable, "-m", "venv", str(media.HOME / ".venv")], check=True)
        subprocess.run([str(media.HOME / ".venv" / "bin" / "pip"), "install", "-q", "sherpa-onnx", "numpy", "soundfile"], check=True)
    media.ensure_vsense()
    print(f"ready. models and helper in {media.HOME}")
