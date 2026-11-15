#!/bin/zsh
# Builds test2.mp4 (30 s) for a blind test. Ground truth: truth2.json (never shown to the watcher).
set -euo pipefail
cd "$(dirname "$0")"
swift textpng.swift "Score 7" score7.png 30
swift textpng.swift "Score 8" score8.png 30
say -v Daniel -o t2a.aiff "The meeting starts at nine thirty."
say -v Daniel -o t2b.aiff "Please remember the code word: mango."

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "color=c=0x404040:s=1280x720:r=30:d=10.5" \
  -f lavfi -i "color=c=0xd0d0d0:s=1280x720:r=30:d=19.5" \
  -f lavfi -i "color=c=yellow:s=90x90:r=30:d=30" \
  -f lavfi -i "color=c=0x2255ff:s=200x60:r=30:d=30" \
  -f lavfi -i "color=c=0x8833aa:s=80x80:r=30:d=30" \
  -loop 1 -framerate 30 -t 30 -i score7.png \
  -loop 1 -framerate 30 -t 30 -i score8.png \
  -i t2a.aiff -i t2b.aiff \
  -f lavfi -i "anoisesrc=d=0.08:c=brown:a=0.9,afade=t=out:st=0:d=0.08" \
  -f lavfi -i "anoisesrc=d=0.08:c=brown:a=0.9,afade=t=out:st=0:d=0.08" \
  -f lavfi -i "anoisesrc=d=30:c=pink:a=0.003" \
  -filter_complex "
    [0:v][1:v]concat=n=2:v=1:a=0[bg];
    [bg][2:v]overlay=x=60:y=50:enable='between(t,3.0,3.25)'[v1];
    [v1][3:v]overlay=x='1200-(t-6)/2*1260':y=600:enable='between(t,6,8)'[v2];
    [v2][5:v]overlay=x=1080:y=30:enable='between(t,13,15.4)'[v3];
    [v3][6:v]overlay=x=1080:y=30:enable='gt(t,15.4)'[v4];
    [v4][4:v]overlay=x=150:y='400-300*sin(PI*(t-21)/0.6)':enable='between(t,21,21.6)'[v];
    [7:a]adelay=12000|12000[a1];
    [8:a]adelay=25000|25000[a2];
    [9:a]adelay=18000|18000[k1];
    [10:a]adelay=18250|18250[k2];
    [11:a][a1][a2][k1][k2]amix=inputs=5:normalize=0,aformat=channel_layouts=mono[a]" \
  -map "[v]" -map "[a]" -t 30 -c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k test2.mp4

cat > truth2.json <<'EOF'
{
  "video": "test2.mp4",
  "events": [
    {"id": 1, "t": 3.0,  "what": "Yellow square flashes at top-left for 0.25 s (3.00-3.25)"},
    {"id": 2, "t": 6.0,  "what": "Blue bar slides right to left along the bottom over 2 s (6-8 s)"},
    {"id": 3, "t": 10.5, "what": "Hard cut from dark grey to light grey background"},
    {"id": 4, "t": 12.0, "what": "Man says: The meeting starts at nine thirty."},
    {"id": 5, "t": 13.0, "what": "'Score 7' appears top-right at 13.0"},
    {"id": 6, "t": 15.4, "what": "Score changes from 7 to 8 at 15.4 and stays"},
    {"id": 7, "t": 18.0, "what": "Two short knocks/thuds at 18.0 and 18.25, nothing changes on screen"},
    {"id": 8, "t": 21.0, "what": "Purple square at left jumps up and comes back down in 0.6 s (21.0-21.6), then disappears"},
    {"id": 9, "t": 25.0, "what": "Man says: Please remember the code word: mango."}
  ]
}
EOF
echo built: $(pwd)/test2.mp4
