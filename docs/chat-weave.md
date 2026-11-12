# Chat replies: draft and weave

How chat replies in T3 Code get their Japanese since 2026-10-22, and the experiment that chose it. The code is
`scripts/chatweave.py` and `scripts/chatweave.mjs` (the weave), `scripts/check_reply.py` (the check),
`scripts/twins.py` (the levels) and `weave.compose_draft` (the brief). Since 2026-11 the Japanese sentences are also
held to the grammar the reader knows and to a length the log supports ([grammar.md](grammar.md)), and a reply carries
every level at once, so the reader's levels are applied when it is shown (below): one for the swapped words and one
for the Japanese sentences.

## The steps

1. Claude writes the reply in plain English and passes it to `fw.py weave --level N` on stdin (with `--sentences S`
   when the sentence level differs: below).
2. The weave runs the extension's pipeline on it, with no model call (`chatweave.mjs`, under node or bun):
   - `matcher.js` finds every place one of the words in use could go;
   - `senses.js` says which options are flagged safe;
   - `verify.js` `candidatesOf` and `levels.js` `planSwaps` place them as at full: every candidate, whatever the
     level, since the reply has to hold what every level shows.

   Words known at a glance go in line by line, once a line. The rest go over the whole reply, a word again where it
   fits, with at most 6 dictionary words the reader doesn't have yet (those nearest the target lookup chance), which
   show only at full. In chat a word's own glosses count as safe (`webdata.add_word_data` with `own_safe`, from
   `chatweave.words_for`), but for closed-class English, as `senses.js` treats it: kuruma is sure for "car" though
   `data/senses.tsv` flags only "automobile"; the web keeps the flags. A span still unsure (one of closed-class
   English: "now", "one", "all") is taken at its first option and marked `{{English→Japanese}}`. Inline code, link
   targets, URLs, tags and paths (`/home/a/b`, `research/net/player/`, `scripts/fw.py`: a run of non-space characters
   that starts with `/` or `~/` or has a `/` between word characters) are left alone.

   The text offers only the swaps that may stay in an English sentence by the check's rules (`check_reply.may_stay`,
   the same rule the check holds the reply to: nouns, adjectives right before an English word, adverbs known at a
   glance; not a word that follows its noun before an English word, nor one next to a number); the rest stay
   English there and go on the first line "for twins only", each Japanese word once with its short gloss (pronouns
   left out: Japanese drops them). Each swap's English, reading, current lookup chance and the English around it are
   kept in `weave-swaps.json` for the check, those for twins flagged `twin`. The first line names the learning words
   and asks for a Japanese twin of each sentence that fits the limits: at most so many Japanese characters a sentence (where known patterns reach a 5% open chance), the known
   grammar patterns plus at most one learning pattern, and this week's learning pattern with its explanation and
   example, to use on purpose in a twin or two (`chatweave.sentence_plan`). It asks for at most 1.5 times the
   sentences much heavier (+2) pays for at the price of a sentence, and at least 3: full shows every twin, but full
   costs more reading than it teaches (round 4 below) and is rarely read, so twins past what +2 uses mostly serve it;
   the slack leaves the ranking a choice and covers the twins the check drops.
3. Claude writes the reply from the output: its English sentences keeping the swaps wherever the sentence's meaning
   still gets across (a near synonym is fine), and undoing one only where the Japanese would obscure or change the
   meaning, written `{{like this}}` so the check leaves it English; and right after
   each sentence that fits, on the same line, its twin in `<ja>...</ja>`: one Japanese or mixed sentence, or two short
   ones for a long English sentence, glossed as before, each ending with a bare 。 or ？. The twin's English is the text
   back to the end of the sentence before it (a `.`, `!`, `?`, `:` or `;` after anything but a space, with any
   closing quotes, brackets or emphasis marks, then a space, abbreviations like "e.g." and a single letter's period
   (U.S., a.m.) aside; or an emoji and a space),
   the end of an earlier `</ja>` on the line, or the line's start after a list, heading or quote marker. Code puts the
   English sentence on each closing mark as its translation, so it is written once. A twin that would cut through the
   reader's markdown keeps its sentence English (`twins.twin_trouble`), and so does one whose English has an
   abbreviation or a single letter's period before a capital ("the U.S. The", "plan A. It", "Dr. Smith"), where code
   can't tell whether a sentence ends: its English holds more than one sentence, sits
   in a table row or a task list, holds a link, or leaves bold, italic, a link or code open (or starts inside one the
   line left open), or its Japanese does. Japanese may be quoted in backticks; elsewhere it goes in twins or glosses.
   The rules and the list of known sense traps, now examples of a meaning obscured, are `weave.DRAFT_RULES`.

       This is a problem.<ja><abbr title="this">これ</abbr>は<abbr title="もんだい · problem">問題</abbr>です。</ja>

4. Claude passes it to `fw.py check-reply --level N` (and `--sentences S`), which:
   - stops only on what code can't fix: a `<ja>` without its `</ja>` or its English sentence, Japanese outside twins
     and glosses, a Japanese sentence outside `<ja>`;
   - drops a twin that breaks a limit (more than two sentences, too long, grammar not on the list, two learning
     patterns in a sentence) or any rule of the old check (a pronoun, もし with no conditional, bare particles that
     carry meaning, する on an English word and the rest), or that says less than its English sentence (a gist: more
     than two of the sentence's content words, and over half of them, nowhere in the twin's English words or its
     glosses' meanings, `check_reply.says_less`; on 401 twins of 2026-11-04 to 11-12 that was 178, such as a bare
     "shall I do it?" for a sentence naming what to delete and build): its sentence stays English;
   - puts back in Japanese a sure swap the reply dropped (`check_reply.put_back`): one the weave offered in its text,
     not unsure, not for twins only and not a dictionary word, that no glossed word of the reply is, whose English the
     reply still has in its own English with at least 4 of the draft's words around it (6 a side, at least one on each
     side the draft has), outside twins (their English and Japanese), code, link targets, URLs, tags, paths and
     `{{...}}`; a Japanese noun never goes back where the English may be a verb (after "it", "to", a modal and the
     like; an -s form with no determiner, number, quantifier or preposition before it; a sentence's first word before
     an object). Each goes in as the weave's abbr and is held to the rules below like the reply's own words, which come
     first for the repeat limit; one the rules send back to English is neither counted nor listed. The summary line
     says "N put back" and a line lists them;
   - puts back in English a swap that breaks the stays-English rules (only nouns, adjectives right before a noun, and
     adverbs the reader knows at a glance, below), and a content word's fifth gloss;
   - scores each twin (the sentence model, per Japanese sentence) and each swap (its lookup chance from the weave;
     each kept swap is the draft's swap of the same word and gloss whose English around it, kept by the weave,
     overlaps most with the reply's, so a word offered for two senses keeps each sense's English even when Claude puts
     an earlier one back; with no clear match its English is the gloss's meaning). A twin's words the weave didn't offer
     are priced as the weave prices its swaps, from the same word list and on the same scale, while a content word not
     on that list or flagged a dictionary word, like a dictionary word, makes the twin show only at full (a learning
     word's later use: from heavier); glossed grammar costs nothing. It then
     decides what each level of each dial shows (thresholds and propensities, below), writes the final markup with
     each element's predicted chance (`data-p`, the tint), checks that it reads back to the same elements, thresholds
     and chances and that every pair of a word level and a sentence level renders clean, and keeps the plan in
     `reply-plans.json` for the hook's sentence record;
   - prints what it dropped and put back, each level's expected sentence opens and word lookups against its two parts
     of the budget (saying where each dial is now), and the final markup under a line of `=====`.

   Claude may fix a dropped twin and check once more (at most twice in all), then sends everything below the `=====`
   line exactly as it is.

The full steps go out on a chat's first turn and every 20 turns or after a compaction (and once to a chat that had the
steps from before twins); other turns carry a short digest. While English only is on (T3 Code, an hour) the hook
keeps the steps, with a line that the reply shows English until English only is off (below); while chat intensity is
off (in draft style both dials: with only the word level off the twins still show at the sentence level), or in a
chat with Japanese off, it sends a one-line note to reply in plain English and `fw.py weave` refuses with both dials at
off. Draft and weave needs `<abbr>` glosses (T3 Code with `fw.py glosses abbr`) and node or bun. It is off in Codex
and terminals, and with `fw.py chat-style classic`, which bring back the brief in which Claude weaves as it writes
(and the plain-English note under English only). The calibration line and topical words belong to the classic brief:
in draft style the weave and the check set the amounts. Brief records carry `mode: draft`.

## Levels after the fact

The contract with the T3 clients (desktop and phone) is the markup: a swapped word in English text is `<abbr
title="reading · meaning" data-en="original English" data-on="N" data-p="P">JAPANESE</abbr>`, shown as that gloss at
word levels N and up and as its English below; a twinned sentence is `<span data-jw="ja" data-on="N"
data-p="P">...</span><span data-jw="en" data-on="N">...</span>`, the Japanese from sentence level N up and the English
below. N is in -2..3 (3: full only). Anything without `data-on` or `data-jw` renders as before, so older messages are
unchanged. Malformed markup renders by one rule on both clients, each element compared to its own kind's dial, which
`twins.render` mirrors so the fingerprints agree: a Japanese span pairs with an English one right after it (whitespace
between allowed) of the same threshold; a lone English span always shows; a lone Japanese one shows from its threshold
and below it is its last sentence mark's translation; in a broken message (a twin span left open, split by a blank
line, or inside another) every pair shows its English, and a Japanese twin left open never shows; a `data-on` that
isn't exactly -2..3 (`4`, `01`, a Unicode minus) renders as written; an empty `data-en` is the gloss's first meaning; a
self-closing `<abbr/>` or level `<span/>` is dropped; a level abbr whose own `</abbr>` (abbrs nest as in HTML) doesn't
come before the end of its block is its English at every level, in place of its tag and the run of Japanese (Han,
kana, the long-vowel mark) its text starts with, the rest of the block as it is; English only (and both dials off)
renders everything as written below every threshold and then as the clients' English-only view reads older markup:
each glossed sentence its translation, back to the previous mark, an English sentence's end, code, a tag or a line
break, and each glossed word its meaning; markup in code (inline, fenced or indented) is text. On the review's corpus
of malformed and edge cases the Python rendering's glosses match the clients' in every rendering where the two clients
agree (code shown literally aside). A sentence record is made only for well-formed markup (which also means every
gloss in a twin's English has a threshold). Each report item T3 sends carries the levels the message was rendered at,
and the annotations and words it reports come from the screen, so a hidden word is never reported as shown.

**Two dials.** The reader sets two levels, each -3..3 with the same steps, the same 3-hour expiry back to normal and
presses logged the same way (`intensity.py`): the *word level* (the collector's surface `chat`, the chat level from
before) decides every level abbr, and the *sentence level* (surface `sentences`) every twin. `GET
/v1/intensity?surface=chat` returns the sentence level as `sentences` next to `intensity` and `english`; a collector
without it means the sentence level is the word level (one dial). The two are independent: what words show doesn't
depend on the sentence level, nor the reverse; a glossed word inside a shown twin's Japanese is part of the twin, and a
swap in a twin's English shows by the word level while the twin is English. At -3 a dial's kind shows no Japanese;
English only stays one switch over both, rendering both as -3. Until the reader first sets the sentence level (no
`intensity_sentences` pref yet), it follows the word level, in the collector's answers and in the hook alike: the
first experience is one dial, and an off spell from before the sentence level keeps the sentences off too. Off still
means no Japanese: `fw.py intensity off` (and the skill's off), `normal` and `full` without a dial named, and saying
"japanese back on", set both dials (a dial that follows the words just follows them, so it stays one dial), while
`fw.py intensity chat off` or `sentences off` turns one kind off. `fw.py intensity sentences off always` keeps the
sentences off until changed (until `intensity.KEEP`), and the hook then asks for no twins at all. `fw.py intensity sentences up` and the like step the
sentence level, and `fw.py intensity` shows both. The hook passes the live levels to the commands it asks Claude to
run (`--level W --sentences S`; `--level` alone, as in older briefs, means both dials at it), so the weave and the
check don't read the levels from a cache that may lag the collector; the hook still writes twins while only the word
level is off, and the weave refuses only with both off. Why:
with one dial the sentences took the whole sentences' part at every level while the stays-English rules kept most
known words out (27 known words offered in one reply, 4 kept; normal spent 0.07 of a 0.64 word budget), so a reader
saw too many Japanese sentences and too few known words.

**Thresholds.** One order per reply and kind, cut at each budgeted level's lookup budget (`intensity.budget` per 100 of
the reply's English words), each kind from its own part of it: twins from the sentences' part (`SENTENCE_SHARE`) at
each sentence level, each at its cost: each Japanese sentence at its predicted open chance but never less than the
price of a sentence (so no level asks for more Japanese sentences than the count before the sentence model did), plus
the costly words in its Japanese; words from the rest at each word level, at their current lookup chance, a word in a
twin's English counted as if its twin were English. Levels are filled from -2 up, each starting from the level below,
so each kind's sets are nested; at most the level's costly words in one English sentence and one use of each learning
word below heavier. The element at the edge of a part gets in with the chance (what is left) / (its cost), one uniform
draw per reply and part, so a level's expected spend is its part and the sets stay nested. Words known at a glance
cost nothing, so the budget can't ration them: much lighter shows 35% of them and lighter 65%, in a random order per
reply, and normal and up show them all (`intensity.FREE_SHARE`; before 2026-11-12 every level showed them all, and the
word dial moved only the few learning words: 18 words shown at much lighter and 20 at much heavier, over 150
replies). A learning word's later uses show from heavier, and so does a twin holding one; dictionary words only at
full, and so does a twin holding one. The
order is by value per expected open: for a twin its own value, the learning values of the costly words in its
Japanese and this week's pattern, over its cost; for a word its learning value (1 at the target chance of 25%) over
its chance. The rules that only made sense with one dial are gone: a twin shown no longer hides its English's swaps
from the budget, and those swaps are no longer held at or below the twin's threshold.

**The tint.** check-reply writes `data-p`, the predicted chance the reader looks it up (0..1, at most two decimals), on
every level abbr (the weave's lookup chance), on each twin's Japanese span (the chance its translation is opened: 1 -
the chance none of its sentences' is) and on the glossed words in a twin's Japanese whose chance it has (the reply's
or the weave's swaps, or the weave's word list); never on a closing mark or an English span. The clients, with the
tint on (a per-device setting, on by default), give each shown Japanese element with a valid `data-p` a faint
background from green (0) through yellow to red (0.5 and up), a twin's span lighter than its words'; an older message
has none and gets no tint. The tint changes nothing in reports.

**Known words in English sentences.** An English sentence keeps the swaps that are nouns, adjectives right before a
noun and, since the two dials, adverbs the reader knows at a glance (the weave's free swaps), time words used as adverbs
among them (now, already, soon, still, first: the dictionary has them as adverbs or as nouns). Verbs, pronouns and
adverbs still being learned stay out, and so do connectives and question words whatever else the dictionary types them
as (`check_reply.connective`): a few spellings (もし, でも, そして and the like), any word typed as a conjunction unless
the English it stands in for is "again" (また), the question words of the ど- series, and any swap for "and", "but",
"so", "if", "however", "moreover", "also", "therefore", "then", "instead", "otherwise", "unless", "in case", "in other
words", "how", "why", "what", "when", "where" and the like ("so" stays as an intensifier for an adverb with no
conjunction type, as とても; tsumari is a connective by spelling). The brief asks Claude to keep, where the English reads
as naturally either way, a phrasing that leaves a swapped known noun or adjective in place. One rule
(`check_reply.english_problem`) serves the check and the weave's offers. A word's types are those of the entries
written that way, else of those it is the reading or another spelling of; a word that is also a verb is never an
adjective before a noun (kuru and aru are also adnominals, as kitaru and "a certain"), and an interjection is a
noun only as a whole noun, not as an affix (nee, a reading of ane, "older sister").

**Offers and put-backs (2026-11).** Of 22 woven replies (about 8,000 English words) after the two dials, the weave
offered 1,259 swaps and 132 survived. Most offers could never stay in an English sentence (pronouns, connectives,
question words, interjections, verbs, adverbs still being learned), and almost every known-word swap was marked unsure
(294 of the reader's 361 known words had no gloss flagged safe), so writers kept about 10 of 140 and dropped good
nouns with them. The weave now offers in its text only what may stay, lists the rest for twins, treats a word's own
glosses as safe, and the brief asks for a looser sense standard; check-reply puts back what was dropped by mistake.
Re-run over the same 22 drafts with the word state of 2026-11-10: 1,851 swaps found (1,841 before), 765 offered in the
text (all 1,841 before) and 305 of them unsure (1,634 before), the other 1,086 listed for twins; re-checking the
writers' final replies, written under the old brief, puts back 178 swaps, and their word swaps go from 156 to 347
(108 to 287 shown at normal, 1.1 to 3.1 per 100 of the drafts' words as the weave counts them). Some put-backs are
still wrong in sense (mise, "shop", for a data "store"; hou, "side", in "side effects"): those replies' writers had
dropped them on purpose under the old brief, where now they would write `{{store}}`. With it, eigo ("English / English
language") joined the word list, and nihongo's gloss is "Japanese / Japanese language", so plain "Japanese" matches,
as a noun and before one; the weave's paths stopped taking swaps ("/home/..." was the word for house).

**Exploration and propensities.** Each element's place in its order is, with chance 0.2 (`twins.EXPLORE`), a uniform
random place instead of its rank. So every element has a known chance of showing at each level, never 0 or 1 by the
model's predictions alone, and the elements placed at random are a sample the model didn't choose. An element's
propensity at a level of its own kind (a twin's at a sentence level, a word's at a word level, a word in a twin's
English while the twin is English) is the share of 1,000 seeded runs of the same randomization in which it shows
there (within about 0.03 of the exact chance), kept with its threshold, its prediction and whether its place was
random.

**What the reading model sees.** Only what was rendered is evidence:
- *Exposure.* T3 reports what is on screen, and the word model reads only a showing's annotations. The transcript
  readers on each machine count a reply with levels at the levels it is read at (its plan's word and sentence levels,
  else the chat's two levels in force now; English only doesn't count, since the reply shows its Japanese once that is
  off), never its full markup: the exposure scan's words and reading entries (`fwlib.read_replies`), the hook's last
  reply and its topical turns (`twins.view`).
- *The join.* The hook's sentence record of a reply with levels keeps, never text, every element with its threshold
  and propensities, which element each glossed word belongs to, and the fingerprint of what each of the 49 pairs of a
  word level and a sentence level (-3..3 each) shows (`grammar.record_levels`, `pf`; a record from before the two dials
  has `fps`, each level -2..3 with both dials at it, and joins those equal pairs; the scanner's version went up, so the
  scan sends every reply's record again with the pairs). The collector keeps each pair a message was shown at as a
  showing of its own (its items carry `level`, the word level, and `sentenceLevel`; feedback.py stores them under
  `<message>@<level>` when the two are equal, else `<message>@<level>/<sentenceLevel>`), and the join matches a
  showing's annotations against the fingerprint at its pair, so it knows which sentences were on screen and where
  (`sentence_model`); a sentence unit keeps the sentence level, its twin's threshold and propensity there. When the
  reader opens a twin's translation, only that twin's own words count as read in English (`knowledge._in_english`,
  from the record's elements): the English sentences around it, and their swaps, were read as they are.
- *Selection.* Thresholds depend on the model's predictions, so without exploration the model would only see what it
  already predicts to be easy. `fw.py calibrate` compares expected and seen opens on the twins placed at random against
  those the model ranked, and gives the open chance over every twin written by inverse propensity. The fit itself stays
  unweighted: when selection depends only on features the model has (length, patterns, the learning words' value), the
  likelihood of the opens given the features is unchanged by it. If the random and the ranked twins disagree, the fit
  can weight each sentence by 1 / its propensity at its level (clipped, normalized), which makes it estimate the open
  chance over every twin written; or be fitted on the random ones alone as a check.
- *Another level.* A message read again at another pair of levels is another showing, not a duplicate, and a change of
  either dial while it is on screen splits its reports (the client's later items carry the new levels). The client
  sends a shown record for each pair a message with level markup is shown at, with that pair's word count; a pair's
  first report that isn't its shown record starts that showing with the word count of another pair until it comes.
- *Presses.* The panel's arrows and English only now change what is on screen at once; they are logged as before, and
  calibrate counts each dial's presses on its own line (English only on as a lighter press of the words). A press of
  English only names the messages on screen, read as shown at the chat's two levels then (before the sentence level's
  first change in the log, at the word level with both dials at it); their Japanese sentences are weak opens.
- *English only* is both dials at -3 at render time: items at (-3, -3) show no Japanese and are dropped, but for a
  shown record, kept with no annotations and never a showing, so a message first shown in English gets its word count
  for the pairs it is read at later; an item with one dial at -3 still showed the other kind's Japanese and is kept. In
  draft style the hook keeps writing twins while English only is on, so turning it off (or its hour running out) shows
  those replies' Japanese at the levels then, like every other message, and English only is a view, as the arrows are;
  with both dials off, or in a chat with Japanese off, the hook still has replies written in plain English: those mean
  no Japanese work. A message's budget in calibrate's lookups is the words' part of its word level's budget and the
  sentences' part of its sentence level's.
- *Older messages* (no thresholds) are read as before: clients send levels only for messages with level markup, and
  an item without one is stored as it always was, one showing per message.

**Deploying.** The collector first (it takes `sentenceLevel`, the `sentences` surface and the records with pair
fingerprints; older clients are unaffected), then the T3 builds with two dials, the tint and the new reports (with an
older collector they read the sentence level as the word level), then the hook (`data-p`, budgets per kind, the
relaxed rule). Each step works with the others still on the old version: an older client shows a message with `data-p`
as before, and an older hook's replies have no tint.

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
- **Japanese words in English grammar slots:** nouns only in English sentences (and adjectives right before a noun;
  since the two dials also adverbs the reader knows at a glance), checked with the dictionary's word types.
- **Words that follow their noun used as English prepositions, pronouns, "if" with no conditional ending, and
  English verbs with する:** all in `check_reply.py`.

All four ways of writing replies were faster than Claude weaving as it writes at the same effort: about 110 to 150 s a
reply against 220 to 250 s.

The user's verdict on the levels: normal is right for everyday chats, and much heavier (+2) is for studying the
words. Normal leaves part of its budget unspent on purpose.
