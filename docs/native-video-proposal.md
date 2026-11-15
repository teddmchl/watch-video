# Claude Video: watching like a person

*A feature proposal written from the outside, as if for a future Claude release. The `watch-video` skill in this repo is the working prototype and the evidence. Nothing here reflects Anthropic's plans.*

## The ask

Claude should watch a video the way a person does. It should catch what happens, when it happens,
who is doing it, what they say and how they say it, and catch the brief things too: a 0.3 s flash, a
score going from 7 to 8, a nod. It should do this on recordings and live. It should know where it
is unsure.

"Watch like a person" means five capabilities. Today's Claude has none of them natively.

| # | Capability | Person | Gemini today (public docs) | Claude today | Prototype skill |
|---|---|---|---|---|---|
| 1 | Doesn't miss brief events | Peripheral vision catches any change | 1 frame/s by default, so a 0.3 s event is often skipped | Only the images it is given | Every frame scanned for change; 9/9 planted events found |
| 2 | Continuous motion | Sees a path, not stills | 1 fps stills | Stills | Strobe images and pose tracks; still stills underneath |
| 3 | Sound tied to picture | Hears and sees together | 1 kbps audio track, timestamps | None | Speech, 300 sound classes and loudness every 50 ms, on one clock |
| 4 | People | Who talks, tone, body language | Partial | None | Speakers by voice (6/6 synthetic turns, 3/3 voices in a real interview), lips, gestures |
| 5 | Real time | Watches as it happens | Live API (streaming) | None | 10 s chunks, about 3 s behind |

The prototype shows the *behaviour* can be had with today's Claude plus perception tools. The
feature is making it native: cheaper, faster, subtler, and available on every surface.

## Why it can't just be "more frames"

The bottleneck is tokens, not seeing.

- One frame costs roughly 70–280 tokens in today's multimodal models, depending on resolution.
- At 1 fps, an hour of video is about 250k–1M tokens, which is why every vendor samples sparsely.
- At the 15 fps the prototype scans at, the same hour would be 4–15M tokens. That isn't possible in context.

A person doesn't attend to every frame either. Peripheral vision is cheap and covers everything.
Attention is expensive and goes only where something changed. **The design principle is to make
sampling adaptive, driven by the content, instead of fixed-rate.** The prototype already does this
outside the model (cheap signals over every frame, then dense frames only in attention windows).
The native version moves it inside.

## What it would take

### 1. A video encoder that compresses time (model)

- **Spatiotemporal tokens.** Encode short clips (tubelets of a few frames) rather than single
  frames, so motion is *in* the token, not inferred between stills. This fixes capability 2.
- **Change-aware budget.** A cheap always-on pass over every frame (motion, cuts, saliency) that
  decides how many tokens each second gets: near zero for a static slide, dense for a goal or a
  gesture. The prototype's signal layer is a hand-built version of this. Train it end to end.
- **Re-attention as a tool.** The model can ask for "frames 41.2–42.0 s at full resolution" mid-answer,
  like the skill's `look`/`zoom`. Gemini's agentic video mode is moving the same way.
- **Multi-frame detail.** Fusing neighbouring frames for small text (shift-and-add worked in the
  prototype: a misread `73 | 12` became `7392`). A learned version would do much better.

### 2. Native audio, on the same clock (model)

- An audio encoder that hears the waveform, not a transcript. That keeps tone, sarcasm, laughter,
  hesitation, music and environmental sound.
- **Shared timestamps** across audio and video tokens, so "the crowd roared *as* the ball crossed the
  line" is a single perception, not a cross-reference of two lists.
- **Speaker attribution** inside the model: voice, plus which face's lips move. The prototype does this
  with pitch, timbre, lip-movement tracking and pauses. It merges overlapping speech, and a native
  model wouldn't have to.

### 3. Training data and objectives (research)

- **Dense, timed supervision**: captions with event timestamps, speaker-attributed transcripts,
  gesture labels, on-screen text with appear and disappear times.
- **Synthetic planted-event video.** This is the prototype's main testing trick, and it scales: render
  clips with known events (a 0.25 s flash, a number change, a sound with no picture) and train on
  exact recall and timing. It's cheap, unlimited and has perfect labels.
- **Abstention training**: reward "I can't see the ball cross the line at 360p" over a confident guess.
  In the blind tests, the best behaviour came from the agent saying what it hadn't verified.

### 4. Streaming inference (infra)

- A persistent session with incremental encoding and a rolling cache, not re-reading the whole video.
- **Event-triggered speech**: the model stays silent until something worth saying happens ("they've
  stopped sharing the screen", "the error dialog appeared"). The prototype gets this with chunked
  scans and a monitor that wakes Claude on new lines.
- Budget: latency under 1 s for live screen help; the prototype is at chunk length + ~3 s.

### 5. Evals (how we'd know it works)

| Eval | Measures | Prototype baseline |
|---|---|---|
| Planted-event recall | % of brief events found, timing error | 9/9, ±0.1 s (synthetic) |
| Temporal grounding | Does "when does X happen" land on X | Correct on real football clip |
| Speaker attribution | Diarization error on multi-speaker clips | 6/6 turns, 3 synthetic voices |
| Change-of-state vs flash | 7→8 is a change, not a flash | Correct after fix |
| Hallucination | Claims not supported by any frame or audio | Speech-model inventions flagged; alignment removed one outright |
| Live latency | Event to notification | ~13 s worst case at 10 s chunks |

### 6. Safety and policy (non-negotiable)

- **No identification.** Describe people and voices; never identify faces or voices biometrically
  and never infer gender, age or ethnicity from them. The prototype enforces this in its output.
- **Live consent.** Camera and screen capture are opt-in per session, visibly on, and never
  background-recorded without the user knowing.
- **Surveillance misuse.** Refuse continuous tracking of specific people across footage.
- **Content.** The same harmful-content rules as images, applied per segment; copyrighted video
  is summarised, not reproduced.
- **Overclaiming.** Every answer distinguishes seen, heard, read and inferred.

## Phasing

| Phase | What ships | Depends on | Rough effort |
|---|---|---|---|
| 0: Harness | Server-side version of this skill as a built-in tool (signals, attention, people, live chunks) | Nothing new in the model | Weeks; the prototype is the spec |
| 1: Native offline | Video and audio tokens in the API, fixed sampling, shared clock | Encoder training | A model cycle |
| 2: Adaptive | Change-aware token budget, re-attention tool, multi-frame detail | Phase 1 + learned sampler | A model cycle |
| 3: Live | Streaming sessions, event-triggered speech | Phase 2 + streaming infra | Infra + product |

Phase 0 is the honest near-term answer. Today's Claude watching through a good perception
harness already beats 1 fps sampling on brief events, because it never skips frames.

## What the prototype can't do, and which phase fixes it

| Limit today | Fixed by |
|---|---|
| Sees stills and signals, not motion | Phase 1 tubelet tokens |
| Facial expressions, fine hand movements | Phase 1–2 (dense attention on faces and hands) |
| Overlapping speakers merge | Phase 1 native audio |
| Not instant: chunk + ~3 s | Phase 3 streaming |
| Tiny text below ~5 px | Phase 2 learned multi-frame detail (some is unrecoverable at any phase) |

## Verified vs assumed

- **Verified on the author's Mac:** every prototype number above (synthetic fixtures with known ground truth, one
  real football clip, a 10-minute performance test, a 3-voice conversation, a low-resolution text sweep).
- **Verified on real footage** (a CC BY 3.0 Royal Society interview, 5 min, 70 cuts): three voices
  separated correctly with an on-device speaker model; expression cues checked by eye. The first run
  produced false gestures (pointing seen side-on, tiny distant figures, tracks crossing cuts), so those
  rules were tightened. Crosstalk is detected on synthetic overlaps but not yet on real ones.
- **Assumed from public information:** Gemini's sampling defaults and token costs (Google's docs).
  Nothing here reflects Anthropic's internal plans. This is an outside-in proposal.
