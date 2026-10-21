# The matcher: code finds the places a word could go

PLAN.md section 6 replaces the swaps a model proposes with code: an index from English to entries marks every span of
a passage that a word could take, with all its options, and a model only settles the doubtful senses (NEXT_STEPS step
12). This is the index and the matcher, in JavaScript (`extension/src/matcher.js`) and Python (`scripts/matcher.py`),
and a lab that scores it (`tools/prompt-lab/matcher-lab.mjs`); then the sense flags that say which of its options code
may take alone (below). The model's check is `extension/src/verify.js`, described and measured in
[swap-prompt.md](swap-prompt.md) ("Code finds the places, a model checks them"). Nothing in the extension uses any of it
yet; the web planner still takes the model's swaps.

## What it does

`buildIndex(entries)` (`build_index` in Python) takes entries `{ ja, en }`: `en` is the lexicon's `"a / b"`, or a list
of glosses (a dictionary's), and an entry may carry JMdict part-of-speech codes as `pos`. For each English alternative
it indexes:

- the gloss as written, lower case, without parentheses, dots, a leading "to" or a counter's description;
- its inflections, by rule (-s, -es, -ies; -ed, -d, -ied; -ing; -er and -est, doubling the last consonant where
  English does) and by the irregular tables in `data/english.json` (159 verbs, 59 plurals, 9 comparatives): thought,
  said, went, men, children, people, better;
- a phrase's first word as a verb ("took time off") and its last as a noun ("younger sisters"); "be wrong" also as
  "wrong", "is wrong", "were wrong";
- phrases that stand for the gloss (`phrases` in the data file): "there is", "there was", "is there", "there's"... for
  "exist"; "a lot of" and "lots of" for "a lot"; "looked at" for "look"; "in front of" for "front"; "this" for "this
  one" (so "this book" can be この); "myself", "herself"... for "self".

Function words (closed-class English: pronouns, determiners, prepositions, conjunctions, auxiliaries, numbers and the
commonest function adverbs, 246 in the data file) are never inflected by rule.

`matchText(index, text)` (`match_text`) finds whole words only, as `lab.mjs` places the oracle's words: a possessive
counts ("grandfather" in "grandfather's"), a contraction doesn't ("can" in "can't"), and like the lab, a word right
after an apostrophe (in single quotes) doesn't either. It is case-insensitive; a phrase may cross white space or a
hyphen as its gloss has it, nothing else. Every match is a candidate; the longest wins, and spans never overlap.
Entries that share an English form become options of one span. Five rules drop matches (each can be switched off to
measure it):

| Rule | Drops | Example |
|---|---|---|
| kinds | forms a word's part of speech doesn't have: a noun gets only plurals, an adjective only -er and -est, a verb its verb forms. From `pos`, else guessed from the Japanese (ending in い an adjective, in a u-row kana a verb, not もう or 二つ; anything else a noun) | "news" is not 新しい, "worked" not 仕事 |
| lexical | where a span is a gloss as written, the options that reach it only by a rule's inflection; irregular forms stay | "times" is 回, not 時間; "later" is 後, not 遅い; "people" stays 人 and 人間 |
| helpers | "have" and "do" as helper verbs, for a gloss "have" or "do" | "had been", "has never played", "have to", "did not", "do you" |
| nouns | after a determiner, entries that are only verbs | "the rest" is not 休む, "a look at" not 見る |
| hyphens | a match inside a hyphenated word | "one-on-one", "well-known" (a gloss "well-known" still matches) |

## What the next step gets

A list of spans in text order:

```js
{ start, end,          // offsets into the text: UTF-16 code units in JavaScript, code points in Python
  english,             // the text of the span, as written ("There was", "grandfather")
  options,             // the entries, as given to buildIndex (the same objects), in their order there
  glosses,             // for each option, the English alternative it matched ("exist" for "there was")
  function }           // every word of the span is a function word ("there's" counts as "there")
```

An option whose entry and gloss are flagged safe (below) can be swapped by code, never in a span of closed-class English;
the rest go to the model with their options (`verify.js`). The `function` flag and the shared function-word list are
there for those flags.

The two implementations are kept the same by shared test vectors (`tests/matcher_cases.json`, run by
`extension/test/matcher.test.js` and `tests/test_matcher.py`), and they agree on all 2,347 spans of the lab's 87
passages with all 1,766 words in the state, rules on and off. `data/english.json` is the one place to edit the
English; `python3 scripts/matcher.py --js` writes the extension's copy (`extension/src/english.js`), and both test
suites fail if it is stale.

## On the lab's judged passages (2026-10-20)

`tools/prompt-lab/matcher-lab.mjs` scores the matcher against `oracle.json` as `lab.mjs` scores a model: every option
of every span is a hit; it covers the "use" places it overlaps whose Japanese includes it, and is right, fine ("ok"),
wrong ("not") or unjudged. The words are the 324 introduced words of the 2026-10-20 snapshot (known, active, shaky),
with their lexicon glosses; the 79 judged passages have 408 "use" places (378 entries, some with several places).
Intervals are the 5th to 95th percentile over 2,000 resamples of the passages.

| | Naive | Matcher |
|---|---|---|
| "use" places covered | 340 of 408, 83.3% (80-86%) | **379 of 408, 92.9%** (91-95%) |
| "use" entries covered, each once | 312 of 378, 82.5% | 349 of 378, 92.3% |
| hits right / fine / wrong | 440 / 86 / 207 | 488 / 95 / 204 |
| wrong share of judged hits | 28.2% (25-32%) | **25.9%** (23-29%) |
| function words: right / fine / wrong | 243 / 58 / 134, 30.8% wrong | 259 / 61 / 135, 29.7% (25-34%) |
| content words: right / fine / wrong | 197 / 28 / 73, 24.5% wrong | 229 / 34 / 69, 20.8% (17-25%) |
| unjudged hits | 120 | 81 |
| spans; options a span | 633; 1.35 | 635; 1.37 |
| spans with a right option / only fine / only wrong / unjudged | 341 / 71 / 147 / 74 | 380 / 71 / 142 / 42 |
| judged places hit that are "not" (the review's count) | 178 of 595, 29.9% | 173 of 636, 27.2% |

The naive baseline (`MATCHER=naive`) is the review's experiment rebuilt: each gloss alternative as written plus -s,
-es, -ed, -d, -ing and y to -ies, whole words, longest first, no overlaps. Counted as the review counted (each "use"
entry once; "not" among the judged places hit) it gives 312 of 378 and 29.9%, against the review's 313 of 377 and 30%.
The review's split, 39% wrong for function words and 17% for content words, grouped "time" and "thing" with the
function words; with the closed-class list here the split is 31% and 25% for the naive matcher, 30% and 21% for this
one.

What closed the gap: irregular forms (said, thought, went, bought, had), "there is" and its kin, "this" before a noun
as この, comparatives (smaller, hardest, cutest), "were wrong" from "be wrong", and plurals. The 29 places still missed
are nearly all English the lexicon's gloss doesn't list: "about" for について (7), "watch" for 見る "see / look at"
(5), "Japanese" for 日本語 "japanese language" (2), "yet" for まだ (2), "different" for 違う (2), mom, guy, whole,
own, everyone, little sister, hour, go to bed, "starts" for 始まる "begin". The dictionary's glosses should cover most
of them; with the few English additions `hints.js` already makes (`EXTRA=1`) coverage is 389 of 408 (95.3%). Three
are suru nouns the guessed parts of speech leave as plain nouns (studying 勉強, explained 説明) and "raining" for 雨.

Each rule switched off on its own, the others on (`ABLATE=1`). The counts are exact and paired (the same passages), so
a difference of a few hits is real for these passages, though small next to the spread of the totals:

| Without | Covered | Right / fine / wrong | Unjudged | What changes |
|---|---|---|---|---|
| (none) | 379 | 488 / 95 / 204 | 81 | |
| kinds | 382 | 491 / 96 / 207 | 81 | +3 right (studying, explained, raining), +1 fine, +3 wrong (news 新しい, worked 仕事, studying 研究) |
| lexical | 379 | 488 / 95 / 207 | 82 | +3 wrong (times 時間 and 時, hours 〜時), +1 unjudged (later 遅い) |
| helpers | 379 | 488 / 95 / 213 | 98 | +9 wrong and +17 unjudged (had been, have to, has never...), nothing right lost |
| nouns | 379 | 488 / 95 / 207 | 81 | +3 wrong (the rest 休む twice, changes 変える) |
| hyphens | 379 | 488 / 95 / 208 | 81 | +4 wrong (the two "one"s of one-on-one, 一 and 一つ each) |
| all five | 382 | 491 / 96 / 226 | 100 | |

Together they remove 22 wrong and 19 unjudged hits for 3 right and 1 fine. "kinds" is a wash here; it is kept because it
stops forms that are other words (news, placed, booking), and with a dictionary's `pos` the suru nouns get their verb
forms back.

The wrong hits that remain are senses, not forms: one 18 (labels and pronouns), about (くらい where it means
"concerning") 11, all 9, and 9, had 9, time 9, so 8, thing 7, much 6, right 6, which 6, after 5, before 5, stop 5. The
extension's own validation drops some of them when they become word swaps (`CHECKS=1`: numberTrap for "Day one" and
"the one", misplaced for 前 and 後 as clause joiners): 170 wrong of 749 judged hits (22.7%), at a cost of 3 places
("before" as 前, twice in "before bed"). 142 spans have only wrong options: there the model has to say "none". This is the work for step
12; its bar is at most 3% wrong.

## Sense flags (2026-10-21)

`tools/sense-flags.mjs` marks each pair of an entry and one of its English glosses (as `glossAlternatives` writes it)
safe or doubtful. Safe means that wherever that English appears in ordinary prose, in any of its forms, this Japanese
word renders it correctly at least 95 times in 100, so code may take the option without a model reading the sentence.
A gloss made only of closed-class English is doubtful by rule and never asked about. Everything else is asked of
Claude Haiku 5.5 (low effort, thinking on) in calls of about 80 pairs, each entry once with its reading, parts of speech
and senses, then its numbered glosses; the answer is two lists of numbers. The answers are cached
(`~/.cache/japanese-weave-build/senses-<prompt>-<model>[-s<sample>].jsonl`), and the flags file counts as a cache too,
so a rerun costs nothing. The flags go to `data/senses.tsv`, one line per entry with its safe and doubtful glosses;
`extension/src/senses.js` (`senseFlag`, `safeOptions`) and `scripts/senses.py` look a span's options up, with shared
test vectors (`tests/senses_cases.json`). A gloss the file doesn't list is doubtful.

```sh
SPEND_LOG=~/.cache/japanese-weave-build/spend/senses.jsonl ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key \
  WORDS=<copy of state.json> ALL=1 SAMPLES=1,2,3 CONFIRM=1 bun tools/sense-flags.mjs   # DRY=1: count and estimate only
```

**One pass is not stable.** The first prompt judged each pair from the entry's senses alone. On the 1,766 words of the
2026-10-20 snapshot (9,810 pairs: 875 closed-class, 8,935 asked) it found 2,206 safe, and in the lab the safe options
were wrong on 17 of 140 judged hits (12.1%): 時 for "time" 7 times, 行く for "go", 日 for "day", 仕事 for "work". An
earlier run of the same prompt on the 324 introduced words alone, in other batches, had marked those doubtful (2 wrong
of 104). Prompt 2 adds, after each gloss, up to four other ranked entries that share that English, most frequent first
(for 時 "time": `(also: ほど (extent; degree), 時間 (time), ころ (time; around), 際 (occasion; time) and 21 more)`), and
says the pair is safe only where this word is the usual translation in almost every use. Two independent samples of it (the second with the entries in another order) agree on 87.8% of the
pairs, but on only 864 of the pairs either finds safe (1,422 and 1,397). So a pair is safe only when every sample
says so. A later sample need only ask the pairs the earlier ones found safe (`CONFIRM=1`); such a confirming pass, which
sees only likely pairs, is somewhat more lenient than a full second sample (it kept 947 of the first sample's 1,422
against 864), so the file uses two confirmations.

The lab's check (`tools/prompt-lab/check-lab.mjs`, "Code alone"): the matcher's hits with the 324 introduced words
whose option is flagged safe, judged against `oracle.json`, with Wilson's 90% interval:

| Flags (your 1,766 words) | Safe pairs | Safe hits right / fine / wrong | Wrong | 90% interval | "use" places they cover | Cost |
|---|---|---|---|---|---|---|
| prompt 1, one sample | 2,206 | 114 / 9 / 17 | 12.1% | 8.3-17.4% | 109 (26.7%) | $0.145 |
| prompt 2, sample 1 | 1,422 | 91 / 4 / 3 | 3.1% | 1.2-7.4% | 89 (21.8%) | $0.193 |
| prompt 2, sample 2 | 1,397 | 67 / 1 / 0 | 0% | 0-3.8% | 67 (16.4%) | $0.193 |
| prompt 2, samples 1 and 2 agree | 864 | 62 / 1 / 0 | 0% | 0-4.1% | 62 (15.2%) | |
| prompt 2, sample 1 and a confirming pass agree | 947 | 66 / 2 / 3 | 4.2% | 1.7-10.1% | 66 (16.2%) | $0.046 |
| **prompt 2, all three agree** (`data/senses.tsv`) | **694** | **53 / 0 / 0** | **0%** | **0-4.9%** | **53 (13.0%)** | |

The check is thin: the 53 safe hits come from 20 distinct pairs (新しい "new" and 読む "read" 10 each), and every wrong
hit above came from one or two frequent pairs (all three of the confirming pass's are 日 "day"). It shows the flags
keep the commonest traps out; it can't show that each pair meets 95%. Read one by one, 3 of 60 random safe pairs of
your words look doubtful to me: 時代 "epoch" (training epochs), 反応 "react" (the React framework) and 選択肢
"options" (command-line or stock options), each a technical or proper-noun use common on the pages you read; the other
57 hold.

Safe options cover few places: 13% of the "use" places, against 80% more that only doubtful options reach, and 342 of
the 581 doubtful spans are closed-class English. So nearly every passage still needs the model; the flags mostly spare
it work and keep the most frequent safe words exact. Their other use is counting matches in what you read (NEXT_STEPS
step 13), where a wrong safe flag counts a word you didn't read.

**Every swappable entry.** Flagging all 17,210 swappable entries with the words in the state was estimated at $1.36
and cost $1.69: a first sample of the 50,126 pairs beyond your words ($1.03; about 12,800 safe, a quarter, since rarer
entries' glosses are more specific), then two confirming passes ($0.39 and $0.27), in calls of about 80 pairs, 12 s
each. `data/senses.tsv` holds every judged pair, 59,061 of 17,297 entries, 10,583 of them safe (17.9%): 1.2 MB, 566 KB
compressed. An extension needs only the safe glosses of the words in use. Reading 60 random safe pairs outside your
words, 2 look doubtful to me: 速記 "shorthand", nowadays mostly "X is shorthand for Y", and 小人 "little person". Most
are specific or several words long ("cutting board", "economic growth") and rarely turn up.

## Speed

On an AMD Ryzen 5 7640U, the 79 judged passages (9 to 40 words, median 19), ranges over 3 to 6 runs:

| Entries | Index (Bun 1.3) | Match a passage (Bun): mean; max | Index (Python 3.14) | Match (Python): mean; max |
|---|---|---|---|---|
| 324 introduced words | 2.5-3 ms | 0.016-0.018 ms; 0.03-0.05 ms | 7-10 ms | 0.05-0.10 ms; 0.12-0.20 ms |
| 1,766 words in the state | 13-14 ms | 0.020-0.023 ms; 0.04-0.05 ms | 33-43 ms | 0.07-0.10 ms; 0.16-0.21 ms |
| 22,000 synthetic entries | 245-295 ms | 0.05-0.10 ms; 0.10-0.42 ms | 730-830 ms | 0.16-0.24 ms; 0.46-1.55 ms |

The synthetic entries (the same in both languages) draw one to three glosses of one to three words from the lexicon's
English, so many share a word, as a dictionary's do. On long text with the whole dictionary, Python's check of a
candidate against the spans already taken was quadratic and took most of the time (916,000 comparisons for one
12,000-character text); it now marks the tokens taken, which gives the same spans (49,555 on 600 replies checked). Python's maximum varies from run to run (with the garbage
collector off as well); its slowest passage is usually the longest one, and its mean stays near 0.2 ms.

## Running it

```sh
cp ~/.japanese-weave/state.json /tmp/state.json        # or python tools/prompt-lab/words.py > /tmp/words.json
WORDS=/tmp/state.json bun tools/prompt-lab/matcher-lab.mjs                 # the matcher
WORDS=/tmp/state.json MATCHER=naive bun tools/prompt-lab/matcher-lab.mjs   # the naive baseline
WORDS=/tmp/state.json ABLATE=1 VERBOSE=1 bun tools/prompt-lab/matcher-lab.mjs
WORDS=/tmp/state.json TIMING=1 bun tools/prompt-lab/matcher-lab.mjs; python3 scripts/matcher.py --time /tmp/state.json
```

`ENTRIES=` takes a JSON list of entries instead of the word state (a dictionary's entries for the same words, say);
`WITHOUT=helpers,nouns` switches rules off; `CHECKS=1` and `EXTRA=1` as above; `SUMMARY=` writes the numbers as
JSON. The lab's oracle parsing is shared with `lab.mjs` (`tools/prompt-lab/oracle.mjs`).

## Open

- Senses: the matcher marks places, not meanings; about a quarter of its judged hits are the wrong sense. The flags
  (below) and the model check (swap-prompt.md) bring that under 1% in the lab.
- Sense flags are a model's judgment, and a single pass flips on frequent pairs; three agreeing samples keep the lab's
  safe hits right, but the lab checks only 20 distinct safe pairs. Technical and brand senses (React, epoch, options)
  slip through; a per-site or per-topic list of such words, or a check of flags against the lookups in the log, would
  catch them.
- Parts of speech are guessed from the Japanese until entries carry `pos`; kanji compounds are taken as nouns, so suru
  nouns miss their verb forms (studied, explained).
- The oracle judges only what the hints and Haiku proposed: 81 hits are unjudged (64 of them function words: "and",
  "that", "this" as この...), so the wrong share is over judged hits only.
- Like `lab.mjs`, "it's" and "there's" count as a possessive of "it" and "there" where no phrase covers them, and a
  word in single quotes is never matched.
