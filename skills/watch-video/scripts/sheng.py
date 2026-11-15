"""Sheng mode: recognising Kenyan speech (Swahili, Sheng, English, mixed) well enough to follow.

Built from open sources (see data/sheng/ and scripts/build_sheng.py): a 967-word Sheng lexicon from
JeunesseAfricaine/sheng_nlu, lughayangu.com, Wiktionary and Wikipedia; Swahili words from Kalebu/kamusi.
Measured on a read Sheng script (character error rate): small model 34%, large-v3-turbo forced to
Swahili 28%, turbo + Sheng hint 23%.
"""

from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path

import media

SKILL = Path(__file__).resolve().parent.parent
DATA = SKILL / "data" / "sheng"
MAIN_MODEL, MAIN_DTW = media.HOME / "models" / "ggml-large-v3-turbo-q5_0.bin", "large.v3.turbo"
EAR2_MODEL, EAR2_DTW = media.HOME / "models" / "ggml-swahili-small-q8_0.bin", "small"
KENYAN_SHARE = 0.15       # this share of words being Sheng/Swahili (not English) switches Sheng mode on
WORD = re.compile(r"[a-z][a-z']+")


def available() -> bool:
    return MAIN_MODEL.exists() and (DATA / "prompt.txt").exists()


def hint() -> str:
    return (DATA / "prompt.txt").read_text().strip()


@lru_cache(maxsize=1)
def vocab() -> tuple[frozenset, frozenset]:
    """(Sheng + Swahili words, English words)."""
    kenyan = set(json.loads((DATA / "lexicon.json").read_text())) if (DATA / "lexicon.json").exists() else set()
    if (DATA / "kamusi_words.csv").exists():
        with (DATA / "kamusi_words.csv").open(errors="replace") as f:
            for row in csv.DictReader(f):
                kenyan |= set(WORD.findall((row.get("Word") or "").lower()))
    english = {w.strip().lower() for w in Path("/usr/share/dict/words").read_text().splitlines()}
    english |= {"i", "a", "the", "and", "is", "it", "to", "of", "you", "this", "that", "me", "my", "so", "okay"}
    return frozenset(kenyan), frozenset(english)


def sounds_kenyan(res: dict) -> bool:
    """A first pass that heard Swahili, or whose words are often Sheng/Swahili and not English."""
    if (res.get("language") or "").startswith("sw"):
        return True
    words = [w for s in res.get("segments", []) for w in WORD.findall(s["text"].lower())]
    if len(words) < 4:
        return False
    kenyan, english = vocab()
    hits = sum(1 for w in words if w in kenyan and w not in english)
    return hits / len(words) >= KENYAN_SHARE
