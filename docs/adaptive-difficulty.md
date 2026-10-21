# Adaptive difficulty

Japanese Weave estimates, for every word, how likely you are to open its gloss, from the gloss interaction log.
It uses those estimates three ways: to decide which words appear on a page or in a reply, to spend each intensity
level's lookup budget (lookups per 100 words, on words not known at a glance), and to move words between learning
and known faster and more reliably than day counts alone. The plan behind it, the simplifications it rests on and
what comes next: [plan.md](plan.md).

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
  every glossed word in it as [word, x, y] relative to the context, its word count and when it was shown ("shown":
  in the browser when its Japanese was first on screen; T3 sends one per message with its prose words). T3 messages
  logged before that get their word count from the transcript and have no shown time. The words of shown browser
  paragraphs are the denominator of the peek rate.
  Paragraphs planned to get Japanese that got none (no candidates fit, or the random thinning left none) are shown
  too, with no glossed words, when enough of them is on screen: the baseline for calibration. They don't count
  toward the peek rate and carry no evidence about words.
- reading: evidence of where the reader was, in contexts with glossed words (browser paragraphs, T3 messages): clicks
  (point, link or text), text selections (box, length, the indexes of the glossed words covered, drag time), mouse
  paths over T3 messages ([ms, x, y] points), and stays on screen of each paragraph (its band in the context; ms of
  possible reading, plus the time away, with media playing, or idle for 30 s; in T3 the paragraph's word count, from
  the build that sends them), also of paragraphs left without Japanese. T3 logs a stay for every paragraph with
  glossed words that was on screen for a second, opened or not. The model below uses the stays; clicks, selections
  and mouse paths not yet.

Commands (`known`, `learn`) are kept with their time in `commands.jsonl` so a replay can place them.

## The model

For a glossed word w shown on surface s (a browser passage, or a T3 message):

    P(peek) = sigmoid(s_offset - k_w(t))

- k_w: knowledge of w on a log-odds scale. Its uncertainty is var_w = 2 / (1 + n_w), where n_w is the evidence
  weight seen so far, which itself decays (half-life 30 days) so a word unseen for a while becomes uncertain again.
- Forgetting: between observations, k_w decays toward its prior mu_w with a half-life of 45 days, but only
  downward (knowledge fades; ignorance doesn't turn into knowledge by waiting).
- s_offset: 0 for the browser; a learned offset for T3 (its popover opens after 0.3 s instead of 0.7 s), moved by
  0.02 times the mean gradient per message and clamped to [-1.5, 1.5].
- Priors from outside the model only: Anki interval i gives mu = min(3, 0.5 + 0.8 * ln(1 + i)); any other word 1.0.
  A `known` command raises k to at least 2.5 (and adds evidence); `learn` lowers it to at most 0.

### Episodes and observations

Every browser passage shown (its "shown" record, one per passage per page view) and every T3 message with a word
count is scored, opened or not, once 10 minutes have passed since the last thing that happened in it (its shown
record, its last stay on screen, its last open). A T3 message without a word count (logged before T3 sent them and
not backfilled) falls back to episodes: the interactions in one (surface, view, context) with no gap over 10
minutes, scored once their last interaction is 10 minutes old. Only messages with an open have episodes, so those
are corrected for that (below).

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
- In a T3 message the same formula runs on the paragraph the word is in: the stays whose band holds the word's top
  (from half a line, at most 10 px, above the band down to its bottom), their ms added; words is the paragraph's
  count when T3 sent it, else the message's words per pixel of its height times the band's height, at least 1 (the
  message's words include paragraphs without Japanese, which T3 doesn't watch, so dividing by the watched bands
  alone would overstate each; without a message height, the summed height of the distinct bands is used); share is
  how far down the band the word sits. A word whose paragraph has no stay gets 0 from this, so only the line rule from the
  words opened can weigh it.
- Unopened words in a T3 sentence whose whole-sentence translation was opened get no observation: you read it in
  English. Words opened in that sentence still count as opened.

### Passages and messages shown: the chance it was read

With every browser passage and every T3 message with a word count in the log, there is no selection to correct for.
The update is the gradient of the log likelihood with P(open) = r * p (an open means it was read):

    opened:      k_w += eta_w * (p_w - y)
    not opened:  k_w += eta_w * r * p_w * (1 - p_w) / (1 - r * p_w)          n_w += r

The T3 offset follows the same gradients with respect to it, per message: s_offset (T3) += 0.02 * (sum over opened
words of (y - p) - sum over the others of r * p * (1 - p) / (1 - r * p)) / (sum of the weights, 1 for an open).

The reading model (pace, top) can't be checked on its own, but it changes how well opens are predicted. Once a day
the replay is scored on a grid (pace 150-650 ms, top 0.5-0.9) by the mean log loss of P(open) for every listed word
of every passage, each predicted before its own update with the read chance from time on screen alone; the best pair
replaces the defaults once there are 60 browser opens to judge by (knowledge.json "reading"). Only browser passages
are scored, but the pair is the reader's pace, so T3 messages use it too.

### Correcting for "only engaged episodes are seen" (T3 messages without a word count)

Episodes exist only because something was opened, so a naive update is biased toward "unknown" (a one-word
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

Selection happens when results are read from the cache, not when the model is called: the cache keeps every validated
candidate, so changing estimates, the budget or the level never pays for new model calls.

A level is a lookup budget (scripts/intensity.py BUDGETS, mirrored in levels.js): the lookups per 100 words the reader
puts up with, spent only on words not known at a glance. Per passage, with B = the level's budget per 100 words *
jitter (the random thinning, below) * passage words / 100, and one use per Japanese word:

1. Each candidate's listed words get their estimate (k and sd from knowledge.json; without one, k = 2.5, fwlib
   KNOWN_PRIOR_K, for a known word, else the neutral prior) and a mean lookup chance p. A candidate whose words all
   have p < 0.08 (fwlib FREE_P) is free: shown wherever the model offered it, at every level but off, except that the
   random thinning keeps each with probability jitter.
2. The others cost the sum of p over their words not known at a glance, and are ordered by learning value: closeness
   of the least-known word's predicted lookup chance to the 25% target on the log-odds scale, with Thompson sampling (k
   drawn from N(k_w, var_w)) so uncertain words still get shown.
3. One with an active word not yet shown today is admitted first, outside the budget (at most one per passage), so new
   words always gather evidence.
4. Each next one is admitted while its cost fits the remaining budget, at most PER_SENTENCE in one sentence (one up to
   normal, two when heavier; a full sentence passes to the next candidate); the first that doesn't fit is admitted
   with probability remaining / cost (so short passages still get learning words in expectation), then it stops.
5. The popup's density, when set, caps the total, budgeted candidates first; without it there is no other ceiling.

The passage's plan records B and the lookups expected (the sum of p over every listed word shown). Randomness is seeded
by the passage and the day, so a page repaints the same way. Changing the challenge or the level repaints open pages;
new estimates and the controller's daily budget apply to passages as they are painted, so words don't change under you
while you read. Selection only chooses among the model's candidates, so a budget above what they can supply saturates
(the controller's scale stops at 4).

### The dial and the budget

`challenge` is the target number of peeks per 100 words shown, default 0: normal's budget is then intensity.py's 0.5
lookups per 100 words, unsteered. It lives in the word state's preferences, so the browser and chat share it; set it
with `fw.py challenge X`, `/japanese-weave:challenge X` or the popup.

Normal's budget per 100 words = challenge * scale (0.5 * 1 without a challenge), sent by the native host as `budget`;
the other levels scale with it (intensity.budget). A daily controller compares the realized browser peek rate over the
last 3 days (peeks per 100 words shown) with the target and moves scale by sqrt(target / realized), clamped to
[0.25, 4], once at least 300 words were shown. The model picks which words; the controller sets how much.

## Chat

A prompt can't enforce a per-reply budget exactly, so the brief (weave.py) asks for an amount the budget pays for and
the next turn says how the last reply did. Each word in use gets a chat lookup chance p: its estimate with the T3
offset, else from its status (known: k = 2.5, fwlib.KNOWN_PRIOR_K; otherwise the neutral prior). Words with p under
8% (fwlib.FREE_P) are known at a glance, whatever their status: they cost nothing and go in wherever the English has
their meaning, listed as swaps or, from 150 of them, as a band whose exceptions name every band word that isn't one
of them and isn't listed as learning. The rest are learning words, known ones included: up to 24, by learning value
(most useful first), grouped by p with rough odds (often, about 1 in 4; sometimes; rarely). The brief asks for the
budget over their mean p per 100 words (to the nearest half under 2; at least 0.5, at most 5 per word allowed in a
sentence and 8 in all), at most intensity.per_sentence in a sentence, where the English around each makes its meaning
clear. Fully Japanese sentences (Stage 3 on) may use only known words, particles and です/ます, and no stage may use
Japanese outside the lists. The line on the last reply counts its known, learning and off-list words and its expected
lookups per 100 words: p summed over the learning (and unlisted) words used, plus 0.27 (the neutral prior) per
off-list word, over its words; known words are left out, being free. Off-list Japanese gets "no Japanese outside the
lists"; else over 1.5 times the budget asks for fewer learning words, and under half of it (or of what the most the
brief asks for would spend) for more.

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
  new words stopping while 5 or more are shaky. A learning or shaky word known at a glance (browser p under 8% in
  knowledge.json) keeps its status but takes no slot; without an estimate it takes one.

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
(scripts/intensity.py): levels -3..3, for 3 hours after the last change. -2..2 are times 0.5, 0.7, 1, 1.4 or 1.8
the usual amount; the ends are modes of their own, -3 off (no Japanese) and 3 full (all the reader can follow), and a
press past either end stays there and restarts the clock. Keys Alt+Shift+Period (heavier) and Alt+Shift+Comma
(lighter) in the browser and in T3 Code, the popup's Less/More buttons, or
`fw.py intensity [chat|browser] up|down|normal|off|full`. On both surfaces -2..2 are lookup budgets of 0.15, 0.3,
0.5, 0.8 and 1.2 per 100 words (intensity.BUDGETS; scaled with normal's when a challenge is set), spent only on words
not known at a glance: known words are free everywhere but off. In chat (see Chat above) the level also scales the
share of fully Japanese sentences. On web pages (see Choosing words) a lighter level picks from each passage's cached
candidates (no model call), a heavier one from the full answer (one answer and one cache for +1, +2 and full; asked for
more on their own, the heavier levels got little more than normal, see docs/swap-prompt.md).

In chat, off replaces the brief with a one-line note to reply in plain English, without the recall cue, the
calibration line or the export notice. Full replaces the budget with two rules: a sentence the reader could
follow in plain Japanese (listed words plus common grammar, plain desu/masu) is written fully in Japanese, and one
that must stay English (steps, code, commands, paths, exact technical detail) swaps in every listed word, known or
learning. The rules on zero-Japanese topics (health, money, safety and the like), on Japanese only in the chat reply,
and on glosses stay as at every other level; there is no calibration line (no budget to correct toward). A chat
turned off on its own (threads.py) stays off at any level.

On the web, half the passages are also thinned at random by a share between 50% and 100% (levels.js jitterFor), which
keeps each free word with that chance and scales the budget by it, and every passage's shown record carries its plan:
the level, its factor, the share, the swaps kept, the candidates offered, the budget and the lookups expected.
Because the thinning is random, comparing thinned and untouched passages measures what the amount
of Japanese alone does to reading time and opens. The comparison counts only passages with candidates (thinning can't
change the others) and includes those thinned to no Japanese at all, which are logged like the rest. Every press is a
`set` op, so the ops log says how often and when the default felt wrong; one into off counts as lighter and one
into full as heavier, like any other step. Presses less than a minute apart count as one decision, judged by where
they end (trying the keys, or overshooting and stepping back, isn't evidence), and a level that ran out counts as
normal before the next press. `fw.py calibrate` reports both
(knowledge.json "calibration"): screen time per word and opens per 100 words by the share of Japanese in a passage,
starting with passages that got none, the thinned-versus-untouched effect (or how far the two groups' shares are
from the 0.5 points it needs), the chat side (T3 messages with a word count: lookups per 100 words, how many
messages had none, and the lookups of listed words the model expected, read chance from the stays times p before
each message's update, against those seen: how well calibrated the model is on chat), and per surface the presses
and hours spent away from normal. Many lighter presses mean the default is too heavy, and the slowdown curve says how
much each extra point of Japanese costs.

On web pages the scale goes one step further each way, -3..3. Off (-3) is no Japanese at all: woven pages go back to
English at once, no passage goes to a model (not even one already queued), and no shown records, stays or other
interactions are logged, since there is no Japanese to measure. Full (+3) has its own cache and prompt, which the
heavier levels (+1, +2) pick from too: no per-passage target, a candidate for every listed word, learning or known,
the passage says in its listed sense (in every place it says it), and a sentence swap for every sentence listed words
and the allowed grammar can say completely, at any stage. planSwaps shows every validated candidate, sentences before
the word swaps inside them, with no ceiling, budget or random thinning (the plan says level 3, intensity 5, jitter 1,
and has no budget). Both ends last 3 hours after the last press like the other levels; a press further at an end
restarts the clock.

## Inspecting

- `fw.py knowledge`: the dial, budget and realized peek rate; the T3 offset; recent evidence moves; the words with
  the most evidence.
- `fw.py why WORD`: its prior, every scored observation with its weight, k over time, and moves. WORD may be a form
  of the word (なります).
- `fw.py status`: the looked-up words lined up to come in next; `fw.py calibrate`: the 30 words opened most in the
  last 30 days that the word list doesn't have, which is what chat replies slip in beyond it.

## Decisions from the review

- Dropped the per-context offset and the per-surface baseline: they were not identifiable and amplified a selection
  bias; replaced by the likelihood conditioned on at least one peek.
- Added count-only "shown" records in the browser: the peek-rate dial needs a denominator.
- Priors no longer come from status (circular); commands are timestamped.
- Selection ranks only learning words, explores by Thompson sampling, guarantees new words exposure, and uses a
  stochastic budget so short passages aren't starved; candidates are not overgenerated (that hurt Gemini's fit).
- Evidence moves use uncertainty bounds, are rate-limited, and sit on top of the day rules instead of racing them.
- Log integrity: client ids against resends, a 7-day time window, gap-split episodes, sentence translations.
- Deferred: retries for T3 (chat has no peek-rate dial yet), merging browser and T3 contexts. T3 shown records
  (word counts) came later, so chat messages without lookups count too.
