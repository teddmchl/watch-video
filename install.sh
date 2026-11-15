#!/bin/zsh
# Installs the watch-video skill for Claude Code on macOS.
#   ./install.sh               base: watch, listen, read the screen, people, live   (~500 MB)
#   ./install.sh --speakers    + tell real voices apart (speaker model, ~250 MB)
#   ./install.sh --sheng       + Kenyan speech: Sheng/Swahili/English mix (~840 MB)
#   ./install.sh --all
set -euo pipefail
cd "$(dirname "$0")"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/watch-video"
HF="https://huggingface.co"
SPEAKERS=0; SHENG=0
for a in "$@"; do case "$a" in
  --speakers) SPEAKERS=1;; --sheng) SHENG=1;; --all) SPEAKERS=1; SHENG=1;;
  *) echo "unknown option $a"; exit 2;; esac; done

[[ "$(uname)" == "Darwin" ]] || { echo "watch-video needs macOS (it uses Apple's Vision and SoundAnalysis)."; exit 1; }
command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
command -v swiftc >/dev/null || { echo "Swift is required: run  xcode-select --install"; exit 1; }
for pkg in ffmpeg whisper-cpp yt-dlp; do brew list "$pkg" >/dev/null 2>&1 || brew install "$pkg"; done

mkdir -p "$DEST/models" "$DEST/bin"
cp -R skill/SKILL.md skill/REFERENCE.md skill/scripts skill/data "$DEST/"

fetch() {   # fetch <file> <url> <sha1>
  local f="$DEST/models/$1"
  if [[ ! -f "$f" ]]; then echo "downloading $1 ..."; curl -L --fail --progress-bar -o "$f.part" "$2" && mv "$f.part" "$f"; fi
  [[ -z "${3:-}" ]] || [[ "$(shasum "$f" | cut -d' ' -f1)" == "$3" ]] || { echo "checksum mismatch for $1"; exit 1; }
}
fetch ggml-small.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-small.bin" 55356645c2b361a969dfd0ef2c5a50d530afd8d5
if (( SPEAKERS )); then
  fetch wespeaker_en_voxceleb_resnet34_LM.onnx \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx" \
    32177cb72e8d3367ab3c9d5fba48ea1ed6a1d171
  [[ -d "$DEST/.venv" ]] || python3 -m venv "$DEST/.venv"
  "$DEST/.venv/bin/pip" install -q sherpa-onnx numpy soundfile
fi
if (( SHENG )); then
  fetch ggml-large-v3-turbo-q5_0.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
  fetch ggml-swahili-small-q8_0.bin "$HF/malinali-app/whisper-swahili-small-ggml/resolve/main/ggml-model-q8_0.bin"
fi

echo "building the perception helper ..."
swiftc -O -swift-version 5 "$DEST"/scripts/vsense/*.swift -o "$DEST/bin/vsense"
echo "installed to $DEST"
echo "try it: ask Claude Code to watch any video file or link."
