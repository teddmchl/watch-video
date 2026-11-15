# Sheng data sources

`lexicon.json` (967 Sheng words) and `prompt.txt` (the hint given to the speech model) were built by
`scripts/build_sheng.py` from:

- `sheng_nlu_README.md`: [JeunesseAfricaine/sheng_nlu](https://huggingface.co/datasets/JeunesseAfricaine/sheng_nlu), MIT. Real Sheng chat phrases. The hint is made only of these.
- [Wiktionary, Category:Swahili slang](https://en.wiktionary.org/wiki/Category:Swahili_slang) and [Wikipedia, Sheng slang](https://en.wikipedia.org/wiki/Sheng_slang), CC BY-SA. Headwords only.
- [lughayangu.com Sheng dictionary](https://lughayangu.com/sheng). Headwords only; its pages and definitions are not redistributed here.
- `kamusi_words.csv`: [Kalebu/kamusi](https://github.com/Kalebu/kamusi), MIT (see `kamusi_LICENSE`). Standard Swahili, used to tell Sheng apart.

To rebuild with fresh sources, save the pages into `sources/` (see the script's docstring) and run it.
Know a Sheng word that is missing? Add it to `lexicon.json` in a pull request.
