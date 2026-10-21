# Adaptive difficulty

Japanese Weave estimates, for every word, how likely you are to open its gloss, from the gloss interaction log.
It uses those estimates three ways: to decide which words appear on a page or in a reply, to spend each intensity
level's lookup budget (lookups per 100 words, on words not known at a glance), and to move words between learning
and known faster and more reliably than day counts alone. The plan behind it, the simplifications it rests on and
what comes next: [PLAN.md](../PLAN.md).

This is version 2: since NEXT_STEPS step 4, one penalized fit of the whole log on every refresh replaces the online
updates of version 1. The first draft was reviewed adversarially (statistics and engineering), and the plan behind
version 2 too; the decisions those reviews forced are listed at the end.

## Background

- Comprehension: unassisted reading needs about 98% of running words known (Hu & Nation 2000; Nation 2006), so
  unfamiliar Japanese should stay near 2 per 100 words; words you know add volume at no cost.
- Difficulty: practice works best when most attempts succeed but not all. Japanese Weave aims learning words at
  a predicted peek chance near 25% (a tunable target, not a law).
- Implicit evidence: a peek shows you were reading that spot, so unopened glossed words around a peek are
  evidence you knew them. Web-search click models make the same split between "examined" and "relevant"
  (Craswell et al. 2008); LingQ's "unclicked words become known on page turn" is the naive form.
- Estimation: a logistic model with a random effect per word (an explanatory item response model, De Boeck &
  Wilson 2004), fitted by penalized maximum likelihood with each word held toward a regression on its features; the
  per-word uncertainty from the fit's curvature (a Laplace approximation). Version 1 used Elo-style online updates
  (Pelánek 2016) with forgetting (Settles & Meeder 2016).

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

For a glossed word w shown on surface s (a browser passage, or a T3 message), written as itself or as a conjugated
form of it (f = 1):

    P(lookup | read) = sigmoid(offset_s + form * f - k_w)

- k_w: knowledge of w on a log-odds scale. It is on the web's scale: the web's offset is 0, the reference.
- offset_t3, chat's: fitted (T3 opens a gloss after 0.3 s, the web after 0.7 s, and chat uses words differently).
- form: fitted too. A conjugated form is looked up for its ending as well as for its word.
- A word is opened only if it was read: P(open) = P(read) * P(lookup | read) (below).

### One fit of the whole log

Every refresh fits the whole log at once (knowledge.Fit; evaluate.CURRENT is "fit"). This replaced online updates
that moved k a step per observation and pulled it back toward its prior between observations: with that pull a word
whose true chance is 5%, read 3 times a day, settled at 11%, and without it the steps needed hundreds of exposures. The
fit minimizes, over every word's k and the shared parameters:

- the observations' negative log likelihood, each weighted by 0.5^(age / 30 days), so old evidence counts less and a
  word unseen for a while grows uncertain again. Nothing pulls k itself;
- for every word with evidence, (k_w - m_w)^2 / 2 tau^2: a Gaussian prior around its prior mean m_w;
- the commands' terms, and each shared parameter's own prior (knowledge.SHARED), which holds it while data is thin.

The prior mean is a regression on the word's features (knowledge.features), fitted along with everything else:

- with an Anki card: a_status + b ln(1 + interval in days). The old prior, min(3, 0.5 + 0.8 ln(1 + interval)), stopped
  rising at a 21-day interval, but live lookups per read keep falling past it: about 7.6% for intervals of 20 to 57
  days, 5.0% for 58 to 89, 2.2% beyond. Here the interval is uncapped and the slope fitted;
- without a card: c_status;
- status is the word's on the fit's day: known, learning (active or shaky) or new (not introduced yet). The interval
  alone mixes words that read differently: in the simulated reader, young Anki cards in the learning set and the queue
  words chat uses topically share intervals, but are looked up about 5% and 60% of the time when read. Status is not
  wholly outside the log (three clean days graduate a word, two peek days make it shaky), so it counts some evidence
  twice; NEXT_STEPS step 8 makes status come from the model, and then status must leave the prior;
- each coefficient has a prior: the old Anki formula (a = 0.5, sd 0.5; b = 0.8, sd 0.25) for card words, the old
  neutral prior (1.0, sd 1) for others, and fwlib.KNOWN_PRIOR_K (2.5, sd 1) for known words without a card. A kind with
  no data keeps what the planner assumed, and the planner now takes every word's estimate from the fit, so the two no
  longer disagree about known words without history (the old replay started them at 27%, the planner at 7.6%); and a
  few observations can't swing a kind (with weak priors, one two-word episode moved the card regression by more than 1
  on the log-odds scale);
- later word features (NEXT_STEPS step 13: frequency rank, kanji shared with known words, script, word type, length)
  are each one more shared parameter in features() and SHARED.

The prior's width tau is fitted per kind, Anki card or none, by EM: each word's squared distance from its prior mean
plus its variance, averaged, held toward 1.0 (card) and 1.5 (none) by 5 words' worth of weight. On the 2026-10-20 log
the widths came out 0.75 and 1.02.

Identification: the web's offset is fixed at 0, so the level of k is set by the prior regression; chat's offset is
identified by the words seen on both surfaces and through the shared prior means, and held toward 0 (sd 0.5).

Fitted on the 2026-10-20 log (sd in brackets): chat's offset +0.20 (0.22) and the form offset -0.04 (0.56); with an
Anki card, a = 0.85 for new words (0.31), 0.41 for learning (0.33) and 0.60 for known (0.34), and b = 0.63 (0.08), so a
known word's prior chance is 35% at no interval, 7.3% at 21 days, 4.0% at 60 and 2.3% at 150; without a card, 1.83 for
new words (0.33; 14%) and 0.46 for learning (0.60); known words without a card keep 2.5, as there are none.

Commands are one-sided pseudo-observations. `known` adds 4 (3.5 - k)^2 / 2 while k is under 3.5 (a curvature of 4, an
sd of 0.5; a lookup chance of 2.9%), `learn` 4 (k - 0)^2 / 2 while k is over 0; both fade like evidence. One command
outweighs a few lookups: a single lookup carries about 0.05 of information at a 5% chance. A known word with no other
evidence lands at k 3.25 to 3.4, sd 0.47, inside the free set.

A conjugated form's observation (evaluate.is_form: an annotation that is neither the entry as written nor its reading)
counts 0.3 of a plain one for its word, besides having its own offset. Why 0.3: a form is looked up for its ending as
well as its word. On the 2026-10-20 log forms were opened at 10.4% a read, against 5.1% for the same words written as
themselves, so about half of a form's lookups were about its word; in the simulated reader a recognised word's form is
looked up 5 times as often (0.1 against 0.02), so about a fifth. 0.3 lies between. Without it, する and 行く left the
free set after one form lookup each (PLAN.md row A); with it they stay (k 3.1 and 2.9).

Each word's sd is its k's entry of the inverse information at the fit (Laplace), the shared parameters' uncertainty
included. A word without evidence has its prior: k = m_w, sd = tau. knowledge.json has every word of the word state:
its k, sd, prior mean (mu), free flag (below), evidence weight (n) and, with evidence, its observations; and "prior",
the regression's coefficients and widths by kind, which the native host uses for a word knowledge.json hasn't seen.

How it is solved: Newton's method with the expected information, which is positive definite (the observed one isn't
everywhere: log(1 - r q) is not concave in k when q is large), with step halving. The information has one row per word,
joined to the others only through the shared parameters, so the Schur complement on the shared parameters solves each
step in time linear in the words. A word's unopened observations with the same read weight (rounded to 0.02) pool, so
the work grows with the words more than with the log. A refresh starts from the last one's fit. On the 2026-10-20 log
(3,396 observations, 397 words, 1,870 pooled terms) a fit takes 0.07 s from scratch and 0.01 s from the last one; on a
simulated log of 60 days (52,000 observations, 1,244 words, 16,563 terms), 0.43 s and 0.05 s.

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
Each observation is a Bernoulli with P(open) = w q (q = P(lookup | read)), where w is its read weight: 1 when opened
(an open means it was read), else the larger of the line rule and the read chance from time on screen. So an opened
word adds y log q + (1 - y) log(1 - q) (y below 1 for a glance), and an unopened one log(1 - w q).

The reading model (pace, top) can't be checked on its own, but it changes how well opens are predicted. Once a day
(knowledge.json "reading") it is chosen by profile likelihood over a grid of paces 150 to 650 ms and caps 0.5 to 0.9,
on both surfaces: at each point the whole log is fitted at that pace and cap, then scored by the likelihood of what was
opened under the reading model the read chances come from. A reader reads a passage or message down to one depth, so
everything with a higher read chance than an open was read: with its words ordered by read chance r_1 >= ... >= r_n,
reading exactly the first j happens with chance r_j - r_(j+1), and each read word is opened with its chance q
(knowledge.nested_loglik). Scoring each word on its own, as version 1 did, can't tell the cap from the level of the
chances: it drifted to the grid's edge. The search scores every other point first, then the neighbours of the best
until it stays. The best pair replaces the defaults (300 ms, 0.7) once there are 60 opens, it beats them by 3 log
likelihood units (about a 95% test of two parameters), and it lies inside the grid.

On the simulated reader (true pace 300 ms, cap 0.7) the profile picks 300 ms and 0.7 on two logs (seed 2 at 28 days,
seed 1 at 60) and 200 ms and 0.6 on two others (seeds 1 and 3 at 28 days), a neighbour along the ridge where a slower
pace trades against a higher cap, 2.0 and 3.9 log likelihood units above the truth; on seed 1 the margin keeps the
defaults. On the live log of 2026-10-20 the profile has no maximum in the plausible range: it keeps rising past 650 ms,
to 5,000 ms a word at a cap of 0.7 and beyond. Time on screen there doesn't say how much was read the way the model assumes
(stays include time the reader spent elsewhere, and every chat reply's paragraph words so far are estimates from a
backfill, PLAN.md row I), so the defaults stay until the reading data can be trusted.

### Correcting for "only engaged episodes are seen" (T3 messages without a word count)

Episodes exist only because something was opened, so a naive likelihood is biased toward "unknown" (a one-word
paragraph exists in the log only when that word was peeked). The likelihood is conditioned on at least one open: with
Q the chance of no open at all in the episode, Q = product of (1 - w_j q_j), the episode adds the sum of its
observations' terms minus log(1 - Q). A one-word episode contributes nothing, as it should. Its read weights are the
line rule's alone. Version 1 tested the same correction as an online update in a simulation (tests/test_weave.py: 600
logged paragraphs of 3 to 12 glossed words, true peek chances 5%, 25% and 50%): the median estimates landed at about
7%, 25-30% and 47-56%.

### Known at a glance: the free line, with hysteresis

A word is free, known at a glance, when its lookup chance on the web's scale is under 8% (fwlib.FREE_P); each refresh
decides it once, in knowledge.free_flag, and knowledge.json carries it per word as "free". So that one lookup doesn't
flip it, it has hysteresis on the word's uncertainty: a free word leaves the free set only when the low end of its
chance, sigmoid(-(k + 1.28 sd)), passes 8%, and a word that isn't free joins only when the high end,
sigmoid(-(k - 1.28 sd)), falls below it, each against its flag at the last refresh. A word without a flag yet starts
on its prior's side: free when sigmoid(-m_w) is under 8%. Consumers use the flag where they have it: fwlib.is_free and
settled_words (the learning slots; a word the log has nothing on still takes its slot), knowledge.word_estimate (which
keeps the chance it reports to weave.py on the flag's side of 8%, so the chat brief agrees), and the extension through
the native host (levels.js costOf). Without a flag, they compare the chance with 8% as before.

On the 2026-10-20 log 354 of the 1,766 words are free, 253 of the 397 with evidence (231 by the old line): 29 joined,
among them する, 行く and ある (a form lookup no longer outweighs the rest), and 7 left (ただ, 写真, 古い, 基本, 心配,
気持ち, 覚える). tests/test_yardsticks.py checks that one lookup leaves a known word and a word
with a 21-day Anki interval free, that a lookup every third time takes them out, and that the same evidence keeps a
word out that was out.

### The price of a whole sentence

knowledge.json "sentencePrice" has, per surface, the chance a reader who reads a whole sentence in Japanese opens its
translation (knowledge.sentence_prices): the opens of sentence ends (an annotation knowledge.is_sentence takes) over
their read chances from time on screen, both weighted like evidence (30-day half-life), shrunk toward 0.08 by a beta
prior worth 25 sentences read. The read chance is time on screen alone, as calibrate weighs a sentence, so calibrate
agrees with the price on the days it was fitted on. On the 2026-10-20 log: chat 0.109 (25.9 opens over 232 sentences
read), the web 0.08 (no sentence ends logged before that day). The native host sends the web's to the extension.

## Choosing words (browser)

Selection happens when results are read from the cache, not when the model is called: the cache keeps every validated
candidate, so changing estimates, the budget or the level never pays for new model calls.

A level is a lookup budget (scripts/intensity.py BUDGETS, mirrored in levels.js): the lookups per 100 words the reader
puts up with, spent only on words not known at a glance and on whole sentences in Japanese. Planning assumes the
passage is read: B and every cost below are lookups if it is read. Per passage, with B = the level's budget per 100
words * passage words / 100 (the random thinning, below, leaves it alone), and one use per Japanese word:

1. Only the candidates the passage can place count, found as the page will find them (text.js locate), and of two
   that overlap only the first one kept. Each candidate's listed words get their estimate from the native host (k, sd
   and the free flag from knowledge.json, which has every word: a word the log has nothing on has its fitted prior,
   and one knowledge.json hasn't seen yet the fitted prior for its status; without a host, k = 2.5, fwlib
   KNOWN_PRIOR_K, for a known word, else the neutral prior) and a mean lookup chance p. A word is known at a glance
   when its free flag says so (with hysteresis, above), else when p < 0.08 (fwlib FREE_P). A word or phrase whose
   words are all known at a glance is free: shown wherever the model offered it, bigger swaps first, at every level
   but off, except that the random thinning keeps each with probability jitter.
2. The others cost the sum of p over their words not known at a glance; a whole sentence also costs its price, the
   chance its translation is opened if it is read (below). They are ordered by learning value: closeness of the
   least-known word's predicted lookup chance to the 25% target on the log-odds scale, with Thompson sampling (k drawn
   from N(k_w, var_w)) so uncertain words still get shown; for a sentence of free words, closeness of its price.
3. One with an active word not yet shown today is admitted first, outside the budget (at most one per passage), so new
   words always gather evidence.
4. Each next one is admitted while its cost fits the remaining budget, at most PER_SENTENCE in one sentence (one up to
   normal, two when heavier); a whole sentence only in a sentence with nothing budgeted yet, and then nothing more
   there (a full sentence passes to the next candidate). The first that doesn't fit is admitted with probability
   remaining / cost (so short passages still get learning words in expectation), then it stops. A sentence left
   English keeps the free words the model offered inside it.
5. The popup's density, when set, caps the total, budgeted candidates first; without it there is no other ceiling.

The price of a whole sentence comes from the native host (`sentencePrice`, the web's from knowledge.json: The price
of a whole sentence, above); without a host it is levels.js SENTENCE_PRICE, 0.08, which is also the fitted price's
prior. That number is from chat, because the web did not log a sentence's translation being opened until 2026-10-20:
of 439 sentence ends in the 123 T3 replies with a word count (2026-10-15 to 20), 24 were opened, 5.5% a sentence
shown, and 8.4% a sentence read when each is weighed by its read chance as the replay weighs words (90% interval 5.4%
to 11.4%, resampling replies). Before, how many sentences a passage got followed the stage (the prompt asked for one
in five from Stage 3, one in ten at Stage 2, none at Stage 1) and they were free when their words were.

The passage's plan records B, the lookups expected (the sum of p over every listed word shown, plus the price of each
sentence shown) and the sentence price. Randomness is seeded by the passage and the day, so a page repaints the same
way. Changing the challenge or the level repaints open pages; new estimates apply to passages as they are painted,
so words don't change under you while you read. Selection only chooses among the model's candidates, so a budget above
what they can supply saturates.

### The challenge: normal's budget

`challenge` is normal's lookup budget per 100 words read, default 0: normal's budget is then intensity.py's 0.5. It
lives in the word state's preferences, so the browser and chat share it; set it with `fw.py challenge X`,
`/japanese-weave:challenge X` or the popup. The native host sends normal's budget as `budget` (the challenge, else
0.5), and the other levels scale with it (intensity.budget). Until 2026-10-20 a daily controller steered it,
multiplying it by up to 4 to bring the web's peeks per 100 words shown toward the challenge; it is gone. It counted
free words the budget leaves out, couldn't tell a miscalibrated model from a thin pool of candidates, and the fitted
chat offset already keeps predicted lookups near those seen.

## Chat

A prompt can't enforce a per-reply budget exactly, so the brief (weave.py) asks for an amount the budget pays for and
the next turn says how the last reply did. Each word in use gets a chat lookup chance p: its estimate with the T3
offset (knowledge.word_estimate; knowledge.json has every word, with its fitted prior when the log has nothing on it),
kept on its free flag's side of 8%; without knowledge.json, from its status (known: k = 2.5, fwlib.KNOWN_PRIOR_K;
otherwise the neutral prior). Words with p under 8% (fwlib.FREE_P), so the free ones, are known at a glance, whatever
their status: they cost nothing and go in wherever the English has their meaning. The brief names each of them with its English, in the base list's order (connectors and the words
technical chat uses first), then the Anki words; numbers and katakana words are left out, as the brief asks for plain
English for numbers and no katakana loanwords. The first 12 that are connectors or adverbs (tier A in
data/lexicon.csv) are the default swaps, written in Japanese every time. No kana readings: in the T3 chats with
`<abbr>` glosses, Claude's own reading matched the list's in 400 of 401 glossed uses of listed kanji words, so a
reading per word (about 1,000 characters for 212 kanji words) would buy little. The rest are learning words, known
ones included: up to 24, by learning value (most useful first), grouped by p with rough odds (often, about 1 in 4;
sometimes; rarely). The brief asks for the budget over their mean p per 100 words (to the nearest half under 2; at
least 0.5, at most 5 per word allowed in a sentence and 8 in all), at most intensity.per_sentence in a sentence, where
the English around each makes its meaning clear. Fully Japanese sentences (Stage 3 on) may use only known words,
particles and です/ます, and no stage may use Japanese outside the lists. At full the known words are the same and every
other word in use is listed as learning.

The hook's text stays in the conversation, so a chat gets the whole brief once (weave.chat_turn, with each chat's
memory in sessions.json): the rules, the learning words, the default swaps and every known word. Later turns carry a
digest of the rules with this reply's amounts, the learning words, the default swaps, and only the known words that
changed since (newly known at a glance, or no longer). The rules come whole again when they change (to or from full,
another stage, other glosses). The whole brief comes back after a compaction, which Claude Code marks with a
`compact_boundary` system line in the transcript and Codex with a `compacted` line, and every 20 turns
(weave.FULL_EVERY) in case the context lost it without a mark. A brief stays under 9,500 characters, since Claude Code
files away hook text over 10,000; known words that don't fit are named on the next turn. On the 2026-10-20 state
(Stage 3, 293 words known at a glance, `<abbr>` glosses), the old brief was about 4,000 characters (about 1,070
tokens) on every turn; the whole brief is about 8,200 (2,360 tokens) and a later one 2,230 (590 tokens). A chat of 4
turns or more comes out ahead; over the 24 chats in the user's transcripts the briefs total 75% of the old ones.

Each brief is kept as a record of what its reply was offered: a `brief` op (hub.py), which changes nothing and goes to
the ops table where the state lives, through the outbox with a collector. Its fields follow a web passage's plan:
level, intensity, budget, the learning words asked for per 100 words (ask), the costly words offered with their p, the
delayed-start arms (empty until that experiment starts), the expected lookups (ask times their mean p), per 100 words
where the web's are per passage; and the session id, the words known at a glance, whether the brief was whole, its
length and, from Codex, the turn id. feedback.chat_briefs reads them back, and feedback.brief_for finds a reply's: the
last of its chat sent at or before it.

The line on the last reply counts its known, learning and off-list words and its expected lookups per 100 words: p
summed over the learning (and unlisted) words used, plus 0.27 (the neutral prior) per off-list word, over its words;
known words are left out, being free. Japanese inside code is left out too: fenced blocks of backticks or tildes (an
unclosed one runs to the end), code spans of any number of backticks, and links (fwlib.strip_code). Off-list Japanese
gets "no Japanese outside the lists"; else over 1.5 times the budget asks for fewer learning words, and under half of
it (or of what the most the brief asks for would spend) for more.

## Status from evidence

The day rules stay the main path. With a challenge set, evidence, when a word has some (an evidence weight n_w of 2
or more), adds three moves, using an 80% bound on the fit's k and sd:

- veto: an active word does not graduate while the lower bound of its p is above 0.3;
- shaky -> known: when the upper bound of p is below 0.15;
- known -> shaky: when the lower bound of p is above 0.35 and it was opened in the last 14 days.

Safety: at most 3 evidence moves per direction per day; none within 14 days of a `known` or `learn` command for that
word; nothing at all if the log shrank since the last refresh. Every move is recorded in knowledge.json.

Words not introduced yet are scored but never moved. Instead, whatever the challenge, one you opened in at least 2
messages or passages over the last 14 days (knowledge.json "recent") is introduced before the lexicon order when a
learning slot opens, most opened first (fwlib.refill; the 20 slots and 5 new words a day stay). Two encounters, not
two opens: opening the same word twice in one message is one look, while a chat that keeps using a word you don't
know is worth teaching it.

### Rule fixes that don't need the model

- A flip on a known word counts like a peek day (two such days within a week make it shaky), not instant shaky; a
  flip on a learning word restarts its graduation clock.
- Shaky words take learning slots: new words fill the learning set up to 20 active plus shaky words, instead of all
  new words stopping while 5 or more are shaky. A learning or shaky word known at a glance (free in knowledge.json,
  fwlib.settled_words) keeps its status but takes no slot; one the log has nothing on takes one, even when its prior
  says free.

## Where it runs

- `scripts/knowledge.py`: the model (one fit of the whole log; the online model it replaced, for comparison), the free
  line, the sentence price, selection value helpers, calibration, transitions.
- `scripts/evaluate.py` and `tools/sim`: the evaluation harness and the simulated reader (Checking the model), run
  by hand; nothing in the live path uses them.
- With a collector, the fit runs there, next to the log and the word state: the collector refreshes
  knowledge.json every 10 minutes, and every other machine gets it with its cached copy of the state (hub.py). The
  collector only answers requests addressed to its own host name, so a web page can't read it through DNS rebinding.
- Without one, `fw.py refresh` fits the whole log (evaluate.extract reads it as the harness does), applies
  transitions in a short state edit, and writes knowledge.json. It runs one at a time (a non-blocking lock). The hook
  starts it in the background when exposure is stale; the native host starts it in the background when knowledge.json
  is over 15 minutes old. Nothing waits on the network.
- What a refresh costs. Calibrate's records need the model as it stood at the start of each day, so each day is
  fitted on the days before it once, and its sums are kept in knowledge.json ("daily", with "dailyFree", the free
  flags at the start of the last day summed), reused while nothing they depend on changes: the model's version, the
  reading model, the challenge, and how much of the log, its interactions and the intensity changes came before the
  day's end. A refresh then fits twice (the whole log, and the log before today), and the first one of a day also
  chooses the reading model (about a dozen fits). On the 2026-10-20 log a refresh takes 0.26 to 0.30 s, the first of a
  day 0.9 s, and 1.4 s with nothing cached (version 1: 0.21 s). On a simulated log of 60 days (52,000 observations),
  1.5 s, the day's first about 7 s and 13.7 s with nothing cached (version 1: 1.25 s, and 5.9 s for the day's first,
  which tuned its reading model by 12 replays). Reading the log (evaluate.extract) takes about 1 s of that 1.5 s.
- The native host's state reply carries, per word, k, its uncertainty and whether it is free, and the challenge and
  normal's budget. They are not part of the word-state version, so the extension's cache isn't cleared when they
  change.

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

On the web, half the passages are also thinned at random by a share between 20% and 100% (levels.js jitterFor), which
keeps each free word with that chance; the budget and the words it pays for are the same in thinned and untouched
passages. Every passage's shown record carries its plan: the level, its factor, the share, the swaps kept, the
candidates offered, the budget and the lookups expected. Because the thinning is random, comparing thinned and untouched
passages measures what words known at a glance alone do to reading time and opens (PLAN.md row F). Until 2026-10-20 the
share ran from 50% to 100% and scaled the budget too: thinned passages came to 5.3% Japanese against 6.5%, too little
contrast to read, and what free words cost was mixed with what the budgeted ones do. The comparison counts only passages
planned since (their plan has a sentencePrice, from extension 0.9.0), and among them only those with candidates
(thinning can't change the others), including those thinned to no Japanese at all, which are logged like the rest; its
effect is per point of Japanese known at a glance. The passages from before are compared apart, on one line. Every press is a `set` op, so the ops log says how often and when the default felt wrong; one into off
counts as lighter and one into full as heavier, like any other step. Presses less than a minute apart count as one
decision, judged by where they end (trying the keys, or overshooting and stepping back, isn't evidence), and a level
that ran out counts as normal before the next press. `fw.py calibrate` reports both (knowledge.json "calibration"):
screen time per word and opens per 100 words by the share of Japanese in a passage, starting with passages that got
none, the thinned-versus-untouched effect (or how far the two groups' shares are from the 0.5 points it needs), the
model against what happened per surface and class, in lookups per 100 words read (Checking the model, below), and per
surface the presses and hours spent away from normal. Many lighter presses mean the default is too heavy, and the
slowdown curve says how much each extra point of Japanese costs.

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

- `fw.py knowledge`: normal's budget; the chat and form offsets and the reading model; recent evidence moves; the
  words with the most evidence, with their lookup chance and whether they are free.
- `fw.py why WORD`: its prior mean, k and sd, whether it is known at a glance, every observation with its weight, and
  moves. WORD may be a form of the word (なります).
- `fw.py status`: the looked-up words lined up to come in next; `fw.py calibrate`: the model per class against what
  happened, and the 30 words opened most in the last 30 days that the word list doesn't have, which is what chat
  replies slip in beyond it.
- `fw.py evaluate [--models today,fit,constant]`: models scored on the log day by day (Checking the model).

## Checking the model

PLAN.md section 10 says how model changes are judged; these are the tools.

### The one unit: lookups per 100 words read

Budgets and their checks share one unit (PLAN.md section 5). A planner assumes the passage will be read, so it
divides the lookup chances it spends by the passage's words (levels.js planSwaps, weave.py's feedback line). A check
weighs each shown word by its chance of having been read: knowledge.words_read gives a passage's words read, its words
times the mean read chance (read_chance) over 20 evenly spaced depths, and a T3 message's paragraph by paragraph from
its stays (stay_chance; where bands overlap, the paragraph's layout changed and it counts once), with the words of
paragraphs T3 doesn't watch (those without Japanese) at the mean read chance of the ones it does. knowledge.per_100
divides lookups by those words read.

`fw.py calibrate` prints, from a record of every passage and message as the model stood at the start of its day
(knowledge.fitted_records: the fit on the days before, the free flags then and the sentence prices then), per
surface: the passages or messages with Japanese (blank ones have nothing to look up and don't
count), words shown and read, lookups, and the budget in the same unit (a web passage's plan; for a chat message, the
chat level in effect when it was shown, with the challenge as normal's; full has none). Then per class, the
lookups expected (each word's best read chance times its P(peek)) and seen (a glance counts 0.6), per 100 words read,
and seen over expected with a 90% interval from resampling days (knowledge.day_interval, a seeded percentile
bootstrap over days, given from 5 days on). Calibrated means the interval lies inside 0.83-1.2. The classes:

- costly: listed words not free as the model stood (the free flag); free: the others;
- topical: chat words found by code (NEXT_STEPS step 14); none yet;
- whole sentences: sentence translations (a 。 annotation, or a whole sentence glossed as one), each at its read
  chance times the surface's sentence price then;
- off-list: words the lexicon doesn't have, at the fitted prior of a new word without an Anki card;
- all lookups.

On the 2026-10-20 log, seen over expected with its interval, before (version 1) and after:

| class | web, before | web, after | chat, before | chat, after |
|---|---|---|---|---|
| costly words | 0.36 (0.15-0.57) | 0.60 (0.22-1.25) | 0.91 (0.81-1.00) | 1.24 (1.13-1.35) |
| free words | 0.67 (0.32-1.15) | 0.58 (0.33-0.91) | 1.36 (1.00-1.78) | 1.00 (0.84-1.08) |
| whole sentences | none shown | none shown | not priced | 1.37 (1.13-1.47) |
| off-list words | none shown | none shown | 0.68 (0.54-0.77) | 1.11 (0.84-1.25) |
| all lookups | 0.61 (0.34-1.00) | 0.58 (0.36-0.96) | 1.07 (0.96-1.13) | 1.19 (1.01-1.26) |

Five days are too few to call either calibrated. Chat's whole sentences are under-predicted mostly on its first day,
which had only the 0.08 prior to go by (the fitted price is 0.109).

On the simulated reader, an interval from a model 20% off passes 10-13% of the time with 3 days and 3-6% with 5, and
a calibrated model's interval fits inside after about 14 days (`tools/sim/run.py calibration-days`).

### The evaluation harness

scripts/evaluate.py (`fw.py evaluate`) reads the log as replay does (knowledge.timeline and knowledge.observed: the
same showings and episodes, read chances, line rule, glances, T3 noise and sentence translations) and scores models
prequentially by day: each day, every model is fitted on the days before and predicts the day's observations. The
score is the log loss of P(open) = read * q, with read from time on screen alone (at least 0.05) at a pace and cap held
fixed for every model compared: the line rule depends on what was opened, so it weighs updates, never predictions.
Chat rows count as well as web rows; episodes are fitted on but not scored. Per class (web and chat; known, learning
and new by the word's status that day, from the word state's added and changed days; conjugated forms on their own;
free and costly by each model's q) it gives the mean loss and seen over expected opens with an interval from
resampling days; for each model against the first, the paired loss difference day by day with its interval and the
days it did better; and on a simulated log, the loss of the true chances, the floor no model can beat.

A model is any object with fit(log, until), which learns from the log's events before `until`, and predict(obs),
its P(lookup | read) for one observation; the module docstring lists what a log and an observation carry, including
each observation's time on screen, words and depth, so a model may fit its own pace. "fit" is knowledge.Fit, refitted
each day from the day before's (evaluate.CURRENT, the model knowledge.py runs); "today" is knowledge.Online, exactly
the old replay's updates; "constant" is one chance per surface. The harness holds the reading model fixed, so the fit
runs at the log's pace and cap there.

On the 2026-10-20 log (5 days scored), the fit against today's model, in log loss per observation with a 90% interval
from resampling days: all -0.0013 (-0.0035 to +0.0007), better on 4 of 5 days; web -0.0026 (-0.0064 to +0.0011);
chat +0.0006 (-0.0057 to +0.0081); known words -0.0017 (-0.0063 to +0.0030), better on 4 of 5; learning words
+0.0004 (-0.0052 to +0.0057), better on 1 of 5. Five days can't tell them apart. On the simulated reader the fit is
better on learning words by 0.0275 a day (sd 0.010), which every 5-day window would show; at the live log's
day-to-day spread too, so the live advantage on learning words is far smaller than the simulated one. At the live
log's overall difference (-0.0017 a day, sd 0.0043), a 90% interval needs about 18 days to exclude no difference.

### The simulated reader

tools/sim (`python3 tools/sim/run.py all`, about two minutes) is a reader whose memory, reading and lookup habits are
known: each word's recall decays on an FSRS-4.5 curve and grows more after longer gaps, and a lookup lowers it; a
word is read with the chance the replay's own formula gives (one depth per passage, so the line rule holds); a word
read and not recognised is looked up with chance 0.9, one recognised with chance 0.02 (0.1 for a conjugated form),
both scaled by a mood factor per day (and in chat by chat_factor, 1 unless an experiment sets it). At the live log's
volumes (web about 8,000 words and 5 lookups a day, chat about
12,000 words and 35 lookups), its days go through feedback.record into a state dir with the truth beside them
(truth.jsonl), so the real fit, calibrate and the harness read them unchanged. Its experiments: volumes,
convergence of one word, recovery per class at 1, 2 and 4 weeks (and the reading model the fit picks), the chat
offset against a true surface difference, the harness on a simulated log, the calibration window, the power of the
delayed-start experiment, and recurrence intake. `run.py log DIR` writes a simulated state dir to look at.

tests/test_yardsticks.py holds the check every model change must pass: a word starting at 27% whose true chance is
5%, shown 3 times a day, crosses 8% within 300 exposures. The fit passes it twice: as fitted (the prior learns from the
100 words of the check, all at 5%, and is under 8% at once) and with its prior held at 27%, as for one word among many
at 27% (11.8% after 10 exposures, 8.8% after 30, 7.4% after 100, 6.7% after 300). Today's model settles near 11%: the
pull toward the prior holds it there.

What the fit does on the simulated reader (3 seeds of 28 days; `run.py recovery`, `harness`, `offset`):

- Log loss, each day predicted from the days before (seed 1): fit 0.1093, today's 0.1286, a constant 0.1362, the true
  chances 0.0948. The fit is better than today's by 0.0193 per observation (0.0168 to 0.0216), on 27 of 28 days;
  on learning words by 0.0260 (27 of 28), on known words by 0.0127 (26 of 28). It closes 57% of the gap between
  today's model and the truth.
- Expected lookups over the truth's, days 8 to 28 (the last week): known words 0.97 (0.94), new words 0.89 (0.96),
  all 1.01 (1.04); today's 2.71, 0.52 and 1.51. Learning words are 2.11 (2.14), today's 3.57: the simulated reader
  learns them while they are shown (each reading strengthens memory), and the fit has one k per word. With a reader
  whose reading teaches nothing (rho = 0) learning words come to 1.05 over days 8 to 28. The recency step (NEXT_STEPS
  step 10) is what follows a word as it changes.
- Estimates against the true chance at 4 weeks, mean logit bias: known -0.10, learning +0.60, new -0.20 (today's
  +1.09, +1.39, -0.78); rank correlation with the truth for known words 0.58 (today's 0.36).
- The chat offset follows a true difference: with chat's chances doubled (about +0.69 on the log-odds scale) it moves
  from about -0.29 to +0.40, the whole difference; halved, it moves 0.46 (a factor can't move large chances the full
  0.69 down). Today's moves 0.32 and 0.18. With the surfaces alike it sits near -0.3: in a chat message the line rule
  takes everything above an open as read, while the simulated reader's reading goes by read chance, paragraph by
  paragraph.

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

Version 2 (NEXT_STEPS step 4, after the review of the plan):

- One fit of the whole log replaces the online updates and their pull toward the prior, which held a 5% word at 11%.
- Status returns to the prior, but as a fitted feature with a prior of its own, not a fixed starting value: the Anki
  interval alone mixed learning words and the queue words chat uses (about 5% and 60% a read in simulation). It counts
  some evidence twice (day rules use lookups too), and goes when status comes from the model (step 8).
- The reading model is chosen by the likelihood of the reading model itself (one depth a passage), on both surfaces,
  and kept at its defaults when the best pair is on the grid's edge.
- The free line has hysteresis on each word's uncertainty, decided once per refresh and sent to every consumer.
- The daily controller is gone: the challenge is normal's budget.
