# The plan, from the goal back to the code

Status: 2026-10-20, revised after an adversarial review by three independent reviewers: the model's assumptions, the
design's simplicity, and a simulated reader with known ground truth. Why the system works the way it does, where it
is going, and why each shortcut is acceptable. What to do next, in order: [NEXT_STEPS.md](NEXT_STEPS.md). Background
and the longer argument: [docs/learning-framework.md](docs/learning-framework.md). How the current model works in
detail: [docs/adaptive-difficulty.md](docs/adaptive-difficulty.md). How the web prompt was tuned:
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
- **Chance you look it up if read** = sigmoid(surface offset − k + recency term). The lookup habit c is folded into
  k and the offset (row R); the probe measures it separately.
- **Starting value:** a word with no history starts from a prior.
  - Today: its status (known: about 7%, others: 27%) or its Anki interval. The Anki formula stops rising at a 21-day
    interval, but your lookups keep falling past it: 7.6% a read for intervals of 20 to 57 days, 5.0% for 58 to 89,
    2.2% beyond.
  - Planned: predicted from the word itself (frequency rank, kanji shared with known words, script, word type,
    length) and the uncapped Anki interval.
- **Chance you read it** comes from time on screen against reading pace (300 ms a word). It is certain above anything
  you looked up further down.
- **How it is fitted:**
  - Today: online updates, one observation at a time with a step size, and between observations k is pulled back
    toward its starting value (45-day half-life, downward only). With known truth in a simulation, that pull holds a
    word whose true chance is 5% at about 11% when it is seen 3 times a day. Without the pull, the step size still
    needs hundreds of exposures to get there.
  - Planned: **one fitter.** On each refresh, a penalized maximum-likelihood fit of the whole log. Each word's k is
    held toward its starting value by a prior, not a pull, and the shared parameters are fitted alongside: surface
    offsets, reading pace and cap, the recency term, and the starting-value regression. Old evidence counts less
    (30-day half-life on its weight). The curvature of the fit gives each word's uncertainty. The log is a few
    thousand rows, so this is cheap.
- **Recency, planned in two stages:**
  - First, a shared term with two parameters. The chance rises with the time since the word was last read, with each
    showing weighted by its read chance, so a showing you likely skipped doesn't reset the clock. A repeat in the
    same page or thread gets its own indicator, because context makes it easier; otherwise it would read as
    forgetting.
  - Later, a strength per word on an FSRS-shaped curve (Anki's scheduler; also half-life regression, Settles and
    Meeder 2016), where reading after a longer gap strengthens memory more. This waits until the log covers 60 days
    and the exposure experiment shows that exposure teaches. Until then the data can't identify it: the log's longest
    within-word gap is about 5 days, and 19 words have 2 or more lookups.
- **Anki gives the starting state, not the rates.** Your reviews ended in June, four months before the log began, so
  nothing yet ties Anki's forgetting rates to reading.
- **Annoyance:** you press lighter or turn it off when recent lookups go past your tolerance. Today the presses are
  only counted (`fw.py calibrate`), not modelled.

## 5. The decision

**One unit everywhere: lookups per 100 words read.** Planning assumes the passage will be read; checking weighs each
shown word by its chance of having been read. The planner, the chat feedback line and `calibrate` all use this one
definition. Today they use three: the planner and feedback line sum lookup chances with no read chance, `calibrate`
weighs by read chance, and the daily controller counts free words that the budget excludes.

For every candidate:

1. **Known at a glance: free.** Show it wherever it fits. This is judged on the long-term chance (the chance after a
   week without the word), not the current one: right after you read a word its current chance is near zero, and that
   must not make a learning word free. A word leaves the free set only when the low end of its uncertainty passes 8%,
   and rejoins when the high end falls below it, so one lookup doesn't flip it.
2. **Otherwise it costs its current chance.** Spend the budget on these, best first: the current chance closest to 25%.
   With the recency term this is also the spacing rule. A word read an hour ago is far below 25% and waits; one unread
   for a while drifts up and comes first. A costly word whose current chance is under 10% waits even when budget is
   left. No separate review schedule is needed, and on the web none could be kept, since a word appears only where a
   page says it.
3. **A whole sentence in Japanese costs the observed rate of translation lookups per sentence** (planned; today
   sentences are free, yet they caused 16% of chat lookups). Sentences use free words only, so how many appear follows
   the budget, not the stage.
4. **At most one costly word per sentence** (two at heavier levels), so no sentence is wall-to-wall unknowns.
5. **Explore a little:** rank on a random draw from each word's uncertainty, so words we know little about still get
   shown sometimes.

Budgets per level, in lookups per 100 words read: 0.15, 0.3, 0.5 (normal), 0.8 and 1.2. `challenge` sets normal's
budget directly. The daily controller that steered it goes: the fitted surface offset already keeps predicted lookups
equal to seen, and the controller couldn't tell a miscalibrated model from a thin candidate pool. Off shows nothing.
Full is the same path with no budget, no per-sentence cap and sentences free.

**One planner, two executors** (planned). The refresh ranks and prices every word once, for both surfaces. On the
web, code carries out the plan exactly. In chat, the brief lists the planner's top costly words with odds, Claude places
them, and the feedback line reports how the reply did. Today the two planners are separate code with mirrored
constants.

## 6. Where words and candidates come from

### The vocabulary

**Today:** a curated list of 1,500 common words (`data/lexicon.csv`) plus your Anki words, in a fixed order. New words
come off the front of the queue, at most 5 a day into 20 learning slots, or sooner if you looked one up in two
messages. Words outside the list can't be taught: chat may not use them, and lookups of them go nowhere. Two systems
decide what "learning" means: these day-count rules, inherited from French Weave, and the model's estimates.

**Planned:**

- **The dictionary is the vocabulary.** All common JMdict entries (about 22,000) with their frequency ranks, built by
  the same pipeline as today's list but without the word-by-word review. It is a static file. The word state keeps
  only words with some history: shown, looked up, marked, or on an Anki card.
- **Your reading nominates words; a few a day get in.**
  - The hook already reads chat transcripts, and the extension already sees page text. Both count, per dictionary
    entry, how often it turns up (counts only, never text). Only safe or model-confirmed matches count, since 30% of
    naive matches are the wrong sense. Claude's Japanese in chat counts too.
  - A word becomes eligible after coming up on 3 different days in 30. Each day at most about 5 eligible words join,
    and only while the budget goes unspent. They are ranked by the days they came up, general frequency, and
    predicted chance.
  - In a simulation of one reader's text, the ranking and the intake rate decide whether words come back, not the
    threshold. Admitting every eligible word (40 to 60 a day) gave a median of 0 or 1 exposures in two weeks. Five a
    day, ranked, gave a median of 11 to 22, and 80 to 100% reached 8. This gets harder after a few thousand words,
    once the frequent ones are used up.
- **No slots, and status from the model.** Known means free (rule 1 in section 5). `known` and `learn` stay as strong
  evidence. These retire after a replay of the log under both systems shows where their labels disagree and the
  disagreements look right:
  - the day-count rules and the 20 slots;
  - the evidence moves;
  - the rule that shows a new word first, outside the budget;
  - the chat recall cue.

  The old fields stay in the state for a month so the switch can be undone.

Why this holds together: the goal weighs words by your reading, so the words your reading repeats are the ones worth
learning, and they come back on their own, which repeated exposure needs. It is also what worked in practice: the
words Claude used outside the list in chat were topical and recurring, and you liked them.

### Candidates on the web

**Today:** Haiku proposes swaps, and code validates and picks. Haiku is timid. On the prompt lab's hand-judged
passages it covers 51% of the places one of your words fits at normal, and 80% when asked for everything, with 2.5% to
2.7% wrong senses. A 22,000-word list wouldn't fit in its prompt either.

**Planned:**

- **Code finds every place.** An index from English (lemmas, irregular forms, phrases like "there is") to dictionary
  entries marks every span a word could take, with its options. On the lab's 79 judged passages, naive matching
  against today's words already covers 83% of the places, instantly and at no cost.
- **A model settles the doubtful ones.** The same naive matching takes the wrong sense at 30% of the judged places it
  hits: 39% for function words ("about", "so", "one"), 17% for content words ("power", "rest"). So each entry carries
  a flag, set once offline: safe (code swaps it alone) or doubtful. Doubtful spans go to Haiku, which picks the right
  option or none. With a large dictionary this is the common case: among 4,000 entries, 28% of English words already
  map to two or more Japanese words.
- **The same call writes whole sentences.** Code finds the sentences and phrases dense enough in words you know to be
  said in Japanese; Haiku writes those, and code validates every word, as today.
- **Passages with nothing doubtful and nothing dense need no call,** so they show at once and cost nothing.
- **One candidate set serves every level,** full included. The separate full prompt, its cache, the per-passage target
  and the density cap go.
- Because code builds the full candidate list, each passage can log its candidates and each one's chance of being
  shown, which evaluating other choosers needs (docs/learning-framework.md, section 4).

### Chat

**Today:** the brief describes the known words as a band ("roughly the 319 most common words"), lists learning words
with odds, and allows no other Japanese. The band is wrong: it is the first 319 words of your list, which the Anki
sync reordered, so most of it isn't what a model thinks of as common.

**Planned:**
- The brief names the known words once per chat, then sends only changes.
- It lists the planner's costly words with odds.
- It lists **topical words found by code**: dictionary entries the matcher finds in the conversation's recent turns,
  each with its odds. So topical words are chosen and priced before Claude writes, not counted afterwards.
- A topical word that keeps coming up is nominated like any other.

## 7. Experiments

Two effects the model can't check on its own get randomized tests.

- **Thinning tests what free words cost** (row F). Each passage keeps a random share of its free words, from 0.2 to 1.
  Today's range of 0.5 to 1 leaves too small a contrast to read: 5.3% against 6.5% Japanese. Thinning no longer scales
  the budget, so costly words are untouched and the comparison isolates free words. The outcome is reading time per
  word, and lookups.
- **Delayed start tests whether exposure teaches** (row E).
  - A random half of new words is held back, appearing nowhere (chat topical words included) for 3 weeks. The other
    half is shown normally for 2 weeks, then paused for 1.
  - At week 3 both halves are probed: each word's next showing is guaranteed. The outcome is whether it was looked up,
    with its read chance as a covariate, compared with a logistic regression.
  - In simulation this has 73% to 95% power at 80 words a half, which takes about 5 weeks of intake at 5 new words a
    day.
  - The earlier design, 0.5 or 1.5 times the exposure read off the model, can't work. More exposure means shorter
    gaps, so it "finds" an effect when there is none (100% false positives in simulation). The true difference is
    also too small to detect in 2 months. As written it was also inverted: the planner ranks by a negative value, so
    multiplying it by 1.5 ranked a word lower.
  - Each word's arm is logged in every plan record and every chat brief record.

## 8. Simplifications, and why each is fine for now

| # | We assume | Why that is likely fine | We'd notice it failing when |
|---|---|---|---|
| A | "Didn't know it" and "bothered to look it up" are one chance. | Lookups are the cost we budget, so their chance is what the planner needs. | Words rated known get looked up whenever the context changes. **Seen now** for common verbs: a lookup of a conjugated form (often about the ending) is charged in full to the base word, so `する` and `行く` left the free set after one lookup each. Fix: forms count as a weaker label, scored as their own outcome. |
| B | Whether you read a spot comes from screen time at a fixed pace (300 ms a word, at most 70%). | A wrong pace scales every word alike, and the fitted offsets absorb that. | Predicted and seen lookups drift apart by position in the passage. **Weak now:** the replay that tunes the pace scores web rows only, nearly all of them words with Anki history, and on the live log a constant guess scores about as well as the default pace. Fix: score chat rows too, in the one fitter. |
| C | Tolerance is one number per level, which you set with the keys. | The keys are a direct tolerance signal, and about 5 presses a day can't fit anything richer. | Presses cluster by site, task or time of day. Then tolerance gets its own model. |
| D | One long-term number per word, plus (planned) a recency term shared by all words. | With small data, simple models match deep ones (Gervet et al. 2020). We have about 200 chat lookups a week, and shared terms need few. | A per-word strength beats it on the replay, by day, on learning words. Not before 60 days of log. |
| E | Being shown a word near a 25% lookup chance teaches it. | It is the usual sweet spot: hard enough to teach, easy enough to read. | The delayed-start experiment shows no difference at week 3 (section 7). |
| F | Known-at-a-glance words cost nothing. | They are the bulk of the reading practice, and you said known words inside English sentences are an easy switch. A free word you do look up stops being free once the evidence is clear. | Lookups on free words grow past the budget itself, or thinning shows free words slow reading a lot. So far: about a third of chat lookups on listed words (0.05 per 100 words), about nine in ten on the web (0.08 per 100 words). |
| G | Words don't affect each other, apart from the one-per-sentence cap. | The cap removes the worst case, two unknowns in one sentence. The data is too thin for interaction terms. | Sentences with two costly words get more lookups than predicted. |
| H | Words with no history start from their status (known: about 7%, others: 27%) or their capped Anki interval. | **Not fine**, and worse with the dictionary: 22,000 words would share a 27% start, right at the 25% target. | Already true. Fix: starting values from the word itself and the uncapped interval, before any dictionary word is offered. |
| I | Every chat reply counts, opened or not, from T3's word counts. | That removes the bias of counting only replies with a lookup. | **Failing now:** the T3 build that sends word counts isn't running. All 123 counted replies are a one-off backfill (paragraph counts estimated from words per pixel), and new replies count only when something in them was opened. |
| J | In chat, Claude follows the brief roughly, not exactly. | Each reply gets a feedback line, so the budget holds on average, not per reply. | The feedback verdicts keep saying over or under. |
| K | Chat may use topical words outside your lists, found by code in the conversation and priced (planned; today none are allowed). | Before the ban they caused 72% of chat lookups with nobody choosing them; now each is chosen and priced first. You missed them, and they recur with the topic. | Topical words take most of the budget, or their lookups run well past prediction. Then cap them per reply. |
| L | A hover under 300 ms in chat is the mouse passing by. Under 600 ms on the web, it counts as 0.6 of a lookup. | Short hovers are mostly accidental, so partial credit is safer than a full lookup. | Short-hover rates differ a lot between words you know and don't. |
| M | Your state of mind (tired, skimming) is ignored. | The fitted surface offsets absorb the average; the rest is noise that averages out over days. Today the chat offset moves 0.02 per message and in simulation recovers a tenth of a true offset, so the one fitter is needed for this to hold. | Lookup rates swing a lot from day to day. Then add a per-day offset. |
| N | Code's matching plus a model's sense check is as accurate as the model proposing swaps itself. | Measured, not assumed: it replaces Haiku's proposals only once the lab shows at least 80% coverage with at most 3% wrong senses. | Flips back to English rise, or wrong senses climb on newly judged passages. |
| O | A word that keeps turning up in your reading is worth learning, and will keep turning up. | The goal weighs words by your reading. Ranked intake of a few a day keeps exposures dense. | Admitted words come up less than forecast after they join (they regress to the mean), or the set fills with narrow jargon. Then weigh general frequency more. |
| P | Every whole sentence in Japanese costs the same: the observed rate of translation lookups per sentence. | They use known words only, so what varies is mostly grammar, which isn't modelled yet. | Long or grammar-heavy sentences get looked up far more than short ones. Then price by length. |
| Q | Your Anki interval predicts reading lookups. | Live lookup rates fall with interval: 7.6%, 5.0% and 2.2% a read across the three interval bands above. | The fitted slope on the interval goes to zero as reading evidence accumulates. |
| R | A lookup is "didn't recognise it" times a lookup habit c, and c is folded into k. | The budget is in lookups, and the product is what costs. The probe measures c on its own. | Lookups fall while the probe doesn't move: you are getting used to the glosses, not learning. Then model c over time. |

## 9. Built so far, and what is planned (2026-10-20)

Built:

- Chat replies that have a word count are counted whether or not anything in them was opened. So far only the 123
  backfilled replies have one (row I).
- Levels are lookup budgets on both surfaces; known words are free; one costly word per sentence.
- Words you know at a glance no longer block new words: the learning window refills again.
- The chat brief lists costly words with their odds and bans Japanese outside the lists. The feedback line reports
  expected lookups against the budget.
- Web plans log their budget and expected lookups, so each prediction can be checked later.
- `fw.py calibrate` prints the chat side: lookups per 100 words, replies with none, and predicted against seen.

Planned: everything marked planned in sections 1 to 8. [NEXT_STEPS.md](NEXT_STEPS.md) has the order and the "done
when" for each step.

## 10. How we test

- **Every change:** unit tests (168 Python, 167 extension). Tests never touch the real word state or the collector.
- **Every model change, first on a simulated reader.** Fit the model to synthetic logs at real volumes, from a reader
  whose memory, reading and lookup habits are known. It must recover the truth there. For example: a word starting at
  27% whose true chance is 5%, seen 3 times a day, must cross the 8% line within 300 exposures. Today it never does.
  Report each class (known, learning, new) on its own: in simulation, today's model got the total within 1.3 times
  while known and learning words were off by 1.6 and 2.8 times. The review's simulation scripts are the starting point.
- **Then on the real log, replayed.** Predict each day from a fit on earlier days only:
  - score chat rows as well as web rows (today only web rows, almost all of them known words);
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
  senses. The code matcher has to match Haiku there before it replaces Haiku's proposals (row N).
- **Prompts:** the web lab, and a chat replay lab built from your recorded prompts. Each variant over 3 or more seeds;
  stop when the gain is smaller than the spread between seeds.
- **Randomized experiments:** section 7.
- **The goal:** the monthly probe (section 1).

## 11. The numbers behind this (2026-10-20)

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
