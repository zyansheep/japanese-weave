# The dictionary

`data/dictionary.tsv` holds every common JMdict entry (22,647) and the 4,322 other entries whose frequency rank is
as good as a typical common entry's, 26,969 in all, for the plan's vocabulary ([PLAN.md](../PLAN.md) section 6,
[NEXT_STEPS.md](../NEXT_STEPS.md) step 11). A refresh reads it for the starting values of words (each word's entry
gives its prior's word features: [adaptive-difficulty.md](adaptive-difficulty.md)), and your reading is counted in it
(`scripts/reading.py`): intake adds a dictionary word to the word state when your reading nominates it
([adaptive-difficulty.md](adaptive-difficulty.md), "Words from your reading"). The word list is still
`data/lexicon.csv`.

## Building it

```sh
python3 tools/build_dictionary.py     # downloads the sources once into ~/.cache/japanese-weave-build
```

The sources are pinned in `tools/build_dictionary.py` by URL and sha256, and the file's comment lines repeat them:
jmdict-eng 3.6.2 (all of JMdict of 2026-10-19, English) from jmdict-simplified, and the JPDB v2.2 and BCCWJ frequency
lists from Kuuuube/yomitan-dictionaries at commit `d6fde80`. A rebuild from an empty cache gives the same file byte
for byte. Loading JMdict takes about 1.2 GB of memory; the build takes about 20 seconds.

An entry is common when JMdict marks one of its spellings common; these are exactly the entries of
jmdict-eng-common. Common entries are built by the pipeline of `data/lexicon.csv` (`tools/build_candidates.py`)
without the word-by-word review: the same written forms, readings and rank (the geometric mean of the JPDB and BCCWJ
ranks, or three times the one rank when only one list has the word). With build_candidates.py's ranking, the common
swappable entries reproduce 3,988 of its 4,000 candidates with the same scores (the other 12 are the written-form
fix below). It differs in five ways:

- No entry is dropped. Its filters (parts of speech, register, katakana, length) become the `swap` flag, and common
  entries without a rank come after the ranked ones.
- Entries JMdict doesn't mark common are kept when their score is at most 20,000 (`--frequent`; the median common
  entry's is 16,749), counting only spellings that no common entry has with the same reading. Otherwise the particle
  て would take the rank of 手, read て. These 4,322 entries have `common` 0: 関数, 変数, フォルダ, おばさん, このまま,
  なので. 2,346 of them are swappable.
- A word written in kana is also ranked by its kanji spellings. BCCWJ files こと under 事, so build_candidates.py put
  こと 1,419th; it is 15th here. 683 scores of common entries change, 672 of them up.
- A search-only, rare, outdated or irregular kanji spelling is never the written form (いけない, not 行けない), nor is
  a spelling in letters not marked common (オッケー, not ＯＫ). 51 common entries.
- Homographs that share a rank (both そう are ranked by the kana そう) put the swappable one first.

Words in `data/lexicon.csv` keep its reviewed gloss and romaji, which fixed morpheme boundaries (実は jitsu wa) that
`kana.romaji` can't. All 1,500 are entries with the same spelling and reading.

## The file

Tab-separated, UTF-8. A few `#` comment lines (format, license, sources), a header row, then one entry per line in
rank order. 3.60 MB, 1.62 MB gzipped.

| Field | Meaning |
|---|---|
| `id` | JMdict sequence number. Stable across JMdict releases: use it to refer to an entry. |
| `ja` | Written form as normally written: kana when JMdict marks the first sense usually-kana. |
| `kana` | Reading, in hiragana (katakana words keep ー: カレー is かれー). |
| `romaji` | Hepburn with macrons (`kana.romaji`), or the lexicon's. |
| `en` | A short gloss for display: the lexicon's, else the first sense's first one or two. |
| `glosses` | English glosses, senses separated by `\|` and glosses by `;`. The first sense comes first, then the others; notes in parentheses dropped; at most 8 (5 from the first sense). |
| `pos` | JMdict part-of-speech tags of all senses, comma-separated, the first sense's first. |
| `score` | The combined rank (lower is more frequent); empty for the 181 common entries in neither list. |
| `jpdb`, `bccwj` | The two ranks it combines; empty where a list doesn't have the word. |
| `common` | 0 for an entry JMdict doesn't mark common, kept for its rank. Otherwise 1 if the written form is marked common, plus 2 if the reading is (all but 3 common entries have 3). jmdict-simplified reduces JMdict's priority tags (news1, ichi1, nf01...) to this. |
| `loan` | JMdict's source languages (`por`, `ger`, `eng`...; 649 entries), `kata` for a katakana word without one (3,890), or empty. |
| `swap` | 1 if build_candidates.py's filters keep it: a noun, adverb, conjunction, adjective or expression that can stand in for an English word (17,210 entries). |
| `field` | The first sense's JMdict field tags, comma-separated (`med`, `law`, `finc`, `comp`...; 1,022 entries): the sense the display gloss and the matcher's English come from. A later sense's tags are mostly incidental (鏡 "mirror" has a finance sense), so they aren't kept. Chat's topical words leave out medical, legal and financial entries by it (weave.SENSITIVE_FIELDS). Since format 2. |
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

`find` prefers, in order: the fewest inflection steps, the given reading, the written form over a reading or kana
spelling over a kanji spelling, the given gloss's English words, then rank.

Loading reads the lines and nothing more; indexes are built when first used. In a fresh process: import 10 ms, load
28 ms, the first `get` 6 ms, the first `find` 63 ms (the spelling index), later finds 0.1 ms, the first `romaji` 70 ms,
all of `english()` 26 ms. The chat hook doesn't load it: what it needs on every prompt (the topical words) it reads
from the reading index, an SQLite file built from the dictionary in the background (`scripts/reading.py`).

JavaScript, `extension/src/dictionary.js`: `parse(text)` gives the same entries (115 ms and about 24 MB of heap in
Bun for the whole file); `load(url)` fetches it. The extension doesn't ship the file yet: setup copies only
`extension/`, so shipping it means copying `data/dictionary.tsv` into the package, which adds 3.60 MB unpacked and
1.61 MB to the .xpi. The fields the matcher needs (`id ja kana romaji en glosses swap score`) are 2.83 MB, 1.27 MB
compressed; the swappable entries alone are 2.25 MB, 1.01 MB compressed.

## Coverage

`tools/dictionary_coverage.py <copy of a state directory>` checks that the lexicon, the word state, the Anki words and
every word opened in the gloss log resolve to entries, and lists the misses. On the 2026-10-20 snapshot:

- `data/lexicon.csv`: 1,500 of 1,500, each to the entry with its spelling, reading and gloss.
- `state.json`: 1,763 of 1,766 (99.8%). 6 resolve to entries not marked common (マジ, おばさん, おじさん, こちらこそ,
  お陰, 路). The 3 misses are Anki kanji cards with no meaning: 敝, 察 and 兹. 3 resolve to a different word than the
  card means:
  - て, the te-form particle, finds って: the particle て isn't marked common, and its only rank is 手's reading.
  - ないで finds ない.
  - 路 read ろ finds 路 read じ.
- `anki.csv`: 571 of 574, the same misses.
- Opened in the log: 148 of 151 words. 3 more were whole sentences opened as one gloss, which have no entry by design,
  and 47 opens were sentence translations. Of the 42 opened words the word state doesn't match, 39 resolve: 9 through
  the inflection rules (戻せます is 戻す), and 4 to entries not marked common (関数, 変数, フォルダ, 封印). The 3 misses
  are technical words too rare in general text to pass the cut: 再帰 (score 67,425), ワークフロー (260,674) and
  ツーウェイ (293,620).

## License

JMdict is © the Electronic Dictionary Research and Development Group, used under CC BY-SA 4.0
(https://www.edrdg.org/edrdg/licence.html); `data/dictionary.tsv` is derived from it and shared under the same license.
