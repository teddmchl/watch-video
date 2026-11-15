# watch-video

A skill that lets Claude Code watch a video the way a person does, for about the cost of reading a page.

Claude can't take video in directly. This skill does the watching on your Mac and hands Claude a
one-page brief plus images it opens only when a question needs eyes. Nothing is uploaded.

```
You:    watch ~/Downloads/demo.mp4 — what happens at the end?
Claude: (scans it in ~10 s, reads a 600-token brief, opens one image, answers with timestamps)
```

## What it does

- **Misses nothing brief.** Every frame is checked for change (camera movement cancelled out) and audio
  every 50 ms. A 0.3-second flash, a score going from 7 to 8, a knock off-screen: all caught and timed.
  Sampling one frame per second, the usual approach, skips these.
- **Hears.** Speech with each word timed, speakers told apart, sounds classified, invented transcript
  lines flagged (speech models make up sentences over music; this checks for an actual voice).
- **Reads.** On-screen text, when it appears and disappears. Multi-frame zoom for small text.
- **Sees people.** Who is talking (lips moving), raised hands, waves, pointing, nods, face close-ups for
  expressions. It describes people; it never identifies them.
- **Watches live.** Your screen, camera, or a stream, in 10-second chunks.
- **Kenyan speech.** Sheng, Swahili and English mixed, using two models and a Sheng word list.
- **Stays cheap.** A 40-second clip costs about 600 tokens; a 5-minute interview about 2,300.

## Install (macOS, Apple silicon or Intel)

```bash
git clone https://github.com/teddmchl/watch-video && cd watch-video && ./install.sh
```

Needs [Homebrew](https://brew.sh), Xcode command line tools and [Claude Code](https://claude.com/claude-code).
The base install downloads one 488 MB speech model. Options: `--speakers` (tell real voices apart,
~250 MB), `--sheng` (Kenyan speech, ~840 MB), `--all`.

Then ask Claude Code to watch any video file or link. Or run it yourself:

```bash
python3 ~/.claude/skills/watch-video/scripts/watch.py scan "<file or link>"
```

## How it works

| Layer | What a person does | What the skill does |
|---|---|---|
| Peripheral vision | Notices any change, cheaply | Motion, cuts and audio signals over every frame |
| Attention | Looks closely where something happened | Labelled frame sheets, motion-trail images, zooms, text read before and after each change |
| Memory | Remembers the story | `BRIEF.md` (one page), `WATCH.md` (everything), reusable for follow-up questions |

Seeing and hearing use Apple's built-in Vision and SoundAnalysis frameworks through a small Swift
helper; speech uses [whisper.cpp](https://github.com/ggml-org/whisper.cpp); media handling uses ffmpeg.

## Tested how

`python3 tests/run_tests.py` builds two clips with planted events (a 0.25 s flash, a silent fall, a
sound with no picture, a number changing) and checks each is found at the right time with nothing
invented. Beyond that: a real 5-minute interview (three voices separated correctly), live screen and
camera sessions, and blind tests where a fresh Claude answers from the skill's output alone.
The numbers are from one Mac and a handful of clips. More clips, more languages and harder footage
are exactly where help is wanted.

## Limits

- macOS only for now (the perception layer is Apple's).
- Claude still sees stills and signals, not motion. Fine hand movements and subtle expressions can be missed.
- Live mode runs about a chunk behind, and long sessions fall further behind.
- Overlapping speech is flagged, not untangled.
- Text a few pixels high is gone for good.

## Help wanted

See [CONTRIBUTING.md](CONTRIBUTING.md). The biggest open pieces: Linux and Windows support, test clips
with known answers, more languages, faster live mode, and a version that works in Claude apps beyond
Claude Code.

## Why this exists

[docs/native-video-proposal.md](docs/native-video-proposal.md) lays out what native video
understanding in Claude would take, with this skill as the working prototype. It's an outside
proposal, not affiliated with Anthropic.

MIT licensed. Built by [Tedd Onyesero](https://github.com/teddmchl) in Nairobi.
