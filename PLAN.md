# The plan, from the goal back to the code

Status: 2026-10-21. Revised on 2026-10-20 after an adversarial review by three independent reviewers (the model's
assumptions, the design's simplicity, and a simulated reader with known ground truth), then implemented on the
integration branch `plan`; sections 4 to 9 say what was built and where results departed from the plan. Why the system
works the way it does, where it is going, and why each shortcut is acceptable. What to do next, in order:
[NEXT_STEPS.md](NEXT_STEPS.md). Background and the longer argument:
[docs/learning-framework.md](docs/learning-framework.md). How the current model works in detail:
[docs/adaptive-difficulty.md](docs/adaptive-difficulty.md). How the web prompt was tuned:
[docs/swap-prompt.md](docs/swap-prompt.md).

Parts of this are built and parts are planned; section 9 says which.

## In one minute

- **Goal:** you understand more Japanese month after month, starting with the words your reading uses most.
- **The real limit is annoyance.** The tool only teaches while it is on, and annoying it gets turned off.
- **Annoyance is mostly interruptions,** and an interruption is a lookup. So each intensity level is a **lookup
  budget**: lookups per 100 words read, one unit used everywhere.
- **Words you know at a glance are free.** They appear wherever they fit. The budget goes to the words that teach most:
  ones you almost know.
- **One fitted model guesses each word's lookup chance** from your history and from what the word is like, refitted as
  a whole on every refresh. The planner uses those guesses.
- **Spacing comes from forgetting.** A word's chance rises with the time since you last read it, so aiming near 25%
  brings it back as it starts to slip. Whether a word counts as known is judged on its long-term chance, never on how
  recently you saw it.
- **The vocabulary is the dictionary, and your reading nominates from it.** Words that keep turning up in what you read
  come in, a few a day.
- **Code finds the places; a model checks them.** On the web, code finds every spot a word could go. A model only
  settles doubtful senses and writes whole sentences in Japanese.
- **Everything is checked against ground truth where one exists:** a simulated reader for every model change, the
  real log replayed for every model comparison, two randomized experiments, and a short monthly probe for the goal.
- **Everything else is a simplification,** listed in section 8 with why it is fine and how we would notice if not.

## 1. The goal

**Understanding over months**, measured as: the share of the words in what you read that you would read in Japanese
without a lookup, each word weighted by how often it comes up in your reading.

It is measured two ways:
- **From the model**, continuously. This number is circular: if you simply get used to the glosses and look up less,
  or the model is refitted, it moves without any learning.
- **By a probe**, monthly: about 60 words sampled by how often your reading uses them, each marked known or not, plus
  1 in 10 made-up words to catch over-claiming. It takes about 3 minutes and has a standard error of about 6 points.
  It is the check on the model's number, and it also measures how often you look up a word you don't know (row R).

Two things multiply into the goal:

- **Learning per hour on:** the right words, at the right difficulty.
- **Hours on:** zero if you turn it off.

The second one is fragile, so it sets the rules: maximise learning per hour **while annoyance stays under your
tolerance**.

## 2. What actually happens

One reading, step by step. **Bold** steps are logged; the rest we can only infer.

1. **Japanese gets written.** In chat, Claude writes the reply, following the hook's brief. On the web, Haiku offers
   candidate swaps today and code picks which to show; planned, code finds the candidates itself (section 6).
2. **The text is on your screen.** Logged: which paragraphs, for how long, and whether the window had focus.
3. Your eyes pass over a word, or skip it. Not logged.
4. You recognise it, guess it from context, or don't. Not logged.
5. **You hover or click to see the gloss** (a lookup), and how long it stays open.
6. **Mouse paths, selections and clicks** inside the text.
7. **You press lighter, heavier, off or full** (the keys), or say "no japanese" in a chat.
8. Days later the word comes back, and you know it a little better or a little worse. Not logged directly; it shows up
   in the next lookups.

**Observed:** what was shown, time on screen, lookups, mouse, presses.
**Hidden:** whether you read each word, whether you knew it, and how much patience you have right now.

## 3. The hidden variables we assume

| Hidden | Meaning | Changes over |
|---|---|---|
| **knowledge of a word** (k) | how likely you are to recognise it at a glance, long term, one number per word | weeks |
| **recency** | how much of that has faded since you last read the word | hours to weeks |
| **read or not** | whether your eyes covered that spot | seconds |
| **lookup habit** (c) | of the words you don't recognise, the share you look up | weeks |
| **tolerance** | how many lookups per 100 words before it becomes annoying | minutes to hours |

Later, maybe: how guessable a word is from its sentence, knowledge of kanji shared between words, and knowledge of
grammar patterns.

## 4. How the hidden variables produce what we see

- **Lookup chance at a spot** = chance you read it × chance you look it up if read.
- **Chance you look it up if read**: its long-term chance is sigmoid(surface offset − k); the recency term below
  lowers it for a while after the word was read. The lookup habit c is folded into k and the offset (row R); the
  probe measures it separately.
- **Starting value:** a word with no history starts from a fitted prior (built 2026-10-21):
  - an intercept by the word's origin (how it came into use: taken in, known from setup or Anki, or new), never by
    its label, since the labels now come from the model;
  - the uncapped Anki interval: the old formula stopped rising at 21 days, but your lookups keep falling past it
    (7.6% a read for intervals of 20 to 57 days, 5.0% for 58 to 89, 2.2% beyond);
  - for new and learning words, the word itself: frequency rank, the share of its kanji in words you know (from the
    previous refresh's free words that have their own evidence, never the word's own), script, word type, length,
    and whether it is rare or a loanword, with coefficients held toward 0;
  - probe answers enter the fit as observations of their own kind, with their own offset, so the monthly probe also
    teaches the prior about rare words. In simulation they cut the starting error at ranks past 3,000 from about
    0.7 to 0.6 in log-odds; most of the gap remains.
- **Chance you read it** comes from time on screen against reading pace (300 ms a word). It is certain above anything
  you looked up further down. The pace and cap are refitted from chat and web rows, but kept at their defaults when
  the best pair lies on the search grid's edge, as it does on the live web log. A reading model per surface is built
  but off (`SURFACE_TUNING`): on the snapshot it would fix the web's over-prediction (one evening's page holds 20 of
  the web's 51 interactions), but it moves every word's scale (366 free words would become 68), which 5 days of web
  data can't justify.
- **How it is fitted: one fitter** (built 2026-10-21). On each refresh, a penalized maximum-likelihood fit of the
  whole log. Each word's k is held toward its starting value by a prior, not a pull, and the shared parameters are
  fitted alongside: the chat offset (the web's is 0, so the prior sets the level of k), the reading model, the
  recency term, the prior regression and the probe offset. Old evidence counts less (30-day half-life on its weight).
  `known` and `learn` are strong one-sided pseudo-observations. A lookup of a conjugated form counts 0.3 toward its
  dictionary word (on the snapshot forms are opened twice as often as the same words written plainly). The curvature
  of the fit gives each word's uncertainty. A refresh takes about 0.3 s, and a few seconds once a day.
  - Before: online updates with a step size, and between observations k was pulled back toward its starting value.
    In simulation that pull held a word whose true chance is 5% at about 11% at 3 showings a day, and the step size
    alone needed hundreds of exposures. The fit closes 57% of the gap to the true chances that the old model left.
- **Recency, in two stages:**
  - Built: a shared term with two fitted parameters, a floor and a context offset. Right after a read, a word's odds
    of a lookup drop to the floor (or stay at its long-term odds if those are lower) and climb back linearly over a
    week. The clock weighs each earlier showing by the chance it was the last one read, given what happened there
    (an open means it was read). A repeat in the same page, or the same T3 window within the hour, gets the context
    offset, so easy repeats don't read as forgetting. The floor's prior sits above every word, so the term is off
    until your own log shows short-term forgetting; on the snapshot it is off: words you looked up are looked up
    again hours later about as often as the first time (12 of 76 showings at 6 to 24 hours, 20 of 137 first
    showings). In simulation it recovers the true decay within a factor of 2 and beats the fit without it on 25 to 26
    of 28 days.
  - Later: a strength per word on an FSRS-shaped curve (Anki's scheduler; also half-life regression, Settles and
    Meeder 2016), where reading after a longer gap strengthens memory more. This waits until the log covers 60 days
    and the exposure experiment shows that exposure teaches; `since_read` marks its place. Until then the data can't
    identify it: the log's longest within-word gap is about 5 days.
- **Anki gives the starting state, not the rates.** Your reviews ended in June, four months before the log began, so
  nothing yet ties Anki's forgetting rates to reading.
- **Annoyance:** you press lighter or turn it off when recent lookups go past your tolerance. Today the presses are
  only counted (`fw.py calibrate`), not modelled.

## 5. The decision

**One unit everywhere: lookups per 100 words read.** Planning assumes the passage will be read; checking weighs each
shown word by its chance of having been read. The planner, the chat feedback line and `calibrate` all use this one
definition (`knowledge.per_100`), and the daily controller, which counted free words per word shown, is gone.

For every candidate:

1. **Known at a glance: free.** Show it wherever it fits. This is judged on the long-term chance (the chance after a
   week without the word), not the current one: right after you read a word its current chance is near zero, and that
   must not make a learning word free. A word leaves the free set only when the low end of its uncertainty passes 8%,
   and rejoins when the high end falls below it, so one lookup doesn't flip it.
2. **Otherwise it costs its current chance.** Spend the budget on these, best first: the current chance closest to 25%.
   With the recency term this is also the spacing rule. A word read an hour ago is far below 25% and waits; one unread
   for a while drifts up and comes first. A costly word waits even with budget left while recency holds it under 10%
   and its long-term chance is at least 10% (the second condition keeps a word in the free line's hysteresis band
   from waiting forever). No separate review schedule is needed, and on the web none could be kept, since a word
   appears only where a page says it.
3. **A whole sentence in Japanese costs its chance of a translation lookup**, fitted per surface (0.109 a sentence
   read in chat on the snapshot; 0.08 until a surface has data; before, sentences were free, yet they caused 16% of
   chat lookups). One rule on both surfaces: sentences may take up to 18% of the budget, learning words the rest, and
   what one can't use goes to the other; 18% reproduces the old Stage 3 density of about one sentence in five at
   normal. Stage no longer gates sentences.
4. **At most one costly word per sentence** (two at heavier levels), so no sentence is wall-to-wall unknowns.
5. **Explore a little:** rank on a random draw from each word's uncertainty, so words we know little about still get
   shown sometimes.

Budgets per level, in lookups per 100 words read: 0.15, 0.3, 0.5 (normal), 0.8 and 1.2. `challenge` sets normal's
budget directly; the daily controller that steered it is gone, since it couldn't tell a miscalibrated model from a
thin candidate pool. Off shows nothing. Full is the same path with no budget, no per-sentence cap and sentences free,
in chat as on the web.

**One planner, two executors** (deferred). The refresh would rank and price every word once, for both surfaces: code
carries out the plan on the web, Claude in chat. Today the two planners are separate code that share their rules and
constants (the free flag, current chances, the sentence share and price, the spacing wait), each with tests. Merging
them would change exploration from a draw per passage to a draw per word per refresh; it is worth doing only if the
live check shows the two disagree in a way that costs.

## 6. Where words and candidates come from

### The vocabulary

**Before:** a curated list of 1,500 common words (`data/lexicon.csv`) plus your Anki words, in a fixed order. New
words came off the front of the queue, at most 5 a day into 20 learning slots, or sooner if you looked one up in two
messages. Words outside the list couldn't be taught: chat could not use them, and lookups of them went nowhere. Two
systems decided what "learning" meant: day-count rules inherited from French Weave, and the model's estimates.

**Built (2026-10-21):**

- **The dictionary is the vocabulary.** `data/dictionary.tsv`: every common JMdict entry plus frequent others, 26,969
  in all, with JPDB and BCCWJ frequency ranks, built reproducibly from pinned sources by `tools/build_dictionary.py`
  without the word-by-word review (docs/dictionary.md). 99.8% of the state's words and 39 of the 42 words you looked
  up outside the old list resolve to an entry. The word state keeps only words with some history.
- **Your reading nominates words; a few a day get in.**
  - Counts per dictionary entry and day (counts only, never text): in chat from Claude's English, through the
    matcher, counting only matches the sense flags call safe, and from Claude's Japanese; on the web from passages
    read, counting safe matches and those the model confirmed.
  - A word is eligible after coming up on 3 different days in 30. At most 5 join a day, and only while the budget
    goes unspent (logged plans spent under 80% of it over the last 48 hours); words you looked up come first, then
    by days, by predicted starting chance (nearest 25% first) and by frequency.
  - In a simulation of one reader's text, the ranking and the intake rate decide whether words come back, not the
    threshold. Admitting every eligible word (40 to 60 a day) gave a median of 0 or 1 exposures in two weeks. Five a
    day, ranked, gave a median of 11 to 22, and 80 to 100% reached 8. This gets harder after a few thousand words,
    once the frequent ones are used up. On your real chats, about 8 new words a day become eligible.
- **No slots, and status from the model.** Known means free (rule 1 in section 5); learning means taken in and not
  free. `known` and `learn` stay as strong evidence. Gone: the day-count rules and the 20 slots, the evidence moves,
  the rule that showed a new word first outside the budget, and the chat recall cue. On the snapshot the switch
  changes 73 of 1,766 labels (`fw.py relabel --dry-run` lists them): 49 Anki cards with 11 to 19 day intervals and
  all 21 shaky words become known, 3 go the other way. `fw.py relabel --restore` undoes it for 30 days.

Why this holds together: the goal weighs words by your reading, so the words your reading repeats are the ones worth
learning, and they come back on their own, which repeated exposure needs. It is also what worked in practice: the
words Claude used outside the list in chat were topical and recurring, and you liked them.

### Candidates on the web

**Before:** Haiku proposed swaps, and code validated and picked. Haiku is timid. On the prompt lab's hand-judged
passages it covers 51% of the places one of your words fits at normal, and 80% when asked for everything, with 2.5% to
2.7% wrong senses. A 27,000-word list wouldn't fit in its prompt either.

**Built (2026-10-21):**

- **Code finds every place.** An index from English (lemmas, irregular forms, phrases like "there is") to dictionary
  entries marks every span a word could take, with its options. On the lab's 79 judged passages, naive matching
  against today's words already covers 83% of the places, instantly and at no cost.
- **A model settles the doubtful ones.** The same naive matching takes the wrong sense at 30% of the judged places it
  hits: 39% for function words ("about", "so", "one"), 17% for content words ("power", "rest"). So each entry carries
  a flag, set once offline: safe (code swaps it alone) or doubtful. Doubtful spans go to Haiku, which picks the right
  option or none. With a large dictionary this is the common case: among 4,000 entries, 28% of English words already
  map to two or more Japanese words. The flags (`data/senses.tsv`) cover all 17,210 swappable entries: a pair is safe
  only when three model passes agree, 18% of 59,061 pairs.
- **The same call writes whole sentences.** Code finds the sentences and phrases dense enough in words you know to be
  said in Japanese; Haiku writes those, and code validates every word, as before, now with general rules for clause
  joiners, contractions, living things, spans of time, a negated "anything" and the pronoun "one".
- **Every passage needs the call after all:** nearly every passage has a closed-class word ("I", "this", "and"),
  which is always doubtful. The safe swaps show at once (under a second, where the old pipeline showed nothing until
  the model answered, about 20 s); the call adds the rest, painting passages not yet seen and only adding to those on
  screen.
- **Measured honestly:** in the lab the pipeline reached 85% coverage with 0.8% wrong senses, but its rules were
  written after reading that set's errors. On 30 fresh passages judged before any run it had 4.3% to 6.1% wrong (today's
  Haiku: 7.8% at full, so those passages were harder too). After general fixes, on a second fresh set of 34 judged
  before any run: 83% coverage and 0.6% wrong at full, against today's Haiku at 73% and 3.5%, at about the same cost
  ($0.49 against $0.51 to $0.67 per 1,000 passages). The bar is therefore relative: no worse than today's Haiku on
  coverage and wrong senses, on text judged before any run.
- **One candidate set serves every level,** full included. The separate full prompt, its cache, the per-passage target
  and the density cap go.
- Because code builds the full candidate list, each passage can log its candidates and each one's chance of being
  shown, which evaluating other choosers needs (docs/learning-framework.md, section 4).

### Chat

**Before:** the brief described the known words as a band ("roughly the 319 most common words"), listed learning words
with odds, and allowed no other Japanese. The band was wrong: it was the first 319 words of your list, which the Anki
sync reordered, so most of it wasn't what a model thinks of as common.

**Built (2026-10-21):**
- The brief names the known words on a chat's first turn (about 8,000 characters, capped so the first turn stays
  under Claude Code's 10,000-character limit for hook text), then a short digest and only changes; the full list
  again after a compaction or every 20 turns. Default swaps are known connectors and adverbs.
- It lists the costly words with odds at their current chance.
- It lists up to 5 **topical words found by code** in the last 3 turns, priced like learning words: safe-flagged
  matches first, and in a technical chat a doubtful match only when the word's first sense is technical. On 40 real
  briefs, 19% of offers were the wrong sense (41% under the first rule). None in health, legal or money chats, and
  never a medical, legal or financial entry.
- A topical word that keeps coming up is nominated like any other.

## 7. Experiments

Two effects the model can't check on its own get randomized tests.

- **Thinning tests what free words cost** (row F). Each passage keeps a random share of its free words, from 0.2 to 1.
  Today's range of 0.5 to 1 leaves too small a contrast to read: 5.3% against 6.5% Japanese. Thinning no longer scales
  the budget, so costly words are untouched and the comparison isolates free words. The outcome is reading time per
  word, and lookups. Only normal thins since extension 0.11: `knowledge.py` calibrate reads normal's passages alone, so
  +1 and +2 keep every free word (docs/swap-prompt.md, "Heavier than normal").
- **Delayed start tests whether exposure teaches** (row E).
  - A random half of new words is held back, appearing nowhere (chat topical words included) for 3 weeks. The other
    half is shown normally for 2 weeks, then paused for 1.
  - At week 3 both halves are probed: each word's next showing is guaranteed. The outcome is whether it was looked up,
    with its read chance as a covariate, compared with a logistic regression.
  - In the review's simulation this had 73% to 95% power at 80 words a half, at 4.6 showings a day. At the
    showings a word actually gets (about 2 a day) it is 33% to 91%, depending on how much a reading teaches. 80
    words a half take about 5 weeks of intake at 5 new words a day; the readout comes about 8 weeks after deployment
    (`fw.py experiment`).
  - The earlier design, 0.5 or 1.5 times the exposure read off the model, can't work. More exposure means shorter
    gaps, so it "finds" an effect when there is none (100% false positives in simulation). The true difference is
    also too small to detect in 2 months. As written it was also inverted: the planner ranks by a negative value, so
    multiplying it by 1.5 ranked a word lower.
  - Each word's arm is logged in every plan record and every chat brief record.

## 8. Simplifications, and why each is fine for now

| # | We assume | Why that is likely fine | We'd notice it failing when |
|---|---|---|---|
| A | "Didn't know it" and "bothered to look it up" are one chance. | Lookups are the cost we budget, so their chance is what the planner needs. | Words rated known get looked up whenever the context changes. Seen on 2026-10-20 for common verbs: a lookup of a conjugated form (often about the ending) was charged in full to the base word, so `する` and `行く` left the free set after one lookup each. Fixed: forms count 0.3, they are scored as their own class, and the free line has hysteresis; both words are free again. |
| B | Whether you read a spot comes from screen time at a fixed pace (300 ms a word, at most 70%). | A wrong pace scales every word alike, and the fitted offsets absorb that. | Predicted and seen lookups drift apart by position in the passage. **Weak:** the pace is now fitted on chat and web rows, but on the live web log its best value lies off the grid, so the defaults stay. A reading model per surface is built and off (`SURFACE_TUNING`) until weeks of web data can justify rescaling every word. |
| C | Tolerance is one number per level, which you set with the keys. | The keys are a direct tolerance signal, and about 5 presses a day can't fit anything richer. | Presses cluster by site, task or time of day. Then tolerance gets its own model. |
| D | One long-term number per word, plus a recency term shared by all words (built; off until the log shows it). | With small data, simple models match deep ones (Gervet et al. 2020). We have about 200 chat lookups a week, and shared terms need few. | A per-word strength beats it on the replay, by day, on learning words. Not before 60 days of log. |
| E | Being shown a word near a 25% lookup chance teaches it. | It is the usual sweet spot: hard enough to teach, easy enough to read. | The delayed-start experiment shows no difference at week 3 (section 7). |
| F | Known-at-a-glance words cost nothing. | They are the bulk of the reading practice, and you said known words inside English sentences are an easy switch. A free word you do look up stops being free once the evidence is clear. | Lookups on free words grow past the budget itself, or thinning shows free words slow reading a lot. So far: about a third of chat lookups on listed words (0.05 per 100 words), about nine in ten on the web (0.08 per 100 words). |
| G | Words don't affect each other, apart from the one-per-sentence cap. | The cap removes the worst case, two unknowns in one sentence. The data is too thin for interaction terms. | Sentences with two costly words get more lookups than predicted. |
| H | Words with no history start from a fitted prior: their origin, the uncapped Anki interval, and for new words their features (rank, kanji known, script, type, length). | It beats a flat start on first exposures in simulation, and probe answers add evidence across ranks. | Rare words still start too well known (about 0.6 in log-odds at ranks past 3,000 in simulation), so first lookups of rare words outnumber predictions. Then widen the coefficients' prior or weigh probe answers more. |
| I | Every chat reply counts, opened or not, from T3's word counts. | That removes the bias of counting only replies with a lookup. T3 vendored.9 sends them since 2026-10-21; the 123 earlier replies are a backfill (paragraph counts estimated from words per pixel). | Live T3 stays stop carrying word counts (a T3 update without the patches). |
| J | In chat, Claude follows the brief roughly, not exactly. | Each reply gets a feedback line, so the budget holds on average, not per reply. | The feedback verdicts keep saying over or under. |
| K | Chat may use topical words outside your lists, found by code in the conversation and priced. | Before the ban they caused 72% of chat lookups with nobody choosing them; now each is chosen and priced first. You missed them, and they recur with the topic. | Topical words take most of the budget, or their lookups run well past prediction. Then cap them per reply. |
| L | A hover under 300 ms in chat is the mouse passing by. Under 600 ms on the web, it counts as 0.6 of a lookup. | Short hovers are mostly accidental, so partial credit is safer than a full lookup. | Short-hover rates differ a lot between words you know and don't. |
| M | Your state of mind (tired, skimming) is ignored. | The fitted surface offsets absorb the average; the rest is noise that averages out over days. Today the chat offset moves 0.02 per message and in simulation recovers a tenth of a true offset, so the one fitter is needed for this to hold. | Lookup rates swing a lot from day to day. Then add a per-day offset. |
| N | Code's matching plus a model's sense check is as accurate as the model proposing swaps itself. | Measured on text judged before any run: on the second fresh set, 83% coverage and 0.6% wrong senses against today's Haiku at 73% and 3.5%. The lab's in-sample numbers proved optimistic, so the bar is relative to today's Haiku on fresh text. | Flips back to English rise, or wrong senses climb on the next freshly judged set. |
| O | A word that keeps turning up in your reading is worth learning, and will keep turning up. | The goal weighs words by your reading. Ranked intake of a few a day keeps exposures dense. | Admitted words come up less than forecast after they join (they regress to the mean), or the set fills with narrow jargon. Then weigh general frequency more. |
| P | Every whole sentence in Japanese costs the same: the observed rate of translation lookups per sentence. | They use known words only, so what varies is mostly grammar, which isn't modelled yet. | Long or grammar-heavy sentences get looked up far more than short ones. Then price by length. |
| Q | Your Anki interval predicts reading lookups. | Live lookup rates fall with interval: 7.6%, 5.0% and 2.2% a read across the three interval bands above. | The fitted slope on the interval goes to zero as reading evidence accumulates. |
| R | A lookup is "didn't recognise it" times a lookup habit c, and c is folded into k. | The budget is in lookups, and the product is what costs. The probe measures c on its own. | Lookups fall while the probe doesn't move: you are getting used to the glosses, not learning. Then model c over time. |

## 9. Built so far, and what is left (2026-10-21)

Deployed (on `main`): levels as lookup budgets on both surfaces, known words free, one costly word per sentence; chat
replies with a word count counted whether or not anything was opened; T3 sending word counts (row I).

Built on the integration branch `plan`, tested and waiting for deployment (NEXT_STEPS.md has the step-by-step
state and whether each "done when" is met):

- the yardsticks: a simulated reader (`tools/sim`), the day-by-day evaluation harness (`fw.py evaluate`), `calibrate`
  per class in lookups per 100 words read with intervals, and a record per chat brief;
- the one fitter, with the prior by origin, word features and probe answers, the free flag with hysteresis, a fitted
  sentence price, and a shared recency term that stays off until the log shows it; no daily controller;
- labels from the model, intake from the unspent budget ranked by your reading, and the delayed-start experiment;
- the chat brief naming known words once per chat, whole sentences priced by one share rule on both surfaces, and
  topical words found by code;
- the dictionary, the matcher, sense flags, and the checked web pipeline in the extension: safe swaps show within a
  second and the model's check adds the rest;
- the monthly probe.

Left: the live check (2 to 4 weeks of data), the experiment's readout (about 8 weeks), a memory strength per word
(after 60 days and a positive readout), and the deferred single planner and chat replay lab. Reading speed as
evidence is built and off: it found no gain in simulation or on the snapshot, and waits for its gate.

## 10. How we test

- **Every change:** unit tests (over 300 Python, over 240 extension). Tests never touch the real word state or the
  collector.
- **Every model change, first on a simulated reader** (`python tools/sim/run.py ...`). Fit the model to synthetic
  logs at real volumes, from a reader whose memory, reading and lookup habits are known. It must recover the truth
  there, class by class (known, learning, new): a total near the truth can hide classes that are off, as the old
  model's 1.3 times total hid 1.6 and 2.8 times errors. Each new term is also checked against a null reader, where it
  must fit to nothing.
- **Then on the real log, replayed** (`fw.py evaluate`). Predict each day from a fit on earlier days only:
  - score chat rows as well as web rows;
  - report each class on its own, and compare against the current model day by day, with a bootstrap interval;
  - hold reading pace fixed between the two models, and score form lookups as their own outcome.

  At today's volume, about 2 weeks of held-out days settle a change that affects learning words. Known words need
  longer and gain little.
- **Calibration, live:** per class (costly, free, topical, sentences, off-list), the ratio of seen to predicted lookups
  with a 90% interval from resampling days. Calibrated means the interval lies within 0.83 to 1.2.
  - Expect 2 to 4 weeks for the interval to narrow: chat lookups vary a lot by day (68, 27, 78 and 30 on the last
    full days).
  - A check over 1 to 3 days within 20%, as planned before, passes a model that is off by 20% about half the time.
  - The replay recomputes this for any model version, so a model change doesn't restart the clock.
- **Web insertion:** the prompt lab's hand-judged passages (`tools/prompt-lab/oracle.json`) score coverage and wrong
  senses, but any rule written after reading a set's errors is scored again on a fresh set judged and committed
  before any run (`fresh.json`, `fresh2.json`). The lab's in-sample 0.8% became 4.3% to 6.1% on the first fresh set;
  only fresh sets decide.
- **Prompts:** the web labs, each variant over 3 or more seeds; stop when the gain is smaller than the spread between
  seeds. A chat replay lab is deferred: it would spend your Claude limits.
- **Randomized experiments:** section 7.
- **The goal:** the monthly probe (section 1).

## 11. The numbers behind this

Measured on 2026-10-21 (the review's findings and what the implementation measured):

- **The fit, in simulation:** predicted against true lookups, known 0.99, new 0.85, learning 1.5 (the old model: 2.7,
  0.5 and 3.6); log loss 0.1000 against the old model's 0.1286, with the truth at 0.0933.
- **The fit, on the 5-day snapshot:** ties the old model on learning words and every class; 5 days can't separate
  them (the simulation expects 2 to 4 weeks). `calibrate`: chat all lookups 1.18 (0.99 to 1.26) of expected; web 0.57
  (0.35 to 0.93), mostly one evening's page.
- **The web pipeline:** on the second fresh set, 83% coverage and 0.6% wrong senses at full; today's Haiku 73% and
  3.5%; through the extension's own code, 83% to 88% and 0.5% to 0.6%. API spend for all the lab work: $3.54.
- **Chat:** the brief's first turn about 8,000 characters, later turns about 2,200; topical offers 19% wrong sense.

As of 2026-10-20 (before the implementation):

- **Words:** 1,766 in the state (297 known, 21 shaky, 6 active, 1,442 queue); 393 have estimates from the log. 150 of
  the 297 known words are Anki words that aren't in the lexicon.
- **Web:** 0.07 lookups per 100 words shown (16 in 23,832 words over 3 days), against a budget of 0.5. The first 90
  passages planned with a budget expected 3.5 lookups of the 11.3 allowed. In the model's history, 1,351 web
  observations were of free words and 157 of costly ones: the web mostly shows words you know.
- **Chat:** 0.33 lookups per 100 words. 122 replies, 79 of them with no lookup. Japanese was 6.9% of the words.
- **What chat lookups hit:**

  | Looked up | Lookups |
  |---|---|
  | words not in the lexicon | 79 |
  | queue words | 65 |
  | sentence translations | 32 |
  | words in your list | 24 |

- **Model check after counting the no-lookup replies:** 78.9 lookups expected on listed words, 82.8 seen. That is
  consistent with calibration, but its 90% interval is about ±19%, so it can't rule out a model 20% off.
- **The log so far:** October 15 to 20, 132 lookups of listed words, over 98 words; 19 words have 2 or more.
- **The chat band:** of the 319 words it calls "the most common", 87 are among the lexicon's 319 most frequent.
- **Naive find-and-replace** on the lab's 79 judged passages with today's words: 83% of the judged places covered; 30%
  of the judged places it hits are the wrong sense (39% for function words, 17% for content words).
- **The review's simulations** (scripts kept outside the repo, in
  `~/.local/share/japanese-weave-review/2026-10-20/`): today's model settles a 5% word at about 11%; a shared recency
  term beats today's model on 88% of held-out days, detectable in 1 to 2 weeks; delayed start has 73% to 95% power at
  80 words a half.
