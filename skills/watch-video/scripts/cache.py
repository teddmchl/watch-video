"""Keep ~/.cache/watch-video under a size cap by removing the least recently used work dirs.

The cap defaults to DEFAULT_CAP_MB and is set with WATCH_VIDEO_CACHE_MB (0 turns it off).
Only folders this tool made are ever removed (ones holding state.json or LIVE.md), never one
used in the last few minutes (a scan or live run may be writing to it), and never one passed in
`keep`.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

DEFAULT_CAP_MB = 2048
ENV = "WATCH_VIDEO_CACHE_MB"
BUSY_SECONDS = 300
MARKERS = ("state.json", "LIVE.md")
MB = 1024 * 1024


def cap_bytes(env: dict | None = None) -> int | None:
    """The cap in bytes, or None when the cap is off."""
    raw = (os.environ if env is None else env).get(ENV, "").strip()
    try:
        mb = float(raw) if raw else DEFAULT_CAP_MB
    except ValueError:
        mb = DEFAULT_CAP_MB
    return None if mb <= 0 else int(mb * MB)


def is_work_dir(d: Path) -> bool:
    return d.is_dir() and not d.is_symlink() and any((d / m).exists() for m in MARKERS)


def size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and not f.is_symlink())


def last_used(d: Path) -> float:
    return max([d.stat().st_mtime, *(f.stat().st_mtime for f in d.rglob("*") if not f.is_symlink())])


def touch(d: Path) -> None:
    """Mark a work dir as just used, so follow-up questions keep it at the back of the queue."""
    try:
        os.utime(d)
    except OSError:
        pass


def entries(root: Path) -> list[tuple[Path, int, float]]:
    """(dir, bytes, last used) for every work dir under root, least recently used first."""
    if not root.is_dir():
        return []
    rows = [(d, size(d), last_used(d)) for d in root.iterdir() if is_work_dir(d)]
    return sorted(rows, key=lambda r: r[2])


def prune(root: Path, cap: int | None, keep: set[Path] | None = None,
          now: float | None = None) -> list[tuple[Path, int]]:
    """Remove the oldest work dirs until root fits under cap. Returns (dir, bytes) removed."""
    if cap is None:
        return []
    rows = entries(root)
    total = sum(b for _, b, _ in rows)
    keep = {Path(k).resolve() for k in (keep or set())}
    now = time.time() if now is None else now
    removed: list[tuple[Path, int]] = []
    for d, b, used in rows:
        if total <= cap:
            break
        if d.resolve() in keep or now - used < BUSY_SECONDS:
            continue
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            removed.append((d, b))
            total -= b
    return removed


def human(n: int) -> str:
    return f"{n / MB:.1f} MB" if n < 1024 * MB else f"{n / 1024 / MB:.2f} GB"
