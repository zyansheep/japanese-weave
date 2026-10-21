# Adaptive difficulty

Japanese Weave estimates, for every word, how likely you are to open its gloss, from the gloss interaction log.
It uses those estimates three ways: to decide which words appear on a page or in a reply, to spend each intensity
level's lookup budget (lookups per 100 words, on words not known at a glance), and to move words between learning
and known faster and more reliably than day counts alone. The plan behind it, the simplifications it rests on and
what comes next: [PLAN.md](../PLAN.md).

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
- Unopened words in a sentence whose whole-sentence translation was opened (its 。 is among the annotations; in T3,
  and on the web since 2026-10-20) get no observation: you read it in English. Words opened in that sentence still
  count as opened.

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
puts up with, spent only on words not known at a glance and on whole sentences in Japanese. Planning assumes the
passage is read: B and every cost below are lookups if it is read. Per passage, with B = the level's budget per 100
words * passage words / 100 (the random thinning, below, leaves it alone), and one use per Japanese word:

1. Only the candidates the passage can place count, found as the page will find them (text.js locate), and of two
   that overlap only the first one kept. Each candidate's listed words get their estimate (k and sd from
   knowledge.json; without one, k = 2.5, fwlib KNOWN_PRIOR_K, for a known word, else the neutral prior) and a mean
   lookup chance p. A word or phrase whose words all have p < 0.08 (fwlib FREE_P) is free: shown wherever the model
   offered it, bigger swaps first, at every level but off, except that the random thinning keeps each with
   probability jitter.
2. The others cost the sum of p over their words not known at a glance; a whole sentence also costs its price, the
   chance its translation is opened if it is read (below). They are ordered by learning value: closeness of the
   least-known word's predicted lookup chance to the 25% target on the log-odds scale, with Thompson sampling (k drawn
   from N(k_w, var_w)) so uncertain words still get shown; for a sentence of free words, closeness of its price.
3. One with an active word not yet shown today is admitted first, outside the budget (at most one per passage), so new
   words always gather evidence.
4. B has two parts: whole sentences may spend up to SENTENCE_SHARE of it (18%), words and phrases the rest
   (levels.js, as intensity.py split says for chat). Going down the one ranking, each is admitted while its cost fits
   what is left of its part, at most PER_SENTENCE in one sentence (one up to normal, two when heavier); a whole
   sentence only in a sentence with nothing budgeted yet, and then nothing more there (a full sentence passes to the
   next candidate). A part stops at its first candidate that doesn't fit. A part whose candidates ran out first hands
   what it has left to the other, which goes on from where it stopped. Then each part's first candidate that didn't
   fit is admitted with probability remaining / cost (so short passages still get learning words in expectation). A
   sentence left English keeps the free words the model offered inside it.
5. The popup's density, when set, caps the total, budgeted candidates first; without it there is no other ceiling.

The price of a whole sentence comes from the native host (`sentencePrice`, from knowledge.json once the fit makes one
per surface); until then it is levels.js SENTENCE_PRICE, 0.08. That number is from chat, because the web did not log
a sentence's translation being opened until 2026-10-20: of 439 sentence ends in the 123 T3 replies with a word count
(2026-10-15 to 20), 24 were opened, 5.5% a sentence shown, and 8.4% a sentence read when each is weighed by its read
chance as the replay weighs words (90% interval 5.4% to 11.4%, resampling replies). Before, how many sentences a
passage got followed the stage (the prompt asked for one in five from Stage 3, one in ten at Stage 2, none at Stage
1) and they were free when their words were.

The share keeps whole sentences in the plan whatever the supply of learning words. Ranked like a word whose lookup
chance is their price, far from the 25% target, they would otherwise get only what learning words leave: all of the
budget on the web today, where learning words are scarce, but nothing once code finds enough candidates. SENTENCE_SHARE
is 0.18 so that normal asks chat for as many sentences as its brief did at Stage 3 before they were priced: one in
five, about 1.1 per 100 words (about 5.5 prose sentences in 100 words), at 0.08 each is 0.088 of normal's 0.5, and
0.18 * 0.5 / 0.08 = 1.1. (The 123 counted T3 replies held 0.73 whole sentences per 100 words, counting every word of
the replies, code included.)

The passage's plan records B, the lookups expected (the sum of p over every listed word shown, plus the price of each
sentence shown) and the sentence price. Randomness is seeded by the passage and the day, so a page repaints the same
way. Changing the challenge or the level repaints open pages; new estimates and the controller's daily budget apply to
passages as they are painted, so words don't change under you while you read. Selection only chooses among the model's
candidates, so a budget above what they can supply saturates (the controller's scale stops at 4).

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
offset, else from its status (known: k = 2.5, fwlib.KNOWN_PRIOR_K; otherwise the neutral prior). Words with p under 8%
(fwlib.FREE_P) are known at a glance, whatever their status: they cost nothing and go in wherever the English has
their meaning. Numbers and katakana words are left out of the brief, as it asks for plain English for numbers and no
katakana loanwords. The first 12 of the rest that are connectors or adverbs (tier A in data/lexicon.csv, in its
order) are the default swaps, written in Japanese every time. The others are named with their English, the senses
joined by a slash ("中 inside/during"), the most useful in chat first (weave.priority): those that came up in the
user's chat replies on 2 days or more (exposure.json), then the base list's others in its order (frequency, with
connectors and the words technical chat uses early), then the Anki words, whose meanings are their cards' and often
odd. They take at most 4,400 characters (weave.KNOWN_ROOM, 250 to 290 words; on the 2026-10-20 state all 255 fit). The
ones left out are still known at a glance: the brief says how many there are and to keep the English for any word not
named, and a reply that uses one anyway is counted as using a known word, free, not as Japanese off the lists. No kana
readings: in the T3 chats with `<abbr>` glosses, Claude's own reading matched the list's in 400 of 401 glossed uses of
listed kanji words, so a reading per word (about 1,000 characters for 212 kanji words) would buy little.

The rest of the words in use are learning words, known ones included: up to 24, by learning value (most useful first),
grouped by p with rough odds (often, about 1 in 4; sometimes; rarely). The budget is split as on the web
(intensity.split): whole sentences in Japanese may take up to 18% of it (SENTENCE_SHARE) at their price, the chance
the reader opens the translation (knowledge.json sentencePrice for t3 once a fit makes one, else
intensity.SENTENCE_PRICE, 0.08, the same as levels.js), learning words the rest, and what one can't use goes to the
other. Learning words can use at most 5 per 100 words per word allowed in a sentence (8 at most) at their mean p, and
none without learning words; sentences at most 3 per 100 words (weave.SENTENCES_MAX, three in five, the most the brief
asked for before), and none without a word known at a glance to write them with. The brief asks for each part over
its cost per 100 words, to the nearest half under 2 and the nearest whole number from there (learning words at least
0.5): at normal on the 2026-10-20 state, 2 learning words and 1 whole sentence per 100 words, where before it asked
for 3 learning words and, at Stage 3, one sentence in five unpriced. Learning words go at most intensity.per_sentence
in a sentence, where the English around each makes its meaning clear. Whole sentences, at any stage now, are short
and plain です/ます, made of known words, particles and これ/それ only; every other sentence stays English with listed
words as written (below Stage 3 also never conjugated and with no particles around them), and no stage may use
Japanese outside the lists. Full is the same brief with the budget lifted: the same lists, every sentence the user
could follow written whole in Japanese (learning words and common grammar allowed), and every listed word wherever the
English has its meaning, steps included, with no cap per sentence; one line of the rules says so.

The hook's text stays in the conversation, so a chat gets the whole brief once (weave.chat_turn, with each chat's
memory in sessions.json): the rules, the learning words, the default swaps and the named known words. Later turns
carry a digest of the rules with this reply's amounts, the learning words, the default swaps, and only the known words
that changed since (newly known at a glance or newly among the most useful, or no longer known at a glance). The rules
come whole again when they change (another stage, other glosses or example sentences); a switch to or from full needs
only the digest, which carries the amounts. The whole brief comes back after a compaction, which Claude Code marks
with a `compact_boundary` system line in the transcript and Codex with a `compacted` line, and every 20 turns
(weave.FULL_EVERY) in case the context lost it without a mark. A brief stays under 9,500 characters, since Claude Code
files away hook text over 10,000: from Stage 3 the rest of a whole brief takes at most about 4,900, so the named
known words always fit, and a word that doesn't (a stage pinned lower has longer rules) is named on the next turn. On
the 2026-10-20 state (Stage 3, 293 words known at a glance, `<abbr>` glosses, with the feedback line), the brief
before it named known words was about 4,000 characters on every turn; the whole brief is 8,128 and a later one 2,294
(at full 7,597 and 1,786). With that state's other words made known, it is 8,737 at 500 known words and 8,717 at
1,500, and later turns stay the same, where naming every word took the whole 9,500 on every turn until all were
named (5 turns at 1,500).

Each brief is kept as a record of what its reply was offered: a `brief` op (hub.py), which changes nothing and goes to
the ops table where the state lives, through the outbox with a collector. Its fields follow a web passage's plan:
level, intensity, budget, the learning words and whole sentences asked for per 100 words (ask, sentences), the price
of a whole sentence (sentencePrice), the costly words offered with their p, the delayed-start arms (empty until that
experiment starts), the expected lookups (ask times their mean p, plus sentences times their price), per 100 words
where the web's are per passage; and the session id, the words known at a glance, whether the brief was whole, its
length and, from Codex, the turn id. Full has no budget, ask, sentences or expected. feedback.chat_briefs reads them
back, and feedback.brief_for finds a reply's: the last of its chat sent at or before it.

The line on the last reply counts its known, learning and off-list words, its whole sentences in Japanese (a run from
a sentence break to 。, ？ or ！ with no Latin letters in it), and its expected lookups per 100 words: p summed over the
learning (and unlisted) words used, plus 0.27 (the neutral prior) per off-list word and the price per whole sentence,
over its words. Known words are left out, being free, whether the brief named them or not. Japanese inside code is
left out too: fenced blocks of backticks or tildes (an unclosed one runs to the end), code spans of any number of
backticks, and links (fwlib.strip_code). The particles and です/ます of a whole sentence are never off the lists; other
hiragana is, below Stage 3. Off-list Japanese gets "no Japanese outside the lists"; else over 1.5 times the budget
asks for fewer learning words, whole sentences or both, whichever spent over 1.5 times its part, and under half of
what the brief asks for at most for more of whichever spent under half its part.

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
not known at a glance and on whole sentences in Japanese, which may take up to 18% (SENTENCE_SHARE): known words are
free everywhere but off. On web pages (see Choosing words) a lighter level picks from each passage's cached
candidates (no model call), a heavier one from the full answer (one answer and one cache for +1, +2 and full; asked for
more on their own, the heavier levels got little more than normal, see docs/swap-prompt.md).

In chat, off replaces the brief with a one-line note to reply in plain English, without the recall cue, the
calibration line or the export notice. Full is the normal brief with the budget lifted, said in one line: a sentence
the reader could follow in plain Japanese (listed words plus common grammar, plain desu/masu) is written fully in
Japanese, and the others swap in every listed word, known or learning, steps included, with no cap per sentence. The
lists, the rules on zero-Japanese topics (health, money, safety and the like), on Japanese only in the chat reply,
and on glosses stay as at every other level; there is no calibration line (no budget to correct toward). A chat
turned off on its own (threads.py) stays off at any level.

On the web, half the passages are also thinned at random by a share between 20% and 100% (levels.js jitterFor), which
keeps each free word with that chance; the budget and the words it pays for are the same in thinned and untouched
passages. Every passage's shown record carries its plan: the level, its factor, the share, the swaps kept, the
candidates offered, the budget and the lookups expected. Because the thinning is random, comparing thinned and untouched
passages measures what words known at a glance alone do to reading time and opens (PLAN.md row F). Until 2026-10-20 the
share ran from 50% to 100% and scaled the budget too: thinned passages came to 5.3% Japanese against 6.5%, too little
contrast to read, and what free words cost was mixed with what the budgeted ones do. The comparison counts only passages
with candidates (thinning can't change the others) and includes those thinned to no Japanese at all, which are logged
like the rest. Every press is a `set` op, so the ops log says how often and when the default felt wrong; one into off
counts as lighter and one into full as heavier, like any other step. Presses less than a minute apart count as one
decision, judged by where they end (trying the keys, or overshooting and stepping back, isn't evidence), and a level
that ran out counts as normal before the next press. `fw.py calibrate` reports both (knowledge.json "calibration"):
screen time per word and opens per 100 words by the share of Japanese in a passage, starting with passages that got
none, the thinned-versus-untouched effect (or how far the two groups' shares are from the 0.5 points it needs), the chat
side (T3 messages with a word count: lookups per 100 words, how many messages had none, and the lookups of listed words
the model expected, read chance from the stays times p before each message's update, against those seen: how well
calibrated the model is on chat), and per surface the presses and hours spent away from normal. Many lighter presses
mean the default is too heavy, and the slowdown curve says how much each extra point of Japanese costs.

On web pages the scale goes one step further each way, -3..3. Off (-3) is no Japanese at all: woven pages go back to
English at once, no passage goes to a model (not even one already queued), and no shown records, stays or other
interactions are logged, since there is no Japanese to measure. Full (+3) has its own cache and prompt, which the
heavier levels (+1, +2) pick from too: no per-passage target, a candidate for every listed word, learning or known, the
passage says in its listed sense (in every place it says it), and a sentence swap for every sentence listed words and
the allowed grammar can say completely. planSwaps plans it the same way as the other levels with no budget, so nothing
costs anything: every candidate the passage can place is shown, a sentence rather than the word swaps inside it, with no
per-sentence cap, density cap or random thinning, and a word again where its English comes again (the plan says level 3,
intensity 5, jitter 1, and has no budget). Both ends last 3 hours after the last press like the other levels; a press
further at an end restarts the clock.

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
