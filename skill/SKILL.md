---
name: watch-video
description: Watch a video file or link the way a person does. Every frame is scanned for motion, cuts and on-screen text, every 50 ms of audio for speech and sounds, then attention goes to the moments that matter (contact sheets, motion-trail images, zooms). Use whenever the user shares a video (mp4, mov, webm, screen recording, YouTube/X/TikTok/Instagram link) or asks what happens in one, to find a moment, summarise a recording, review a demo or bug video, check a meeting clip, or anything else that needs seeing video. Runs fully on this Mac.
---

# Watch video

You can't take video in directly. `scan` watches for you: every frame for motion and cuts, every
50 ms of audio, speech with speakers, text on screen, people. Everything runs on this Mac.

```bash
python3 ~/.claude/skills/watch-video/scripts/watch.py scan "<file or URL>"
```

It prints two paths. **Read `BRIEF.md` first and usually only that**: about a page with the key
moments, what was said and by whom, text on screen, and people. Most questions (what happens, what did
they say, when does X happen, summarise this) are answered from it for roughly the cost of a PDF page.

Spend more only when the question needs it:

| Need | Open | Cost |
|---|---|---|
| What it looks like, who or what is in it | `glance.jpg` | ~400 tokens |
| What happened at one moment | that moment's sheet, the `→` path in the brief | ~1,400 each |
| Expressions | `faces.jpg` | ~1,500 |
| Exact wording, word times, every event | `WATCH.md` | text, grows with length |
| A moment the scan didn't cover | `watch.py look DIR --start S --end E`, `zoom DIR --t T [--stack 15]`, `listen DIR --start S --end E` | one image or a few lines |

`DIR` is the folder holding `BRIEF.md`. Rescans of the same file reuse it.

Rules that keep answers honest:
- Give timestamps. Mark claims as seen (you opened an image), heard, read (OCR) or inferred. If you
  answered from the brief alone, say you watched through signals and didn't look at frames.
- Before stating something visual that the brief only implies (a colour, who did it, an expression),
  open the one image that shows it.
- Speech lines flagged `NO VOICE HEARD` are the speech model inventing. Don't quote them.
- Kenyan speech (Sheng/Swahili mix) is transcribed by two models; reconstruct from both lines in
  `WATCH.md` before quoting.
- Describe people and voices; never identify them or infer gender, age or ethnicity.
- Low-resolution or tiny detail may not be recoverable. Say so instead of guessing.

Flags: `--budget N` attention windows (default 8), `--lang sw`, `--no-speech`, `--no-people`.
Live: `watch.py live screen|camera|<url|file> [--for SECONDS]`.
Everything else (signal meanings, people and gestures, Sheng mode, live mode, limits, requirements)
is in `REFERENCE.md` next to this file. Read it when you need it, not by default.
