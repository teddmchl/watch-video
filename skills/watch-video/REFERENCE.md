# watch-video reference

The full manual. SKILL.md is the short version; read this when you need a signal explained, a flag, live mode, people, Sheng mode, or the limits.


# Watch video

You can't take video in directly. This skill gives you the three things a person uses instead:

1. **Peripheral vision** (`scan`): cheap signals over *every* frame (motion, cuts, where on screen
   things change) and every 50 ms of audio (loudness, sudden sounds, sound types, speech).
   Nothing is skipped the way 1 fps sampling skips things.
2. **Attention**: you look closely only where something happened: labelled contact sheets of
   dense frames, a *strobe* image that shows a whole movement in one picture, full-resolution
   zooms, and OCR before and after every change.
3. **Memory**: `WATCH.md` and `state.json` keep a timestamped timeline, so follow-up questions
   don't need another full pass.
4. **People**: who speaks when (voices told apart, turn by turn, each word timed), what each
   voice is like, which face is talking (lips moving), and body language: raised hands, waves,
   pointing, nods, head shakes, standing up, walking, touching the face, crossed arms.

Everything runs on-device (ffmpeg, whisper.cpp, Apple Vision and SoundAnalysis). Nothing is uploaded.

## 1. Scan

```bash
python3 ~/.claude/skills/watch-video/scripts/watch.py scan "<file or URL>"
```

It prints the path to `WATCH.md`. Measured on an M2 MacBook Air: a 40-second 720p clip takes about 80 s the
first time, and a 5-minute 1080p clip with people in it about 7 min. Asking again about a video it has already
scanned reuses the work dir: about 9 s and 1 min for those two. For anything over ~1 minute, run it in the
background. Useful flags:
`--budget N` sets the number of attention windows (default 8; raise it for long or busy videos),
`--lang sw` forces a speech language (default auto), `--no-speech` skips transcription,
`--out DIR` sets the work dir (default `~/.cache/watch-video/<name>-<hash>`; rescans of the same file reuse it),
`--no-people` skips face and body tracking (it only runs when the storyboard shows people anyway).

## Live

```bash
python3 ~/.claude/skills/watch-video/scripts/watch.py live screen|camera|<url|file> [--chunk 10] [--for 600]
```

Run it in the background and follow its output (Monitor the output file, wake on new lines). It
records in chunks, scans each one as it finishes, and prints one line per event with wall-clock
time; `LIVE.md` keeps the log, and each chunk has its own `WATCH.md` for drilling in. Latency is
the chunk length plus ~3 s. Each chunk's log is budgeted like attention: the 5 most important
visual changes, one line for small flickers (typing, cursors), up to 3 new pieces of screen text,
and all speech; the rest is counted and left in that chunk's `WATCH.md`. Screen mode records no
audio; camera mode uses the default microphone. Screen mode needs Screen Recording for Claude in
System Settings. Camera mode can't run inside the Claude app (it can't ask macOS for the camera):
run it from Terminal, which asks once. With no frames it stops after ~25 s and says why. A file is
played at real speed, which is how to test it. Live recordings of the screen can hold private
content: delete the `live-*` folder in `~/.cache/watch-video/` when done.

## 2. Watch, in this order

1. **Read `WATCH.md`.** It gives the facts, the reading order, the event timeline, the transcript and OCR.
2. **Look at `chart.png`.** This is the whole video at a glance: motion, loudness and voice lanes, yellow
   lines at cuts, pink markers at the events you're about to look at. Note the busy and quiet parts.
3. **Look at `storyboard.jpg`** (16 evenly spaced frames) and **`shots.jpg`** (one frame per shot) for
   the setting, the people and the look.
4. **Look at every attention window** `attend/W*.jpg`, in time order. Each tile is labelled
   `MM:SS.ss`. Where there's a strobe image (`W*_strobe.jpg`), look at it as well: the background is the
   still scene, every moving thing is painted at each moment, **faint = earlier, solid = later**,
   so you can read path, direction and speed from one image.
5. **Build your account, then check it.** For every claim you are about to make, ask: did I actually
   see this, or am I inferring between frames? If you're inferring, go back and look:
   - `watch.py look DIR --start S --end E [--n 16] [--strobe]` gives dense frames for any range.
     For fast action keep the spacing ≤ 0.1 s, so `--n` ≈ 10 × seconds.
   - `watch.py zoom DIR --t T [--box x,y,w,h] [--stack 15]` crops at full resolution (box is
     normalized, top-left origin). Without `--box` it zooms on whatever moved at T. It also runs OCR.
     `--stack N` aligns and averages N neighbouring frames (shift-and-add): use it for small text or
     numbers in low-resolution video. In tests it turned a misread `73 | 12` into the correct `7392`.
   - `watch.py frame DIR --t T` gives one full-resolution frame.
   - `watch.py listen DIR --start S --end E` lists speech (with each word's time and speaker), sound
     types, loudness and sound events in that range.
6. **Answer with timestamps** and say what your answer rests on: which windows you looked at, and
   which parts you only know from signals.

`DIR` is the folder that holds `WATCH.md`.

## Reading the signals

Motion is measured *after undoing camera movement*, so a pan doesn't hide a ball flying through the
shot. Event kinds:

- **motion** says how big the change is, where on screen, and its path: `moves left`,
  `moves up then back` (a hop or a wave), or `in place` (something changed without travelling).
  It also says `FAST` (in screen-widths per second along the path) and `instant (one-frame change)`. Extra
  details come from looking just before and just after the change:
  - `colour now X (was Y)`
  - `text now: …` / `text gone: …` (OCR)
  - `FLASH: a yellow thing appeared, stayed 0.27s, then vanished`, which is only claimed when the spot
    really went back to how it was. A change like `Score 7` → `Score 8` is a change of state, not a flash.
- **steady**: ongoing background movement (a drift, someone walking, a fan) over several seconds. It is
  context, not an event. Sudden things inside it are still listed as separate motion events.
- **camera**: `pans left/right`, `tilts up/down`, or `shakes`, with the speed. A pan that follows
  something usually means that thing matters (a ball, a runner).
- **cut**: a hard cut, detected from colour, so a cut between two equally bright scenes still counts.
  Slow fades and dissolves are *not* flagged; look at the storyboard for those.
- **sound**: sudden sounds outside speech, grouped. Examples: `2 sharp sounds in a row (gaps 0.25s)`
  (knock, clap, footsteps), or `sustained` (an alarm, a horn, notes of a tune).
- **loud**: the loudest stretches compared with this video's own typical level (a crowd roar, shouting,
  a music swell). These are skipped for mostly silent videos.
- **sound-type** / **soundtrack** come from Apple's classifier (about 300 classes). `soundtrack` is
  merged music, with its most common labels. Sound-type labels over near-silence, and weak labels
  under a voice, are already dropped. Treat what's left as hints and confirm them against the frames.
- **Speech** lines marked `NO VOICE HEARD HERE` had no voice under them. Whisper sometimes writes
  fluent sentences over music or silence. Don't quote those lines as speech.
- **Text on screen the whole time** lists titles, handles and watermarks once. **Text that comes and goes** gives the time each
  line was first seen. Numbers are compared exactly (`7` ≠ `8`), and low-confidence OCR guesses are dropped,
  so use `zoom` on stylised or tiny text.
- **Scene labels** (Apple Vision) are rough. Trust your own eyes on the storyboard over them.

Strobe images number each moment at the centre of what changed (1 = first). Read the numbers to
follow a path that reverses (up then down), which faint-to-solid alone can't show. Strobes are clipped
to a single shot and are only made automatically when the camera is still. During a pan the background
smears, so use dense `look` sheets there.

## Kenyan speech (Sheng, Swahili, English mixed)

If the first pass hears Swahili, or 15%+ of its words are Sheng/Swahili, the scan switches to
**Sheng mode**: whisper large-v3-turbo forced to Swahili with a Sheng hint (real Sheng phrases), plus
a Swahili-tuned small model as a **second ear**. `--lang sw` forces it. In `WATCH.md` each speech line
has an `ear 2:` line under it. The two models make different mistakes (one glues words, the other
mishears Sheng as Swahili), so **reconstruct what was said from both**, as a Sheng speaker would write it,
and name the words you're unsure of. On a read Sheng script: one model alone got ~23–34% of characters
wrong; reconstructing from both ears got 9.5%. Never quote a raw line as what someone said.
Turbo on auto-language can loop an invented English sentence over Sheng; that's why it's forced to Swahili.

## People, voices and body language

- **Speech by turn**: speakers `S1, S2 …` are separated by a speaker-recognition model (WeSpeaker,
  on-device), learned from the longer stretches and checked in 1.5 s windows, so a hand-over with no
  pause still splits. When faces are tracked, `[F2]` means face F2's lips were moving during that
  turn; `off-screen` means faces were visible but none was talking (a narrator, someone out of shot).
- **crosstalk** events / `(talking over S2)`: two people at once, found where the word aligner
  piles several words onto one instant right at a change of speaker. Words there may be garbled:
  `listen` to the range before quoting them.
- **Faces up close**: `faces.jpg` shows each tracked face over time. Read expressions there with
  your own eyes (smiling, frowning, rolling eyes, surprise); that beats any landmark rule.
  **face** events (`smiles`, `raises their eyebrows`, `closes their eyes`) are cues measured
  against that person's resting face, only for faces big enough to measure.
- Face and body IDs restart after every cut. Faces that talk with the same voice are probably the
  same person.
- **Voices** are *described* (pitch band, intonation, pace), never identified. Don't infer name,
  gender, age or ethnicity from a voice or a face.
- **body** events: `P1 (left of frame) raises their right hand`, `waves`, `points to the left of frame`,
  `stands up`, `walks to the right`, `touches their face`, `crosses their arms`, `F1 nods`,
  `shakes their head`. Sides are the person's own. Only judged for bodies big enough (neck to hip
  ≥15% of frame height), and pointing only when facing the camera.
- Close-ups and webcams show no hips, so gestures come from **hands judged against the face**:
  `raises a hand`, `waves`, `touches their face` (down to the chin), `raises a hand above their
  head (pointing or reaching up?)`. Only when a face is in shot. Talking with the hands, a thumbs
  up, a tongue out or a peace sign are not detected: look at `faces.jpg` and the storyboard. Head gestures need ≥5 fps tracking (clips up to 3 min); longer
  videos track at 3 or 1 fps, so check with `look` before claiming a nod.
- These are cues, not meanings: "raised a hand" is seen; "wanted to ask a question" is inferred.

## Tips from blind tests

- The key moment is often where the **camera pans** (it is following something) or where speech suddenly
  turns exclamatory. Make sure you've looked densely there, even if no attention window covers it.
- When a small object matters (a ball, a cursor, a price), `zoom` on 3–5 times about 0.1 s apart. A
  low-resolution source (360p) may simply not show it; say so rather than guess.
- Words are timed by alignment to about ±0.2 s (`listen` shows them). That's good enough to say a
  word came before or after a gesture, not to put it on an exact frame.
- Mark each claim as **seen**, **heard**, **read** (OCR) or **inferred**. That keeps the answer honest
  about the gaps between the frames you looked at.

## Honest limits (say them when they matter)

- You see stills and signals, not continuous motion. Strobes, dense sheets and pose tracks close
  most of the gap for *what moved where* and the big gestures. Facial expressions (a smirk, an
  eye-roll) and fine hand movements are not tracked; look at them densely.
- `live` is near-real-time (chunk length + a few seconds), not instant, and you are only told
  about a chunk after it ends.
- Crosstalk is flagged but not untangled: during it, words may go to the wrong speaker or be lost.
  Two very similar voices (synthetic ones especially) may merge. Speaker labels are per video.
- Stacked zoom recovers about one step of resolution. Text a couple of pixels high is gone for good.
- Very long videos (over 1 hour) are scanned at 4–8 fps and get a coarse storyboard. Drill in with `look`.
- Song lyrics and heavy crosstalk transcribe poorly. Sheng needs the two-ear reconstruction above,
  and very new or very local Sheng words may still be misheard.
- Camera shake can hide small local events. Check busy stretches with `look`.

## Known system quirk

On some Macs, Apple's accurate text model fails to load from its cache for a while (`e5rt` errors, often
right after the helper is rebuilt). The helper then reads text with the fast model for 30 minutes, which
is a little weaker on small or stylised text. `VSENSE_OCR=fast` forces it.

## Requirements

`ffmpeg`, `whisper-cli` (Homebrew `whisper-cpp`), and the model at `models/ggml-small.bin`.
`yt-dlp` is needed for links. Sheng mode uses `models/ggml-large-v3-turbo-q5_0.bin`,
`models/ggml-swahili-small-q8_0.bin` and `data/sheng/` (rebuild with `scripts/build_sheng.py`). Speaker recognition uses `.venv` (sherpa-onnx, numpy, soundfile) and
`models/wespeaker_en_voxceleb_resnet34_LM.onnx`; without them it falls back to pitch and timbre. `bin/vsense` (Swift, Apple frameworks) rebuilds itself from
`scripts/vsense/main.swift` if missing. Work dirs in `~/.cache/watch-video/` hold frames and
audio; delete a video's folder when you're done if it's large or private.
