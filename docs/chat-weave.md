# Chat replies: draft and weave

How chat replies in T3 Code get their Japanese since 2026-10-22, and the experiment that chose it. The code is
`scripts/chatweave.py` and `scripts/chatweave.mjs` (the weave), `scripts/check_reply.py` (the check),
`scripts/twins.py` (the levels) and `weave.compose_draft` (the brief). Since 2026-11 the Japanese sentences are also
held to the grammar the reader knows and to a length the log supports ([grammar.md](grammar.md)), and a reply carries
every level at once, so the reader's level is applied when it is shown (below).

## The steps

1. Claude writes the reply in plain English and passes it to `fw.py weave --level N` on stdin.
2. The weave runs the extension's pipeline on it, with no model call (`chatweave.mjs`, under node or bun):
   - `matcher.js` finds every place one of the words in use could go;
   - `senses.js` says which options are flagged safe;
   - `verify.js` `candidatesOf` and `levels.js` `planSwaps` place them as at full: every candidate, whatever the
     level, since the reply has to hold what every level shows.

   Words known at a glance go in line by line, once a line. The rest go over the whole reply, a word again where it
   fits, with at most 6 dictionary words the reader doesn't have yet (those nearest the target lookup chance), which
   show only at full. A span the web would ask its model about is taken at its first option and marked
   `{{English→Japanese}}`. Each swap's English, reading and current lookup chance are kept in `weave-swaps.json` for
   the check. The first line names the learning words and asks for a Japanese twin of each sentence that fits the
   limits: at most so many Japanese characters a sentence (where known patterns reach a 5% open chance), the known
   grammar patterns plus at most one learning pattern, and this week's learning pattern with its explanation and
   example, to use on purpose in a twin or two (`chatweave.sentence_plan`). It asks for at most 1.5 times the
   sentences much heavier (+2) pays for at the price of a sentence, and at least 3: full shows every twin, but full
   costs more reading than it teaches (round 4 below) and is rarely read, so twins past what +2 uses mostly serve it;
   the slack leaves the ranking a choice and covers the twins the check drops.
3. Claude writes the reply from the output: its English sentences with the swaps whose sense fits, and right after
   each sentence that fits, on the same line, its twin in `<ja>...</ja>`: one Japanese or mixed sentence, or two short
   ones for a long English sentence, glossed as before, each ending with a bare 。 or ？. The twin's English is the text
   back to the end of the sentence before it (a `.`, `!`, `?`, `:` or `;` and a space, abbreviations like "e.g."
   aside), the end of an earlier `</ja>` on the line, or the line's start after a list, heading or quote marker. Code
   puts the English sentence on each closing mark as its translation, so it is written once. The rules and the list of
   known sense traps are `weave.DRAFT_RULES`.

       This is a problem.<ja><abbr title="this">これ</abbr>は<abbr title="もんだい · problem">問題</abbr>です。</ja>

4. Claude passes it to `fw.py check-reply --level N`, which:
   - stops only on what code can't fix: a `<ja>` without its `</ja>` or its English sentence, Japanese outside twins
     and glosses, a Japanese sentence outside `<ja>`;
   - drops a twin that breaks a limit (more than two sentences, too long, grammar not on the list, two learning
     patterns in a sentence) or any rule of the old check (a pronoun, もし with no conditional, bare particles that
     carry meaning, する on an English word and the rest): its sentence stays English;
   - puts back in English a swap that breaks the stays-English rules (only nouns, and adjectives right before a noun),
     and a content word's fifth gloss;
   - scores each twin (the sentence model, per Japanese sentence) and each swap (its lookup chance from the weave),
     decides what each level shows (thresholds and propensities, below), writes the final markup, checks that it reads
     back to the same elements and thresholds and that every level renders clean, and keeps the plan in
     `reply-plans.json` for the hook's sentence record;
   - prints what it dropped and put back, each level's expected opens against its budget, and the final markup under
     a line of `=====`.

   Claude may fix a dropped twin and check once more (at most twice in all), then sends everything below the `=====`
   line exactly as it is.

The full steps go out on a chat's first turn and every 20 turns or after a compaction (and once to a chat that had the
steps from before twins); other turns carry a short digest. While English only is on (T3 Code, an hour) the hook
keeps the steps, with a line that the reply shows English until English only is off (below); while chat intensity is
off, or in a chat with Japanese off, it sends a one-line note to reply in plain English and `fw.py weave` refuses at
off. Draft and weave needs `<abbr>` glosses (T3 Code with `fw.py glosses abbr`) and node or bun. It is off in Codex
and terminals, and with `fw.py chat-style classic`, which bring back the brief in which Claude weaves as it writes
(and the plain-English note under English only). The calibration line and topical words belong to the classic brief:
in draft style the weave and the check set the amounts. Brief records carry `mode: draft`.

## Levels after the fact

The contract with the T3 clients (desktop and phone) is the markup: a swapped word in English text is `<abbr
title="reading · meaning" data-en="original English" data-on="N">JAPANESE</abbr>`, shown as that gloss at levels N and
up and as its English below; a twinned sentence is `<span data-jw="ja" data-on="N">...</span><span data-jw="en"
data-on="N">...</span>`, the Japanese from N up and the English below. N is in -2..3 (3: full only); at -3 (off, and
English only) nothing is Japanese. Anything without `data-on` or `data-jw` renders as before, so older messages are
unchanged. Each report item T3 sends carries the level the message was rendered at, and the annotations and words it
reports come from the screen, so a hidden word is never reported as shown.

**Thresholds.** One order per reply and kind, cut at each budgeted level's lookup budget (`intensity.budget` per 100 of
the reply's English words). Twins spend the sentences' share of it (`SENTENCE_SHARE`), each Japanese sentence at its
predicted open chance but never less than the price of a sentence, so no level asks for more Japanese sentences than
the count before the sentence model did and normal's and much heavier's densities stay as they were; words spend the
whole budget at their current lookup chance, as the weave gave a chat reply's learning words before. Levels are filled
from -2 up, each starting from the level below, so the sets are nested. At each level twins come first (a twin shown
hides its English sentence's swaps, and charges the costly words in its Japanese to the words' part), then words, at
most the level's costly words in one English sentence and one use of each learning word below full. The element at
the edge of a part gets in with the chance (what is left) / (its cost), one uniform draw per reply and part, so a
level's expected spend is its part and the sets stay nested. Words known at a glance show from -2; dictionary words
and a learning word's later uses only at full; a twin's English swaps never above the twin, so as the level goes down
a sentence turns English before its words do. The order is by value per expected open: for a twin its own value, the
learning values of the costly words in its Japanese and this week's pattern, over its sentences' part; for a word its
learning value (1 at the target chance of 25%) over its chance.

**Exploration and propensities.** Each element's place in its order is, with chance 0.2 (`twins.EXPLORE`), a uniform
random place instead of its rank. So every element has a known chance of showing at each level, never 0 or 1 by the
model's predictions alone, and the elements placed at random are a sample the model didn't choose. An element's
propensity at a level is the share of 1,000 seeded runs of the same randomization in which it shows there (within
about 0.03 of the exact chance), kept with its threshold, its prediction and whether its place was random.

**What the reading model sees.** Only what was rendered is evidence:
- *Exposure.* T3 reports what is on screen, and the word model reads only a showing's annotations. The transcript
  readers on each machine count a reply with levels as it was shown when it was sent (its plan's level, else the level
  in force now, -3 while English only is on), never its full markup: the exposure scan's words and reading entries
  (`fwlib.read_replies`), the hook's last reply and its topical turns (`twins.view`).
- *The join.* The hook's sentence record of a reply with levels keeps, never text, every element with its threshold
  and propensities, which element each glossed word belongs to, and the fingerprint of what each level shows
  (`grammar.record_levels`). The collector keeps each level a message was shown at as a showing of its own (its items
  carry the level; feedback.py stores them under `<message>@<level>`), and the join matches a showing's annotations
  against the fingerprint at its level, so it knows which sentences were on screen and where (`sentence_model`).
- *Selection.* Thresholds depend on the model's predictions, so without exploration the model would only see what it
  already predicts to be easy. `fw.py calibrate` compares expected and seen opens on the twins placed at random against
  those the model ranked, and gives the open chance over every twin written by inverse propensity. The fit itself stays
  unweighted: when selection depends only on features the model has (length, patterns, the learning words' value), the
  likelihood of the opens given the features is unchanged by it. If the random and the ranked twins disagree, the fit
  can weight each sentence by 1 / its propensity at its level (clipped, normalized), which makes it estimate the open
  chance over every twin written; or be fitted on the random ones alone as a check.
- *Another level.* A message read again at another level is another showing, not a duplicate, and a level change while
  it is on screen splits its reports (the client's later items carry the new level). A level's first report that isn't
  its shown record starts that showing, with the word count of the message's other level.
- *Presses.* The panel's arrows and English only now change what is on screen at once; they are logged as before, and
  calibrate counts them as before (English only on as a lighter press). A press of English only names the messages on
  screen, read as shown at the chat level then; their Japanese sentences are weak opens.
- *English only* is level -3 at render time: items at -3 show no Japanese and are dropped. In draft style the hook
  keeps writing twins while it is on, so turning it off (or its hour running out) shows those replies' Japanese at the
  level then, like every other message, and English only is a view, as the arrows are; at off (-3 from the arrows) or
  in a chat with Japanese off, the hook still has replies written in plain English: those mean no Japanese work.
- *Older messages* (no thresholds) are read as before, and so are clients that send no level.

**Deploying.** The collector first (it takes the level and the new record fields; an older one rejects the records and
keeps every level of a message in one row), then the T3 builds that render levels (an older build shows both twins),
then the hook.

## The experiment

Five prompts (a Bloom filter, a RefCell panic, a Kyoto weekend, TCP or UDP for a game, keeping a side project going)
were answered by Opus 5.5 at extra-high effort. Each run used a copy of the word state, under a stand-in for T3 Code
so the hook gave its real brief. Each round was judged blind by a separate Opus agent, which saw the replies shuffled
and unlabelled.

| Round | Compared | Result |
|---|---|---|
| 1 | Today's brief (A) against a draft woven by code and re-read, at the normal budget (B) | A on all five: B's tool offered 50 to 90 unsure swaps a reply, and fixed English left verbs in "-ing" slots |
| 2 | A, A with new rules (A2), and B with every candidate and freedom to rewrite into Japanese order (B2) | B2 > A2 > A1 on all five: the rewritten sentences teach grammar; A2 halved A's errors but also its Japanese |
| 3 | B2 against B2 with the judge's fixes (B3), at full and at the normal budget | B3 first on three, B3 at normal first on two, B2 last on all five; the word pool mattered less than the rewriting |
| 4 | B3 plus a code check and nouns-only English sentences, at every level on one question | The judge recovered the level order blind. +2 was best for learning; full cost more reading than it taught. Problems per word were a quarter of B3's at the same density |
| 5 | Normal and +2 with stricter rules (no する on English verbs, glossed particles) against round 4 | +2 cleaner (3 problems in 17 glossed words against 9 and 10), normal a draw |

What the judges kept finding, and what now handles it:
- **Wrong senses on basic words:** the trap list in `DRAFT_RULES`.
- **Japanese words in English grammar slots:** nouns only in English sentences, checked with the dictionary's word
  types.
- **Words that follow their noun used as English prepositions, pronouns, "if" with no conditional ending, and
  English verbs with する:** all in `check_reply.py`.

All four ways of writing replies were faster than Claude weaving as it writes at the same effort: about 110 to 150 s a
reply against 220 to 250 s.

The user's verdict on the levels: normal is right for everyday chats, and much heavier (+2) is for studying the
words. Normal leaves part of its budget unspent on purpose.
