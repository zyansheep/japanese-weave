# The plan, from the goal back to the code

Status: 2026-10-20. What is built, what comes next, and why each shortcut is acceptable. Background and the longer
argument: [learning-framework.md](learning-framework.md). How the current model works in detail:
[adaptive-difficulty.md](adaptive-difficulty.md).

## In one minute

- **Goal:** you understand more Japanese, month after month.
- **The real limit is annoyance.** The tool only teaches while it is on, and annoying it gets turned off.
- **Annoyance is mostly interruptions,** and an interruption is a lookup. So each intensity level is a **lookup
  budget**: lookups per 100 words you put up with.
- **Words you know at a glance are free.** They appear wherever they fit. The budget goes to the words that teach most:
  ones you almost know.
- **A small model guesses each word's lookup chance** from what you did before. The planner uses those guesses.
- **Everything else is a simplification,** listed in section 6 with why it is fine and how we would notice if not.

## 1. The goal

**Understanding over months**, measured as: how many common words you read without looking them up, weighted by how
often they come up.

Two things multiply into it:

- **Learning per hour on:** the right words, at the right difficulty.
- **Hours on:** zero if you turn it off.

The second one is fragile, so it sets the rules: maximise learning per hour **while annoyance stays under your
tolerance**.

## 2. What actually happens

One reading, step by step. **Bold** steps are logged; the rest we can only infer.

1. **A model writes the Japanese.** In chat, Claude writes the reply, following the hook's brief. On the web, Haiku
   offers candidate swaps, and code picks which to show.
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
| **knowledge of a word** (k) | how likely you are to recognise it at a glance, one number per word | days and weeks |
| **read or not** | whether your eyes covered that spot | seconds |
| **tolerance** | how many lookups per 100 words before it becomes annoying | minutes to hours |

Later, maybe: how guessable a word is from its sentence, knowledge of kanji shared between words, and knowledge of
grammar patterns.

## 4. How the hidden variables produce what we see

- **Lookup chance at a spot** = chance you read it × chance you look it up if read.
- **Chance you look it up if read** = sigmoid(surface offset − k). It starts from a prior and moves with each
  exposure: down after a lookup, up after reading it without one.
- **Chance you read it** comes from time on screen against reading pace (300 ms a word). It is certain above anything
  you looked up further down.
- **Forgetting:** k drifts back toward its starting point (half-life 45 days), and old evidence counts less (30 days).
- **Annoyance:** you press lighter or turn it off when recent lookups go past your tolerance. Today the presses are
  only counted (`fw.py calibrate`), not modelled.

## 5. The decision

For every candidate word:

1. **Known at a glance** (lookup chance under 8%): free. Show it wherever it fits.
2. **Otherwise it costs its lookup chance.** Spend the level's budget on these, best first. "Best" means a lookup chance
   near 25%: almost known, so seeing it teaches the most.
3. **At most one costly word per sentence** (two at heavier levels), so no sentence is wall-to-wall unknowns.
4. **Explore a little:** rank on a random draw from each word's uncertainty, so words we know little about still get
   shown sometimes.

Budgets per level, in lookups per 100 words: 0.15, 0.3, 0.5 (normal), 0.8 and 1.2. Off shows nothing. Full shows
everything the word list can say.

- **Web:** code applies these rules exactly, to the candidates Haiku offered.
- **Chat:** Claude writes, so the brief states the rules and lists the costly words with rough odds ("about 1 in 4").
  After each reply, the next brief adds a line on the last reply: expected lookups against the budget, and a verdict.

## 6. Simplifications, and why each is fine for now

| # | We assume | Why that is likely fine | We'd notice it failing when |
|---|---|---|---|
| A | "Didn't know it" and "bothered to look it up" are one chance. | Lookups are the cost we budget, so their chance is what the planner needs. | Words rated known get looked up whenever the context changes. |
| B | Whether you read a spot comes from screen time at a fixed pace (300 ms a word, at most 70%). | A wrong pace scales every word alike, and the per-surface offset absorbs that. The pace is refitted once 60 web lookups exist. | Predicted and seen lookups drift apart by position in the passage. |
| C | Tolerance is one number per level, which you set with the keys. | The keys are a direct tolerance signal, and about 5 presses a day can't fit anything richer. | Presses cluster by site, task or time of day. Then tolerance gets its own model. |
| D | One number per word, with fixed learning and forgetting rates. | With small data, simple models match deep ones (Gervet et al. 2020). We have about 200 chat lookups a week. | A richer model beats it on replay log loss for held-out days. |
| E | "Aim near a 25% lookup chance" stands in for modelled learning. | It is the usual sweet spot: hard enough to teach, easy enough to read. | Only a randomized experiment can tell (step 6 below). |
| F | Known-at-a-glance words cost nothing. | They are the bulk of the reading practice, and you said known words inside English sentences are an easy switch. A free word you do look up stops being free. | Lookups on free words grow past the budget itself (calibrate prints it). |
| G | Words don't affect each other, apart from the one-per-sentence cap. | The cap removes the worst case, two unknowns in one sentence. The data is too thin for interaction terms. | Sentences with two costly words get more lookups than predicted. |
| H | Words with no history start from their status (known: 8%, others: 27%) or their Anki interval. | **Not fine.** 1,442 queue words look identical to the planner. | Already true: this is step 2. |
| I | Old chat replies' paragraph word counts are estimated from words per pixel. | It only affects 123 old replies. New ones send exact counts. | Nothing to watch: the estimate goes away with time. |
| J | In chat, Claude follows the brief roughly, not exactly. | Each reply gets a feedback line, so the budget holds on average, not per reply. | The feedback verdicts keep saying over or under. |
| K | No Japanese outside the word list in chat. | Those words caused 72% of chat lookups, and nobody chose them. New words now come in through the queue (up to 5 a day). | You miss them. Then words you looked up off the list join the queue first. |
| L | A hover under 300 ms in chat is the mouse passing by. Under 600 ms on the web, it counts as 0.6 of a lookup. | Short hovers are mostly accidental, so partial credit is safer than a full lookup. | Short-hover rates differ a lot between words you know and don't. |
| M | Your state of mind (tired, skimming) is ignored. | The per-surface offset absorbs the average; the rest is noise that averages out over days. | Lookup rates swing a lot from day to day. Then add a per-day offset. |

## 7. Built so far (2026-10-20)

- Every annotated chat reply counts, looked up or not. T3 sends word counts; old replies were backfilled.
- Levels are lookup budgets on both surfaces; known words are free; one costly word per sentence.
- Words you know at a glance no longer block new words: the learning window refills again.
- The chat brief lists costly words with their odds and bans Japanese outside the list. The feedback line reports
  expected lookups against the budget.
- Web plans log their budget and expected lookups, so each prediction can be checked later.
- `fw.py calibrate` prints the chat side: lookups per 100 words, replies with none, and predicted against seen.

## 8. Next steps, in order

1. **Check the live data** (1 to 3 days after T3 is restarted). Done when:
   - chat predicted and seen lookups agree within 20%;
   - Japanese outside the list causes under 20% of chat lookups (was 72%);
   - both surfaces land within a factor of 2 of their budget.
2. **Starting chances from similar words.** Predict a new word's chance from its frequency rank, kanji shared with
   words you know, script (katakana gets its own baseline), word type, length and Anki interval. Fit a small
   regression on the words with history. Done when it beats today's flat start on replay log loss, scored on each
   word's first exposures.
3. **One record per chat reply:** the brief's budget, its odds, and the expected lookups, stored with the reply's
   context. Then chat predictions can be checked like web ones.
4. **Prompt iteration per model and surface.** Web: the existing lab with its hand-judged answers, scored on budget
   error, coverage and wrong senses. Chat: a new replay lab built from your recorded prompts. Run each variant over
   3 or more seeds; stop when the gain is smaller than the spread between seeds.
5. **Reading speed as evidence.** Slower paragraphs probably held unknown words. Every paragraph has a speed, so this
   is evidence even without a lookup.
6. **Test the learning assumption (E).** Randomly split learning words into two groups, show one group more for two
   weeks, then compare lookups. Slow, but it is the only direct test that exposure teaches.
7. **Later:** a tolerance model by context, grammar patterns as items, and sending the word list once per chat
   instead of every turn.

## 9. How we test

- **Every change:** unit tests (168 Python, 167 extension). Tests never touch the real word state or functor.
- **Any model change:** replay log loss on held-out days, in time order: predict each row from earlier rows only. One
  number decides between two models. It is reported for all words and separately for words being learned.
- **Live:** `fw.py calibrate`. It shows predicted against seen lookups per surface, lookups per 100 words per level,
  and presses.
- **Prompts:** the labs (step 4). The web lab already scores coverage and wrong senses against a hand-judged answer
  set.
- **Effects the model can't check on its own:** randomized experiments. Thinning already runs: half the web passages
  get a random share of their swaps. Step 6 adds the learning experiment.

## 10. The numbers behind this (2026-10-20)

- **Words:** 1,766 in the state (297 known, 21 shaky, 6 active, 1,442 queue); 392 have estimates from the log.
- **Web:** 0.07 lookups per 100 words shown (16 in 22,281 words over 3 days).
- **Chat:** 0.33 lookups per 100 words. 121 replies, 78 of them with no lookup. Japanese was 6.9% of the words, against
  a 25% target.
- **What chat lookups hit:**

  | Looked up | Lookups |
  |---|---|
  | words not in the lexicon | 79 |
  | queue words | 65 |
  | sentence translations | 32 |
  | words in your list | 24 |

- **Model check after counting the no-lookup replies:** 78 lookups expected on listed words, 81 seen.
