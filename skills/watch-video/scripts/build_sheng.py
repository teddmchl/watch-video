"""Build the Sheng lexicon and speech hint from the files in data/sheng/ (run once, offline).

Sources (all kept in data/sheng/ with their origin):
  sheng_nlu_README.md   JeunesseAfricaine/sheng_nlu (MIT): ~300 real Sheng chat phrases
  sources/*.html        lughayangu.com Sheng dictionary pages (headwords and posts)
  sources/wikt.json     Wiktionary "Swahili slang" category
  sources/wiki.json     Wikipedia "Sheng slang" article
  kamusi_words.csv      Kalebu/kamusi (MIT): 16.6k standard Swahili words, used to tell Sheng apart

Writes data/sheng/lexicon.json (Sheng words, with where each came from) and data/sheng/prompt.txt
(real Sheng sentences, chosen to cover the most common Sheng words, for whisper's --prompt).
"""

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "sheng"
PROMPT_CHARS = 600          # whisper keeps ~224 tokens of prompt; stay well inside it
WORD = re.compile(r"[a-z][a-z']{2,}")
STOP = {"https", "www", "org", "nbsp", "mit", "url", "kenya", "nairobi", "kenyan", "luhya", "gikuyu",
        "kikuyu", "luo", "kamba", "engsh", "lughayangu", "standardmedia", "eds", "kcse", "wpi"}


def page_text(path: Path) -> str:
    """Only the dictionary part of a lughayangu page, not its menus and footers."""
    s = path.read_text(errors="replace")
    body = s[s.find("<body"):]
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "\n", body)))
    for start, end in (("Dictionary of popular Sheng words", "Sunda email"),
                       ("Sheng Stories", "Get the latest Sheng Word"), ("", "Get the latest Sheng Word")):
        i, j = text.find(start), text.find(end)
        if i >= 0 and j > i:
            return text[i:j]
    return ""


def is_english(w: str, english: set[str]) -> bool:
    if w in english:
        return True
    for suffix, repl in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""),
                         ("ing", "e"), ("'s", ""), ("ly", ""), ("er", ""), ("ers", "")):
        if w.endswith(suffix) and w[: -len(suffix)] + repl in english:
            return True
    return False


def phrases() -> list[str]:
    """Lines of real Sheng from the sheng_nlu README (skip headings and markup)."""
    out = []
    for line in (DATA / "sheng_nlu_README.md").read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue                                   # phrases are bullet items; headings are not
        line = re.sub(r"\.{2,}", " ", line[2:]).strip(" .")
        if not line or len(line) > 80:
            continue
        if WORD.search(line.lower()):
            out.append(line)
    return out


def known_words() -> tuple[set[str], set[str]]:
    english = {w.strip().lower() for w in Path("/usr/share/dict/words").read_text().splitlines()}
    swahili: set[str] = set()
    with (DATA / "kamusi_words.csv").open(errors="replace") as f:
        for row in csv.DictReader(f):
            for w in WORD.findall((row.get("Word") or "").lower()):
                swahili.add(w)
    return english, swahili


def main() -> None:
    english, swahili = known_words()
    texts: dict[str, str] = {"sheng_nlu": "\n".join(phrases())}
    for p in sorted((DATA / "sources").glob("*.html")):
        texts[f"lughayangu:{p.stem}"] = page_text(p)
    wikt = json.loads((DATA / "sources" / "wikt.json").read_text())
    texts["wiktionary"] = "\n".join(m["title"] for m in wikt["query"]["categorymembers"])
    wiki = json.loads((DATA / "sources" / "wiki.json").read_text())
    texts["wikipedia"] = wiki["parse"]["wikitext"]["*"]

    count: Counter[str] = Counter()
    where: dict[str, set[str]] = defaultdict(set)
    for src, text in texts.items():
        for w in WORD.findall(text.lower()):
            if is_english(w, english) or w in swahili or len(w) > 14:
                continue
            count[w] += 1
            where[w].add(src.split(":")[0])
    # keep words seen in a real-usage source, or in two separate sources (cuts page chrome)
    lexicon = {w: {"count": c, "sources": sorted(where[w])} for w, c in count.most_common()
               if w not in STOP}
    (DATA / "lexicon.json").write_text(json.dumps(lexicon, indent=0, ensure_ascii=False))

    # hint: greedily pick phrases that add the most not-yet-covered Sheng words
    pool, chosen, covered, size = phrases(), [], set(), 0
    while pool and size < PROMPT_CHARS:
        best = max(pool, key=lambda p: sum(count[w] for w in set(WORD.findall(p.lower())) - covered if w in lexicon))
        gain = set(WORD.findall(best.lower())) & set(lexicon) - covered
        pool.remove(best)
        if not gain:
            break
        chosen.append(best.rstrip(".?!") + ".")
        covered |= gain
        size += len(best) + 2
    (DATA / "prompt.txt").write_text(" ".join(chosen))
    print(f"{len(lexicon)} Sheng words; prompt {size} chars covering {len(covered)} of them")


if __name__ == "__main__":
    main()
