# Adaptive difficulty

Japanese Weave estimates, for every word, how likely you are to open its gloss, from the gloss interaction log.
It uses those estimates three ways: to decide which learning words appear on a page, to steer how many appear
toward a target number of peeks per 100 words (one dial for a steady learning rate), and to move words between
learning and known faster and more reliably than day counts alone.

This is version 1. A first draft was reviewed adversarially (statistics and engineering); the decisions that
review forced are listed at the end.

## Background

- Comprehension: unassisted reading needs about 98% of running words known (Hu & Nation 2000; Nation 2006), so
  unfamiliar Japanese should stay near 2 per 100 words; words you know add volume at no cost.
- Difficulty: practice works best when most attempts succeed but not all. Japanese Weave aims learning words at
  a predicted peek chance near 25% (a tunable target, not a law).
- Implicit evidence: a peek shows you were reading that spot, so unopened glossed words around a peek are
  evidence you knew them. Web-search click models make the same split between "examined" and "relevant"
  (Craswell et al. 2008); LingQ's "unclicked words become known on page turn" is the naive form.
- Estimation: Elo-style online updates of a logistic model (Pelánek 2016) with forgetting (Settles & Meeder 2016).

## Data

The log (collector.py, feedback.py) holds, without page text:

- interactions: one row per peek (gloss opened by hovering), pin (opened by a click or tap, T3) or flip (word
  turned back to English, browser): time, surface (browser | t3), word, how long the gloss stayed open, view,
  context, the word's index in the context, and a client id that makes resends harmless.
- contexts: one row per paragraph per page load (browser) or chat message per window session (T3): its box,
  every glossed word in it as [word, x, y] relative to the context, and, for the browser, its word count and when
  its Japanese was first on screen ("shown"). The words of shown paragraphs are the denominator of the peek rate.
  Paragraphs planned to get Japanese that got none (no candidates fit, or the random thinning left none) are shown
  too, with no glossed words, when enough of them is on screen: the baseline for calibration. They don't count
  toward the peek rate and carry no evidence about words.
- reading: evidence of where the reader was, in contexts with glossed words (browser paragraphs, T3 messages): clicks
  (point, link or text), text selections (box, length, the indexes of the glossed words covered, drag time), mouse
  paths over T3 messages ([ms, x, y] points), and stays on screen of each paragraph (its band in the context; ms of
  possible reading, plus the time away, with media playing, or idle for 30 s), also of paragraphs left without
  Japanese. The model below doesn't use it yet.

Commands (`known`, `learn`) are kept with their time in `commands.jsonl` so a replay can place them.

## The model

For a glossed word w shown on surface s (a browser passage, or a T3 message in which something was opened):

    P(peek) = sigmoid(s_offset - k_w(t))

- k_w: knowledge of w on a log-odds scale. Its uncertainty is var_w = 2 / (1 + n_w), where n_w is the evidence
  weight seen so far, which itself decays (half-life 30 days) so a word unseen for a while becomes uncertain again.
- Forgetting: between observations, k_w decays toward its prior mu_w with a half-life of 45 days, but only
  downward (knowledge fades; ignorance doesn't turn into knowledge by waiting).
- s_offset: 0 for the browser; a learned offset for T3 (its popover opens after 0.3 s instead of 0.7 s), moved by
  0.02 per episode and clamped to [-1.5, 1.5].
- Priors from outside the model only: Anki interval i gives mu = min(3, 0.5 + 0.8 * ln(1 + i)); any other word 1.0.
  A `known` command raises k to at least 2.5 (and adds evidence); `learn` lowers it to at most 0.

### Episodes and observations

Browser: every passage shown (its "shown" record, one per passage per page view) is scored, opened or not, once 10
minutes have passed since the last thing that happened in it. T3: an episode is the interactions in one
(surface, view, context) with no gap over 10 minutes; T3 logs a message only when something in it was opened, so only
such engaged episodes exist, scored once their last interaction is 10 minutes old.

Every lexicon word among the context's annotations becomes one observation per episode, words not introduced yet
included (they start at the prior like any other). An annotation is matched to its entry through its dictionary form
(scripts/inflection.py): T3 glosses every word of a fully Japanese sentence, conjugated verbs whole, so なります counts
for なる, 待っています for 待つ and 出てきます for 出る (or 出てくる, if listed). The word as written wins, then the
candidate the fewest steps back, then the earliest entry; a form is matched only to a real entry of the right kind
(戻せます is not 戻る). Opened words with no entry (燃料, リスト) count toward knowledge.json "unlisted" instead.

- y = 1 if opened (a pin or flip, or a peek open at least 600 ms); y = 0.6 for a shorter peek (a soft label: a
  glance to confirm). T3 peeks under 300 ms are noise (the mouse passing over a word) and are dropped.
- y = 0 if not opened, weighted by the chance it was read. Weight 1 on the lines down to the lowest opened
  annotation; below it the weight halves per line (line height estimated from the annotations' vertical spacing),
  since that part may not have been read. In the browser the weight is at least the read chance from the passage's
  time on screen: r = top * (1 - exp(-active / (pace * words * share))), where active is the ms the passage was on
  screen while a reader could have been reading it (its visible stays: tab focused, no media playing, input in the
  last 30 s), words its word count, and share how far down the word sits (at least 0.1). pace starts at 300 ms per
  word and top at 0.7; a passage never on screen like that gives no evidence at all.
- Unopened words in a T3 sentence whose whole-sentence translation was opened get no observation: you read it in
  English. Words opened in that sentence still count as opened.

### Browser: the chance it was read

With every browser passage in the log, there is no selection to correct for. The update is the gradient of the log
likelihood with P(open) = r * p (an open means it was read):

    opened:      k_w += eta_w * (p_w - y)
    not opened:  k_w += eta_w * r * p_w * (1 - p_w) / (1 - r * p_w)          n_w += r

The reading model (pace, top) can't be checked on its own, but it changes how well opens are predicted. Once a day
the replay is scored on a grid (pace 150-650 ms, top 0.5-0.9) by the mean log loss of P(open) for every listed word
of every passage, each predicted before its own update with the read chance from time on screen alone; the best pair
replaces the defaults once there are 60 browser opens to judge by (knowledge.json "reading").

### Correcting for "only engaged episodes are seen" (T3)

T3 episodes are logged only because something was opened, so a naive update is biased toward "unknown" (a one-word
paragraph exists in the log only when that word was peeked). The likelihood is conditioned on at least one peek.
With Q the predicted chance of no peek at all in the episode, Q = product of (1 - p_j):

    k_w += eta_w * weight * (p_w / (1 - Q) - y)          eta_w = max(0.2, 1.2 / (1 + 0.3 * n_w))
    s_offset (T3) += 0.02 * sum(weight * (y - p / (1 - Q))) / sum(weight)

A one-word episode contributes nothing, as it should. In a simulation (tests/test_weave.py: 600 logged paragraphs
of 3 to 12 glossed words, true peek chances 5%, 25% and 50%) the median estimates land at about 7%, 25-30% and
47-56%. Paragraphs of one to three glossed words carry little information, so they converge slowly. The proximity
weighting assumes readers often stop before the end; for a reader who reads everything it overstates peek chances a
little (25% reads as about 30%). That shifts every word the same way, so the order is kept, and the controller sets
the volume.

## Choosing words (browser)

Selection happens when results are read from the cache, not when Gemini is called: the cache keeps every validated
candidate, so changing estimates or the dial never pays for new Gemini calls.

Per passage, given the count limit N (the density setting or the level rule) and a peek budget B:

1. Learning candidates (active or shaky words) are ordered by learning value: closeness of their predicted peek
   chance to the 25% target on the log-odds scale, with Thompson sampling (k drawn from N(k_w, var_w)) so uncertain
   words still get shown.
2. An active word not yet shown today is admitted first, outside the budget (at most one per passage), so new words
   always gather evidence.
3. Each next learning word is admitted while its p fits the remaining budget; the first that doesn't fit is admitted
   with probability remaining / p (so short passages still get learning words in expectation), then learning stops.
4. Known words fill the rest of N; they cost nothing.

Randomness is seeded by the passage and the day, so a page repaints the same way. Changing the dial repaints open
pages; new estimates and the controller's daily budget apply to passages as they are painted, so words don't change
under you while you read. Selection only chooses among Gemini's candidates, so a dial above what they can supply
saturates (the controller's scale stops at 4).

### The dial and the budget

`challenge` is the target number of peeks per 100 words shown, default 0 (adaptation off: today's behavior). It
lives in the word state's preferences, so the browser and chat share it; set it with `fw.py challenge X`,
`/japanese-weave:challenge X` or the popup.

B = budget per 100 words * passage words / 100, where budget per 100 words = challenge * scale. A daily controller
compares the realized browser peek rate over the last 3 days (peeks per 100 words shown) with the target and moves
scale by sqrt(target / realized), clamped to [0.25, 4], once at least 300 words were shown. The model picks which
words; the controller sets how much.

## Chat

A prompt can't enforce a per-reply budget, so chat only uses the estimates for order: the learning list is sorted by
learning value (most useful first), which is what "use 2 to 4 per reply" draws from.

## Status from evidence

The day rules stay the main path. With the dial on, evidence, when a word has some (n_w >= 2), adds three moves,
using an 80% bound:

- veto: an active word does not graduate while the lower bound of its p is above 0.3;
- shaky -> known: when the upper bound of p is below 0.15;
- known -> shaky: when the lower bound of p is above 0.35 and it was opened in the last 14 days.

Safety: at most 3 evidence moves per direction per day; none within 14 days of a `known` or `learn` command for that
word; nothing at all if the log shrank since the last refresh. Every move is recorded in knowledge.json.

Words not introduced yet are scored but never moved. Instead, whatever the dial, one you opened in at least 2
messages or passages over the last 14 days (knowledge.json "recent") is introduced before the lexicon order when a
learning slot opens, most opened first (fwlib.refill; the 20 slots and 5 new words a day stay). Two encounters, not
two opens: opening the same word twice in one message is one look, while a chat that keeps using a word you don't
know is worth teaching it.

### Rule fixes that don't need the model

- A flip on a known word counts like a peek day (two such days within a week make it shaky), not instant shaky; a
  flip on a learning word restarts its graduation clock.
- Shaky words take learning slots: new words fill the learning set up to 20 active plus shaky words, instead of all
  new words stopping while 5 or more are shaky.

## Where it runs

- `scripts/knowledge.py`: the model (pure replay from the log), selection value helpers, the controller, transitions.
- With a collector, the replay runs there, next to the log and the word state: the collector refreshes
  knowledge.json every 10 minutes, and every other machine gets it with its cached copy of the state (hub.py). The
  collector only answers requests addressed to its own host name, so a web page can't read it through DNS rebinding.
- Without one, `fw.py refresh` replays from scratch, applies transitions in a short state edit, and writes
  knowledge.json. It runs one at a time (a non-blocking lock). The hook starts it in the background when exposure is
  stale; the native host starts it in the background when knowledge.json is over 15 minutes old. Nothing waits on
  the network.
- The native host's state reply carries, per word, k and its uncertainty, and the challenge and budget. They are
  not part of the word-state version, so the extension's cache isn't cleared when they change.

## Intensity and calibration

The reader can turn the Japanese heavier or lighter for a while, separately in chat and on web pages
(scripts/intensity.py): levels -2..2, times 0.5, 0.7, 1, 1.4 or 1.8 the usual amount, for 3 hours after the last
change. Keys Alt+Shift+Period (heavier) and Alt+Shift+Comma (lighter) in the browser and in T3 Code, the popup's
Less/More buttons, or `fw.py intensity [chat|browser] up|down|normal`. In chat the hook scales the target, the cap and
the share of fully Japanese sentences. On web pages a lighter level keeps that share of each passage's cached
candidates (no model call); a heavier one raises the ceiling and asks the model for more (its own cache).

On the web, half the passages are also thinned at random to between 50% and 100% of what fits (levels.js
jitterFor), and every passage's shown record carries its plan: the level, the factors, the swaps allowed and the
candidates offered. Because the thinning is random, comparing thinned and untouched passages measures what the amount
of Japanese alone does to reading time and opens. The comparison counts only passages with candidates (thinning can't
change the others) and includes those thinned to no Japanese at all, which are logged like the rest. Every press is a
`set` op, so the ops log says how often and when the default felt wrong. `fw.py calibrate` reports both
(knowledge.json "calibration"): screen time per word and opens per 100 words by the share of Japanese in a passage,
starting with passages that got none, the thinned-versus-untouched effect (or how far the two groups' shares are
from the 0.5 points it needs), and per surface the presses and hours spent away from normal. Many lighter presses
mean the default is too heavy, and the slowdown curve says how much each extra point of Japanese costs.

## Inspecting

- `fw.py knowledge`: the dial, budget and realized peek rate; the T3 offset; recent evidence moves; the words with
  the most evidence.
- `fw.py why WORD`: its prior, every scored observation with its weight, k over time, and moves. WORD may be a form
  of the word (なります).
- `fw.py status`: the looked-up words lined up to come in next; `fw.py calibrate`: the 30 words opened most in the
  last 30 days that the word list doesn't have, which is what the chat's Japanese sentences use beyond it.

## Decisions from the review

- Dropped the per-context offset and the per-surface baseline: they were not identifiable and amplified a selection
  bias; replaced by the likelihood conditioned on at least one peek.
- Added count-only "shown" records in the browser: the peek-rate dial needs a denominator.
- Priors no longer come from status (circular); commands are timestamped.
- Selection ranks only learning words, explores by Thompson sampling, guarantees new words exposure, and uses a
  stochastic budget so short passages aren't starved; candidates are not overgenerated (that hurt Gemini's fit).
- Evidence moves use uncertainty bounds, are rate-limited, and sit on top of the day rules instead of racing them.
- Log integrity: client ids against resends, a 7-day time window, gap-split episodes, sentence translations.
- Deferred: shown records and retries for T3 (chat has no peek-rate dial yet), merging browser and T3 contexts.
