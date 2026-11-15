#!/bin/zsh
# Installs the watch-video skill for Claude Code on macOS.
#   ./install.sh               base: watch, listen, read the screen, people, live   (~500 MB)
#   ./install.sh --speakers    + tell real voices apart (speaker model, ~250 MB)
#   ./install.sh --sheng       + Kenyan speech: Sheng/Swahili/English mix (~840 MB)
#   ./install.sh --all
set -euo pipefail
cd "$(dirname "$0")"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/watch-video"
SPEAKERS=0; SHENG=0
for a in "$@"; do case "$a" in
  --speakers) SPEAKERS=1;; --sheng) SHENG=1;; --all) SPEAKERS=1; SHENG=1;;
  *) echo "unknown option $a"; exit 2;; esac; done

[[ "$(uname)" == "Darwin" ]] || { echo "watch-video needs macOS (it uses Apple's Vision and SoundAnalysis)."; exit 1; }
command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
command -v swiftc >/dev/null || { echo "Swift is required: run  xcode-select --install"; exit 1; }
for pkg in ffmpeg whisper-cpp yt-dlp; do brew list "$pkg" >/dev/null 2>&1 || brew install "$pkg"; done

mkdir -p "$DEST/models"
cp -R skills/watch-video/SKILL.md skills/watch-video/REFERENCE.md skills/watch-video/scripts skills/watch-video/data "$DEST/"
ARGS=()
(( SPEAKERS )) && ARGS+=(--speakers)
(( SHENG )) && ARGS+=(--sheng)
python3 "$DEST/scripts/watch.py" setup "${ARGS[@]}"
echo "installed to $DEST"
echo "try it: ask Claude Code to watch any video file or link."
