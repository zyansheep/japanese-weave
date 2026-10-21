# The dictionary

`data/dictionary.tsv` holds every common JMdict entry (22,647) with its frequency rank, for the plan's vocabulary
([PLAN.md](../PLAN.md) section 6, [NEXT_STEPS.md](../NEXT_STEPS.md) step 11). Nothing reads it at run time yet: the
word list is still `data/lexicon.csv`.

## Building it

```sh
python3 tools/build_dictionary.py     # downloads the sources once into ~/.cache/japanese-weave-build
```

The sources are pinned in `tools/build_dictionary.py` by URL and sha256, and the file's comment lines repeat them:
jmdict-eng-common 3.6.2 (JMdict of 2026-10-19) from jmdict-simplified, and the JPDB v2.2 and BCCWJ frequency lists
from Kuuuube/yomitan-dictionaries at commit `d6fde80`. A rebuild from an empty cache gives the same file byte for byte.

It is the pipeline of `data/lexicon.csv` (`tools/build_candidates.py`) without the word-by-word review: the same
written forms, readings and rank (the geometric mean of the JPDB and BCCWJ ranks, or three times the one rank when
only one list has the word). With build_candidates.py's ranking, the dictionary's swappable entries reproduce 3,988
of its 4,000 candidates with the same scores (the other 12 are the written-form fix below); with the ranking here,
3,903 of the 4,000 stay in the top 4,000. It differs in four ways:

- No entry is dropped. Its filters (parts of speech, register, katakana, length) become the `swap` flag, and entries
  without a rank come after the ranked ones.
- A word written in kana is also ranked by its kanji spellings. BCCWJ files こと under 事, so build_candidates.py put
  こと 1,419th; it is 15th here. 683 scores change, 672 of them up.
- A search-only, rare, outdated or irregular kanji spelling is never the written form (いけない, not 行けない), nor is
  a spelling in letters not marked common (オッケー, not ＯＫ). 51 entries.
- Homographs that share a rank (both そう are ranked by the kana そう) put the swappable one first.

Words in `data/lexicon.csv` keep its reviewed gloss and romaji, which fixed morpheme boundaries (実は jitsu wa) that
`kana.romaji` can't. All 1,500 are entries with the same spelling and reading.

## The file

Tab-separated, UTF-8. A few `#` comment lines (format, license, sources), a header row, then one entry per line in
rank order. 2.96 MB, 1.34 MB gzipped.

| Field | Meaning |
|---|---|
| `id` | JMdict sequence number. Stable across JMdict releases: use it to refer to an entry. |
| `ja` | Written form as normally written: kana when JMdict marks the first sense usually-kana. |
| `kana` | Reading, in hiragana (katakana words keep ー: カレー is かれー). |
| `romaji` | Hepburn with macrons (`kana.romaji`), or the lexicon's. |
| `en` | A short gloss for display: the lexicon's, else the first sense's first one or two. |
| `glosses` | English glosses, senses separated by `\|` and glosses by `;`. The first sense comes first, then the others; notes in parentheses dropped; at most 8 (5 from the first sense). |
| `pos` | JMdict part-of-speech tags of all senses, comma-separated, the first sense's first. |
| `score` | The combined rank (lower is more frequent); empty for the 181 entries in neither list. |
| `jpdb`, `bccwj` | The two ranks it combines; empty where a list doesn't have the word. |
| `common` | JMdict's common marker: 1 if the written form is marked common, 2 if the reading is (3 for both). jmdict-simplified reduces JMdict's priority tags (news1, ichi1, nf01...) to this, so in a file of common entries it is nearly always 3. |
| `loan` | JMdict's source languages (`por`, `ger`, `eng`...; 608 entries), `kata` for a katakana word without one (3,664), or empty. |
| `swap` | 1 if build_candidates.py's filters keep it: a noun, adverb, conjunction, adjective or expression that can stand in for an English word (14,864 entries). |
| `forms` | Other spellings, comma-separated: kanji spellings (殆ど for ほとんど), then other kana. |

The loaders add `rank` (the line's position, 1 the most frequent) and `kanji` (the kanji of the written form).

## Reading it

Python, `scripts/dictionary.py` (standard library only):

```python
d = dictionary.load()
d.find('なります')                    # なる: as written or conjugated (inflection.py), as knowledge.py matches words
d.find('つける', 'つける', 'switch on')  # kana and a gloss choose among homographs: 点ける, not 付ける
d.written('殆ど'), d.reading('かみ'), d.romaji('tabun'), d.get(1259290)
d.english()                          # (gloss, entry index, sense number) for every gloss, for the matcher
```

Loading reads the lines and nothing more; indexes are built when first used. In a fresh process: import 10 ms, load
25 ms, the first `get` 5 ms, the first `find` 51 ms (the spelling index), later finds 0.1 ms, the first `romaji` 62 ms,
all of `english()` 23 ms. The chat hook takes about 90 ms today, so a hook that resolves words on every prompt would
want the spelling index cached: a `marshal` copy of the lines and index loads in about 26 ms.

JavaScript, `extension/src/dictionary.js`: `parse(text)` gives the same entries (115 ms and about 22 MB of heap in
Bun for the whole file); `load(url)` fetches it. The extension doesn't ship the file yet: setup copies only
`extension/`, so shipping it means copying `data/dictionary.tsv` into the package, which adds 2.96 MB unpacked and
1.34 MB to the .xpi. The fields the matcher needs (`id ja kana romaji en glosses swap score`) are 2.35 MB, 1.06 MB
compressed; the swappable entries alone are 1.93 MB, 0.86 MB compressed.

## Coverage

`tools/dictionary_coverage.py <copy of a state directory>` checks that the lexicon, the word state, the Anki words and
every word opened in the gloss log resolve to entries, and lists the misses. On the 2026-10-20 snapshot:

- `data/lexicon.csv`: 1,500 of 1,500, each to the entry with its spelling, reading and gloss.
- `state.json`: 1,758 of 1,766 (99.5%). The 8 misses are Anki words: マジ, おばさん, おじさん, こちらこそ and お陰,
  which JMdict doesn't mark common, and 敝, 察 and 兹, kanji cards with no meaning. 3 resolve to a different word than
  the card means: て (the particle, not common) is 手, ないで is ない, and 路 read ろ is 道 (みち).
- `anki.csv`: 566 of 574, the same misses.
- Opened in the log: 144 of 151 words (3 more were whole sentences opened as one gloss, which have no entry by design;
  47 opens were sentence translations). Of the 42 opened words the word state doesn't match, 35 resolve, 9 of them
  through the inflection rules (戻せます is 戻す). The 7 misses are technical words JMdict doesn't mark common: 関数,
  変数, 再帰, フォルダ, ワークフロー, ツーウェイ, 封印.

Adding JMdict's other entries whose combined rank is within 20,000, leaving out kana spellings a common entry already
has (they would inherit its rank), would add 4,321 entries and cover 関数, 変数, フォルダ, 封印, おばさん, おじさん,
こちらこそ, お陰 and マジ; 再帰, ワークフロー and ツーウェイ rank past 60,000.

## License

JMdict is © the Electronic Dictionary Research and Development Group, used under CC BY-SA 4.0
(https://www.edrdg.org/edrdg/licence.html); `data/dictionary.tsv` is derived from it and shared under the same license.
