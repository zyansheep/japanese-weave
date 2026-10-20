# A unified learning model, walked through

Status: a proposal to replace the hand-tuned rules in [adaptive-difficulty.md](adaptive-difficulty.md). Nothing here
is built. As of 2026-10-16 the collector holds two logged peeks, so every step below waits on data.

## 1. Two jobs: measuring and deciding

**Measuring**: for any Japanese word shown in any paragraph, predict the chance you open its gloss. This can be
checked, because each time a word is shown we later see whether you opened it.

**Deciding**: given those predictions, choose which of Gemini's candidate words to show, so you learn as much as
possible without opening more glosses than you want. This cannot be checked the same way. We only see what happened
with the words that were shown, never what a different choice would have done.

The current design blends both jobs into hand-set rules. The proposal separates them: prove a predictor on the log,
then build a chooser on top. "A single metric" covers only the first job; the second needs its own definition of
learning, and that is where most of the hard questions are.

## 2. The data

A toy session. On October 20 you read a 60-word paragraph in the browser. Gemini proposed four words for it: jikan
(time), tabun (probably), mise (shop) and kotoba (word). The chooser showed three. You hovered on tabun and kept its
gloss open for 1.8 seconds. Two days later a chat reply used tabun, and you did not open it.

Each row is one word shown once. "Opened" means a peek (hover until the gloss shows), a pin (click, in chat) or a flip
(click to turn the word back to English). Values marked *new* are not logged today.

| time | surface | paragraph | word | line | on screen | opened | gloss open | candidates | pick chance |
|---|---|---|---|---|---|---|---|---|---|
| Oct 20 09:14 | browser | P1, 60 words | jikan | 1 | 40 s *new* | no | | 4 words *new* | 0.9 *new* |
| Oct 20 09:14 | browser | P1, 60 words | tabun | 2 | 40 s *new* | yes | 1.8 s | 4 words *new* | 0.8 *new* |
| Oct 20 09:14 | browser | P1, 60 words | mise | 4 | 40 s *new* | no | | 4 words *new* | 0.7 *new* |
| Oct 22 21:03 | chat | M7 | tabun | 3 | *new* | no | | none | none |

Kotoba gets no row because it was not shown, but the candidates column records that it could have been. "Pick
chance" is the probability that the chooser would show that word there. It describes the chooser, not you.

What exists today:

- **Browser "shown" records**: one per paragraph per page load, when its Japanese first comes on screen: time,
  paragraph id (a hash; no page text), word count, and every glossed word with its position. That covers rows 1 to 3
  except the *new* columns. Since every shown paragraph is logged, the old "at least one open" correction is not
  needed for the browser.
- **Browser peeks and flips**: time, word, how long the gloss stayed open (glosses need a 0.7 s hover first).
- **Chat peeks and pins**, but no shown records: a message is logged only if you opened something in it. Row 4 does
  not exist today, and chat data leans toward opens. Leave chat out of the fit until it sends shown records. Chat
  glosses words as written, conjugated verbs whole (なります); the replay maps each to its dictionary form
  (scripts/inflection.py), and every lexicon word counts, not only the ones introduced so far.

Since extension 0.3.1 time on screen is logged too (visible stays), and since 0.6.0 each shown passage's plan (the
swaps allowed, the candidates offered, the reader's intensity and a random thinning factor). Paragraphs planned that
got no Japanese (no candidates, or thinned to none) are logged as shown and on screen too, with no glossed words. Not
logged: the candidate list itself, pick chances, and the chooser's inputs at the time (each word's status
and estimate, the dial, the budget).

**Second stream: Anki.** One row per review: card, time, rating, kind, interval. 51,465 rows for 3,702 cards, July
2025 to June 5, 2026. Of the 16,961 "Again" presses, only 6,412 are failed scheduled reviews (out of 22,934). The other
10,549 are repeats minutes apart within a learning or relearning session. Another 6,514 rows are manual reschedules,
not reviews. Anki stopped four months before the reading log began.

## 3. The functions to learn

| | input | output | learned from | scored by |
|---|---|---|---|---|
| (a) reading | line position, time on screen, paragraph length | chance you read the word's line | indirectly | only via the combined score |
| (b) opening | the word, its features, your history with it, context | chance you open it, if read | shown rows, plus Anki | replay log loss of (a) × (b) |
| (c) change | a word's history plus one new event | chance of opening it later | history weights inside (b) | weakly by log loss; properly by randomized exposure |

**(a) Did you read it?** "Not opened" may mean you knew it, skipped the line, or didn't care. Reading itself is never
logged, only time on screen (knowledge.py read_chance turns it into a chance per line, as a first version of (a));
the only firm evidence is an open at or below that line (line 2 in the toy, so line 4 is a guess). Opening glosses
takes time, so time before the first open is a cleaner input than total time. (a) can never be checked on its own.

**(b) Would you open it, if read?** The proposal uses logistic regression: add up weighted features and convert the
total to a probability. The total is in log-odds, another scale for probability where 0 means 50%. In words:

> log-odds of opening = word difficulty − your current knowledge of the word + context adjustments

Word difficulty is a per-word number plus weights on frequency rank, script and Anki history. Current knowledge is
weighted counts of your past exposures and opens in time windows (last day, week, month, ever), the DAS3H approach
(Choffin et al. 2019). Context is paragraph length, Japanese density, browser or chat. This is a "knowledge tracing
machine" (Vie and Kashima 2019); such models match deep ones on small datasets (Gervet et al. 2020). Anki rows join
with their own offset, sharing word difficulty.

Two inputs need care. Gloss-open time is known only after an open, so it cannot predict that open. And Anki features
must use only reviews before each row's time: anki.csv holds today's intervals, so using them to replay past rows
leaks the future (the current starting scores do this).

**(c) What does an exposure do?** (b) answers "if word A appears, will you open it?" Choosing needs (c): "if we show
word A today, how much likelier are you to read it unaided next week?" Here (c) is not separate code but the weights
on the history windows: a fitted forgetting curve replacing the fixed 45-day half-life (window edges stay hand-picked).

The catch: a model can predict well while those weights mean the wrong thing. Today a flip restarts a word's
graduation clock, so words you struggle with get shown more. "Many recent exposures" then goes with "more opens", and
the fit concludes that exposure hurts: fine as prediction, backwards as cause. Only exposure that the system itself
randomizes separates the two.

### Scoring a predictor: the replay

Sort rows by time. For each row, predict from earlier rows only, record a penalty, then learn from the row. The
penalty is log loss: minus the natural log of the probability given to what actually happened.

| row | word | predicted chance of opening | happened | loss |
|---|---|---|---|---|
| 1 | jikan | 0.10 | no | −ln 0.90 = 0.11 |
| 2 | tabun | 0.60 | yes | −ln 0.60 = 0.51 |
| 3 | mise | 0.30 | no | −ln 0.70 = 0.36 |
| 4 | tabun | 0.45 | no | −ln 0.55 = 0.60 |

Average: 0.39. Always guessing 25% scores 0.56; always 50%, 0.69. Lower is better, and confident misses are costly:
0.01 for something that happens costs 4.6. FSRS, Anki's scheduler, is fit and benchmarked with this loss on reviews
in time order.

Two cautions. Fix the label rules first (does a half-second glance count?), since models compare only on identical
labels. And most rows will be known filler words you never open; report the loss on learning words separately.

## 4. What "maximize learning" means

**Knowledge** of a word at a future date (the horizon): the model's chance that you would *not* open it in a typical
paragraph you read then.

**Objective**, some candidates: the number of learning words you could read unaided in a week (the sum of those
chances); the same weighted by how often each word appears in your reading; average knowledge over the next month, so
quickly forgotten words count less; or knowledge gained per gloss opened. The choice changes behaviour: weighting by
frequency favours common words, a one-week horizon favours quick wins.

**Budget**: the dial, expected opens per 100 words shown, plus the cap on Japanese words per paragraph.

**Rule.** For each candidate, *gain* is predicted knowledge at the horizon if shown now minus if not shown; *cost* is
the chance you open it now. Then:

> show the words with the highest gain − λ × cost

λ is a price per open, tuned until expected opens match the dial. Gain comes entirely from (c): if (c) is wrong, the
rule optimizes the wrong thing. If gain peaks for words you open about a quarter of the time, the rule lands near
today's 25% target, for a measured reason.

**Greedy versus planning.** Greedy picks the best words for this paragraph now. Planning would weigh later paragraphs
too, but you don't control what you read next. Greedy still respects spacing: the history windows give a word seen
minutes ago little gain.

### Testing a chooser

- **Simulation**: run each rule against the fitted model as a stand-in for you. Cheap, but circular: a model that
  thinks exposure teaches a lot will favour rules that show a lot.
- **Off-policy evaluation**, estimating how a rule you did not run would have done. Each logged outcome counts in
  proportion to (new rule's chance of that choice) ÷ (old rule's chance), so the old rule needs some chance of every
  choice the new one makes: randomization. With the dial off (the default) today's chooser is fixed, chances 0 or 1,
  so nothing can be reweighted. With the dial on it already draws at random (seeded by paragraph and day). This works
  for short-range outcomes, such as opens in that paragraph. "Known in a week" depends on dozens of later choices; the
  ratios multiply, and with one reader the estimate is too noisy.
- **An experiment on yourself**: randomly split learning words into two groups, show one more for two weeks, then
  compare how often you open each group. Slow, but it measures (c) directly.

Chat words are picked by the chat model, with no pick chances, so chat choices can only be simulated.

## 5. Ambiguities and weak spots

- **An open is not ignorance, and no open is not knowledge.** An open may be checking a guess, or curiosity; no open
  may be skipping, guessing from context, or not caring.
- **Measuring teaches.** Every open is also a lesson, so history counts mix evidence of knowledge with its causes.
- **Context versus the bare word.** "It will tabun rain" is guessable; tabun alone may not be. Page text is never
  logged, so guessability lands as noise in word difficulty.
- **Circularity and feedback.** The objective is the model's own forecast, and the system shows what it believes is
  useful. Known words, for instance, are shown often as filler and rarely opened, which says nothing about exposure
  teaching. Only later outcomes under varied choices validate the model.
- **Anki is a different task.** Recall from a card is not recognition mid-sentence, and the reviews ended in June.
  About 45,000 Anki reviews will swamp a few thousand reading rows in the shared word numbers, so Anki needs
  down-weighting or its own per-word adjustment. And only 574 words from those 3,702 cards are in anki.csv, so many
  reviews have no listed word to attach to.
- **Gemini's pool limits choice.** The chooser only reorders Gemini's candidates, and Gemini's prompt is shaped by the
  word list. Words that rarely fit English text can't be shown often.
- **One reader, little data, shifting behaviour.** Mood, fatigue, time of day and page type move the open rate. Every
  feature costs data: add one at a time and keep it only if the replay loss drops.
- **Annoyance and flow are not in the objective.** The dial stands in for them.
- **Embeddings may not help.** For one reader, difficulty is mostly frequency and personal history. With a few hundred
  words a large embedding will overfit. Kanji shared with known words is more plausible; test it like anything else.

## 6. Next steps

1. **Log what is missing**: time on screen with the tab focused and time before the first open; the candidate list,
   pick chances, and the chooser's inputs and version; shown records for chat. Still no page text.
2. **Add a little randomness**: even with the dial off, pick a small share of words at random, chances logged.
   Optionally run the two-group experiment.
3. **Build the replay harness** with fixed label rules. Baselines: a fixed rate, a per-word rate, the current
   knowledge.py. Report loss on all rows and on learning words.
4. **Fit the logistic model**, Anki included as of each row's time, adding features one at a time.
5. **Switch the chooser last**, only when the predictor beats the baseline, and treat its exposure effects as unproven
   until randomized data backs them.

## References

- Vie, J.-J., & Kashima, H. (2019). Knowledge Tracing Machines: Factorization Machines for Knowledge Tracing. AAAI.
- Choffin, B., Popineau, F., Bourda, Y., & Vie, J.-J. (2019). DAS3H: Modeling Student Learning and Forgetting for
  Optimally Scheduling Distributed Practice of Skills. Educational Data Mining.
- Gervet, T., Koedinger, K., Schneider, J., & Mitchell, T. (2020). When Is Deep Learning the Best Approach to
  Knowledge Tracing? Journal of Educational Data Mining.
