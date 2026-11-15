# Contributing

Thanks for helping Claude watch videos. Small, tested changes are the easiest to merge.

## Good first contributions

- **A test clip with known answers.** Add a script under `tests/` that builds a clip with planted events
  (like `make_test.sh`) and its expectations in `run_tests.py`. Synthetic clips only: no copyrighted or
  private footage in the repo.
- **A Sheng word that's missing** from `skills/watch-video/data/sheng/lexicon.json`.
- **A wrong answer.** Open an issue with what the skill said, what actually happens, the timestamp, and
  a way to get the clip. These are the most useful reports.

## Bigger pieces

- **Linux and Windows.** `skills/watch-video/scripts/vsense/` (Swift, Apple frameworks) does OCR, sound classes, faces,
  pose and image rendering. A portable backend needs the same subcommands and JSON shapes.
- **More languages**, following the Sheng mode pattern in `skills/watch-video/scripts/sheng.py`.
- **Live mode** that keeps up with real time.
- **Lower cost**: anything that keeps answers right with fewer tokens.

## Rules

- Run `python3 tests/run_tests.py` before a pull request; it must pass.
- A detection rule must be checked against real frames, not just a synthetic case. Say what you checked.
- The skill describes people and never identifies them. No face recognition, no guessing gender, age or
  ethnicity. Changes that add these won't be merged.
- Everything runs on the user's machine. No uploads.
- State limits plainly. If a change only works on one kind of footage, say so in `REFERENCE.md`.
