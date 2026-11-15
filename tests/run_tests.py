#!/usr/bin/env python3
"""Planted-event tests: build two synthetic clips with known events, scan them, check every
event is found at the right time with the right description, and that nothing extra is claimed.

  python3 tests/run_tests.py            (needs macOS, ffmpeg, whisper-cpp; takes about a minute)
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# tests run against the installed skill (it holds the models); set WATCH_VIDEO_DIR to test another copy
INSTALLED = Path(os.environ.get("WATCH_VIDEO_DIR", Path.home() / ".claude" / "skills" / "watch-video"))
WATCH = INSTALLED / "scripts" / "watch.py"
TOL = 0.6   # seconds

# (time, kinds allowed, words the description must contain)
EXPECT = {
    "test1": {
        "events": [(9.0, {"motion"}, ["moves right", "red"]), (14.0, {"cut"}, []),
                   (17.2, {"motion"}, ["CODE 4471", "FLASH"]), (25.0, {"sound"}, ["sharp"]),
                   (30.0, {"motion"}, ["moves down", "green"]), (35.0, {"sound", "soundtrack"}, [])],
        "speech": ["red ball", "what happened"], "max_events": 9,
    },
    "test2": {
        "events": [(3.0, {"motion"}, ["FLASH", "yellow"]), (6.0, {"motion"}, ["moves left", "blue"]),
                   (10.5, {"cut"}, []), (13.0, {"motion"}, ["Score 7"]), (15.4, {"motion"}, ["Score 8"]),
                   (18.0, {"sound"}, ["2 sharp"]), (21.0, {"motion"}, ["moves up then back"])],
        "speech": ["meeting", "mango"], "max_events": 9,
    },
}


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="watch-video-tests-"))
    failed = 0
    for script in ("make_test.sh", "make_test2.sh"):
        for f in ("textpng.swift", script):
            (work / f).write_bytes((HERE / f).read_bytes())
        subprocess.run(["zsh", str(work / script)], check=True, capture_output=True)
    for name, exp in EXPECT.items():
        out = work / f"out_{name}"
        subprocess.run([sys.executable, str(WATCH), "scan", str(work / f"{name}.mp4"), "--out", str(out)],
                       check=True, capture_output=True)
        st = json.loads((out / "state.json").read_text())
        for t, kinds, words in exp["events"]:
            hit = [e for e in st["events"] if e["kind"] in kinds and abs(e["t"] - t) <= TOL
                   and all(w.lower() in e["detail"].lower() for w in words)]
            ok = bool(hit)
            failed += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {name} {t:5.1f}s {'/'.join(sorted(kinds))} {words}")
        said = " ".join(s["text"] for s in st["transcript"] if s.get("voice", True)).lower()
        for phrase in exp["speech"]:
            ok = phrase in said
            failed += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {name} speech contains '{phrase}'")
        real = [e for e in st["events"] if e["kind"] != "steady"]
        ok = len(real) <= exp["max_events"]
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name} no flood of false events ({len(real)} <= {exp['max_events']})")
        for f in ("BRIEF.md", "glance.jpg", "WATCH.md"):
            ok = (out / f).exists()
            failed += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {name} wrote {f}")
    print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILED'}  (work dir: {work})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
