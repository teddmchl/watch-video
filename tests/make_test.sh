#!/bin/zsh
# Builds test1.mp4: a 40 s video with known events (ground truth in truth1.json).
set -euo pipefail
cd "$(dirname "$0")"
swift textpng.swift "CODE 4471" code.png 22
say -v Samantha -o s1.aiff "Welcome to the test. Watch the red ball."
say -v Samantha -o s2.aiff "Did you see what happened?"

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "color=c=0x2a3a2a:s=1280x720:r=30:d=14" \
  -f lavfi -i "color=c=0x1a2a5a:s=1280x720:r=30:d=26" \
  -f lavfi -i "color=c=red:s=60x60:r=30:d=40" \
  -f lavfi -i "color=c=0x999999:s=80x80:r=30:d=40" \
  -f lavfi -i "color=c=0x22cc44:s=70x70:r=30:d=40" \
  -loop 1 -framerate 30 -t 40 -i code.png \
  -i s1.aiff -i s2.aiff \
  -f lavfi -i "anoisesrc=d=0.25:c=white:a=0.9,afade=t=out:st=0:d=0.25" \
  -f lavfi -i "aevalsrc='0.35*sin(2*PI*(440+220*floor(mod(t*4,4)))*t)':d=3:s=44100" \
  -f lavfi -i "anoisesrc=d=40:c=pink:a=0.004" \
  -filter_complex "
    [0:v][1:v]concat=n=2:v=1:a=0[bg];
    [bg][3:v]overlay=x='100+20*t':y=200:enable='lt(t,14)'[v1];
    [v1][2:v]overlay=x='(t-9)/0.4*1340-60':y=330:enable='between(t,9,9.4)'[v2];
    [v2][5:v]overlay=x=1110:y=670:enable='between(t,17.2,17.5)'[v3];
    [v3][4:v]overlay=x=600:y='min(t-30,1)*620':enable='gte(t,30)'[v];
    [6:a]adelay=2000|2000[a1];
    [7:a]adelay=20000|20000[a2];
    [8:a]adelay=25000|25000[a3];
    [9:a]adelay=35000|35000[a4];
    [10:a][a1][a2][a3][a4]amix=inputs=5:normalize=0,aformat=channel_layouts=mono[a]" \
  -map "[v]" -map "[a]" -t 40 -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k test1.mp4

cat > truth1.json <<'EOF'
{
  "video": "test1.mp4",
  "events": [
    {"t": 2.0,  "kind": "speech", "what": "Woman says: Welcome to the test. Watch the red ball."},
    {"t": 0.0,  "kind": "scene", "what": "Scene A, 0-14 s: dark green background, a grey square drifts slowly right"},
    {"t": 9.0,  "kind": "fast-motion", "what": "Red ball streaks left to right across the middle of the screen in 0.4 s (9.0-9.4 s)"},
    {"t": 14.0, "kind": "cut", "what": "Hard cut to a dark blue background (scene B); grey square gone"},
    {"t": 17.2, "kind": "brief-small-text", "what": "Small white text 'CODE 4471' flashes bottom-right for 0.3 s (17.2-17.5 s)"},
    {"t": 20.0, "kind": "speech", "what": "Woman says: Did you see what happened?"},
    {"t": 25.0, "kind": "audio-only", "what": "Loud bang/noise burst; nothing changes on screen"},
    {"t": 30.0, "kind": "silent-visual", "what": "Green square appears at top centre and falls to the bottom in 1 s, then stays; no sound"},
    {"t": 35.0, "kind": "music", "what": "Short beeping melody (tones), 35-38 s"}
  ]
}
EOF
echo built: $(pwd)/test1.mp4
