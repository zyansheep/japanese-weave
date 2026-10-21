# Adaptive difficulty

Japanese Weave estimates, for every word, how likely you are to open its gloss, from the gloss interaction log.
It uses those estimates three ways: to decide which words appear on a page or in a reply, to spend each intensity
level's lookup budget (lookups per 100 words, on words not known at a glance), and to move words between learning
and known faster and more reliably than day counts alone. The plan behind it, the simplifications it rests on and
what comes next: [PLAN.md](../PLAN.md).

This is version 3: since NEXT_STEPS step 4, one penalized fit of the whole log on every refresh replaces the online
updates of version 1, and since step 10 the fit has a shared recency term, so a word has a long-term chance and a
current one. The first draft was reviewed adversarially (statistics and engineering), and the plan behind version 2
too; the decisions those reviews forced are listed at the end.

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

    P(lookup | read) = sigmoid(offset_s + form * f + context * s + R(k_w, u))

- k_w: long-term knowledge of w on a log-odds scale, its chance a week after it was last read. It is on the web's
  scale: the web's offset is 0, the reference.
- R(k_w, u): the recency term (below). Right after a read the odds fall to a floor shared by every word, and climb back
  to the word's long-term odds over a week; u is how far back it is. At u = 1 (a week or more, or never read in the
  log) R = -k_w, and the model is version 2's.
- context: an offset for a repeat in the same page or T3 window within the hour, s the chance the word was last read
  there.
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
its k (long-term), sd, prior mean (mu), free flag (below), evidence weight (n), with evidence its observations, and
read, when it was last read (Recency, below); "recency", the term's shared parameters; and "prior", the regression's
coefficients and widths by kind, which the native host uses for a word knowledge.json hasn't seen.

How it is solved: Newton's method with the expected information, which is positive definite (the observed one isn't
everywhere: log(1 - r q) is not concave in k when q is large), with step halving. The information has one row per word,
joined to the others only through the shared parameters, so the Schur complement on the shared parameters solves each
step in time linear in the words. A word's unopened observations with the same read weight (rounded to 0.02) and the
same clock (Recency, below) pool, so the work grows with the words more than with the log. The information is the
expected one through each chance's log odds (Gauss-Newton), the recency term bending its derivative in k between -1
and 0. A refresh starts from the last one's fit, but the day's first, which starts from nothing (from two floors, each
fitted twice: Recency). On the 2026-10-20 log (3,396 observations, 397 words, 3,222 pooled terms) a fit takes 0.5 s
from nothing and 0.05 s from the last one; on a simulated log of 60 days (52,000 observations, 1,244 words, 43,269
terms), 7 s and 0.9 to 1.2 s (version 2: 1,870 and 16,563 terms; 0.07 s and 0.01 s, 0.43 s and 0.05 s).

### Recency: the long-term chance and the current one

Version 2 had one k per word, so a word read three minutes ago and one unread for a week had the same chance; k was the
mean over the gaps a word happened to be shown at. On the simulated reader that over-predicted learning words 2.6 to
3.6 times when shown again within the hour, and under-predicted them after three days. Since NEXT_STEPS step 10 the fit
has a term shared by every word (knowledge.recency_log_odds):

    odds of a lookup = m + (o - m) u        o = e^(-k_w), the long-term odds; m = min(e^floor, o), smoothly

- u, how far the word is back toward its long-term chance: the time since it was last read over LONG_DAYS (7 days),
  at most 1, averaged over when it was last read (the clock, below). At u = 1, a week or more after the last read or
  never read in the log, the odds are o: k_w is the chance a week after a read, the long-term chance PLAN.md defines.
- floor: the log odds of a lookup right after a read, fitted, shared by every word. A word known better than the
  floor has no recency: m is a smooth minimum of the floor's odds c and o, (c^-8 + o^-8)^(-1/8) (SOFT_MIN), which is
  0.92 c at o = c and within 1% of the smaller of the two once one is 1.5 times the other.
- context, a log-odds offset times s, the chance the word was last read in the same context: the same page view, or the
  same T3 window within the hour (CONTEXT_MS). A repeat there is easier for the context, which would otherwise read as
  memory. T3 logs no thread, so its window within the hour stands in for one.
- The offsets for chat and forms multiply the whole curve's odds, the floor's too.

Why this form. On the simulated reader's FSRS memory, a word's lookup chance right after a read is the chance of
checking a word recognised (2%), and while the time since is short of the word's stability the odds above that grow in
proportion to it, so the odds rise linearly from the floor at the start to the long-term odds at a week. How far a word
falls right after a read then depends on how unknown it is, with no parameter per word: a known word at 3% has little to
lose, a learning word at 25% falls about 17 times in odds. Mixing over when a word was last read is all but exact
here, the odds being linear in u. While choosing the form (seed 1, with the floor's prior then at 2%), a term added on
the log-odds scale, slope times log((u + 0.05) / 1.05), which gives every word the same fall, fit worse (log loss
0.0992 against 0.0981) and under-predicted known words more (0.83 against 0.86); shapes concave in time
((Δ/7 days)^a with a = 0.3 to 0.7) did worse on learning words (1.74 to 1.90 times the truth's lookups against 1.60),
and so did a long-term chance defined 6 hours or a day after a read (1.90 to 2.27).

The clock (knowledge.last_reads, since_read, read_clocks). Each earlier showing of the word in the log is weighed by the
chance it was the last one the word was read in: its read chance times the chance none of the newer ones was read,
newest first, until that chance is under 1% or the showing is a week old. The read chance is the one given what
happened there: 1 when opened, and r (1 - q) / (1 - r q) when not, r its read weight (the likelihood's: the line rule
or time on screen) and q its P(lookup | read) at its own clock. So a word the reader doesn't know, shown and left
unopened, was most likely skipped and doesn't reset the clock: with r alone, the unopened showings of new words on the
simulated reader reset theirs, the context offset came out +0.40 on a reader with no context effect, and new words
fit worse (log loss 0.5305 against 0.5276). As q comes from the fit, a fit from nothing runs twice (CLOCK_PASSES),
the second with the clock the first's chances give (a third changes nothing); one from an earlier fit runs once, its
clock weighed by that fit's chances. A showing's time is its shown record's, else its first interaction's.

The floor's prior is centred above every word (log odds 1.0, sd 1.5): no recency until the reader's own log shows it.
With it centred at 2% (the simulated reader's), the 2026-10-20 log's second and third days were predicted worse than
by version 2, the term assuming a fall right after a read that the data don't show for new words in chat. Where the
floor is above every word only its prior bends the objective, and a fit can end in either of two minima, one with
recency and one without: a fit from nothing (start=None) starts from a floor of 2% and one above every word and keeps
the better, and the floor moves at most 0.5 a Newton step. The day's first refresh fits from nothing; the others start
from the last refresh's fit. The context offset's prior is 0 (sd 0.5).

What the term leaves out: learning. The simulated reader's memory grows with every read it recognises (rho), and one k
per word lags behind a word being learned. A shared rate of learning per read, per read weighed by how far the word
had faded, or per read weighed by the chance's excess over the floor, as FSRS's gains are, brought learning words from
1.60 to at best 1.43 times the truth's lookups on seed 1 at no better log loss, so none is in the model. That is the
memory strength per word (NEXT_STEPS step 16).

Long-term and current. Free and known are judged on the long-term chance (k, the free flag with its hysteresis, the
status moves). A lookup costs the current chance (knowledge.current_chance; levels.js currentChance): the web planner,
the chat brief's odds, calibrate's expected lookups and the harness's predictions all use it, in a new context (s = 0)
for a page or reply not yet shown. A costly word recency holds under 10% (SPACING_P), where its long-term chance
isn't, waits. knowledge.json "read" has, per word, [time shown, chance it was the last read] for its showings of the
last week (word_reads), and "recency" the floor, the context offset, LONG_DAYS, SOFT_MIN and SPACING_P; the native host
sends the extension each word's read and the floor.

How it does (`tools/sim/run.py recency`, 3 seeds of 28 days; `fw.py evaluate` on the log of 2026-10-15 to 20):

- The simulated reader. The floor comes out at 0.029, 0.019 and 0.014 in odds, the truth's 0.0204 (2% for a word
  recognised, as written) within a factor of 1.5. The decay, how much a word's chance rises in log odds from an hour
  after a read to a week after, fitted for each word with evidence against the truth's (FSRS at the word's stability at
  the end), weighted by its observations: known words +0.95 against +0.81 (1.18 times), learning +2.81 against +1.96
  (1.43), new +3.82 against +2.68 (1.43), all +1.22 against +0.97 (1.25). Each day predicted from the days before, days
  8 to 28: log loss 0.0998 against 0.1042 without the term (the truth's, 0.0933), better on 26 of 28 days in each seed;
  on learning words by 0.0109, 0.0123 and 0.0095 a day (better on 25, 23 and 21 of 28), on known words by 0.0000 to
  0.0006 (never worse). Expected lookups over the truth's: learning words 1.48 (2.11 without), known 0.99 (0.97), new
  0.84 (0.89), all 0.94 (1.01). Learning words stay over-predicted: their memory grows with every read (with a reader
  whose reading refreshes recall but never strengthens it, rho 0, they come to 0.80), and the floor and the context
  offset are biased by the line rule in chat (below). The context offset comes out +0.17 to +0.48 (sd 0.18) on a reader
  with no context effect: in a chat message the line rule takes every word above an open as read, where the simulated
  reader reads paragraph by paragraph, so a repeat in the same chat session is less recent than the clock says; with the
  clock ignoring the line rule in chat, the offset came to -0.02 and the floor to 0.0216 (seed 1, while choosing the
  form). The days needed: on learning words the daily difference has a mean of -0.0106 and an sd of 0.0162, so a 90%
  interval excludes no difference after about 6 days, 25 if the difference were half that.
- An inert reader, whose memory nothing changes: the floor goes above every word (3.9 to 4.7 in odds), the fitted
  decay is 0.00, and the fit predicts as without the term (log loss differences of at most 0.0005 a day). With rho 0 the
  floor is 0.016 to 0.020 and the decay 1.10 to 1.33 times the truth's.
- The live log of 2026-10-15 to 20 (5 days scored, 3,326 observations). The floor stays above every word (2.7 in
  odds): the term is off, and the fit predicts as without it, but for the context offset (-0.10): all +0.0000 a day
  (-0.0001 to +0.0001), web +0.0001, chat -0.0001, known -0.0000 (better on 3 of 5 days), new -0.0006, learning
  +0.0006 (+0.0002 to +0.0009, better on none of 5; with the context offset held at 0 the two are the same model).
  Five days can't show what the simulation needs about a week for, but they say more than that: the log's
  likelihood is 15.8 units worse with the floor at the simulated reader's 2% than with no recency, and about flat for
  floors from 0.2 up. What the term would take from it is new words in chat looked up again hours later about as
  often as the first time (12 opens in 76 showings 6 to 24 hours after the last, against 20 in 137 first showings),
  where the simulated reader, once it looks a word up, knows it for hours. Known words on the web point the other way
  (1 open in 548 repeats within 15 minutes, 11 in 189 first showings), mostly repeats on the same page. With the
  floor's prior at 2% the term cost 0.0033 a day on learning words and 0.019 on new words, mostly on the second and
  third days, before the data had moved the floor off its prior; hence the prior above every word.

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

A word is free, known at a glance, when its long-term lookup chance on the web's scale (k's, a week after a read) is
under 8% (fwlib.FREE_P), never its current one: right after a read every word's chance is near the floor, and that must
not make a learning word free (PLAN.md section 5, rule 1). Each refresh decides it once, in knowledge.free_flag, and
knowledge.json carries it per word as "free". So that one lookup doesn't flip it, it has hysteresis on the word's
uncertainty: a free word leaves the free set only when the low end of its chance, sigmoid(-(k + 1.28 sd)), passes 8%,
and a word that isn't free joins only when the high end, sigmoid(-(k - 1.28 sd)), falls below it, each against its flag
at the last refresh. A word without a flag yet starts on its prior's side: free when sigmoid(-m_w) is under 8%.
Consumers use the flag where they have it: fwlib.is_free and settled_words (the learning slots; a word the log has
nothing on still takes its slot), knowledge.word_estimate (which keeps the chance it reports to weave.py on the flag's
side of 8%, so the chat brief agrees), and the extension through the native host (levels.js costOf). Without a flag,
they compare the chance with 8% as before.

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
   KNOWN_PRIOR_K, for a known word, else the neutral prior), when it was last read (read, with the fit's floor:
   recency) and a mean lookup chance p: its long-term chance for a word known at a glance, else its current one at
   the time of planning (levels.js currentChance, as knowledge.current_chance). A word is known at a glance when its
   free flag says so (with hysteresis, above), else when its long-term chance is under 0.08 (fwlib FREE_P). A word or
   phrase whose
   words are all known at a glance is free: shown wherever the model offered it, bigger swaps first, at every level
   but off, except that the random thinning keeps each with probability jitter.
2. The others cost the sum of p over their words not known at a glance, at their current chances; a whole sentence
   also costs its price, the chance its translation is opened if it is read (below). One with a word recency holds
   under 10% (levels.js SPACING_P, knowledge.SPACING_P), where its long-term chance isn't, waits even when budget is
   left: a word read an hour ago is near the floor (PLAN.md section 5, rule 2). A word whose long-term chance is itself
   under 10% is cheap and never waits; else a word in the free line's hysteresis band, not free and never shown, would
   never gather the evidence to rejoin. The rest are ordered by learning value: closeness of the least-known word's
   current lookup chance to the 25% target on the log-odds scale, with Thompson sampling (its current k drawn from
   N(k, var_w)) so uncertain words still get shown; for a sentence of free words, closeness of its price. A word unread
   for a while has climbed back toward its long-term chance and comes first: this is the spacing.
3. One with an active word not yet shown today is admitted first, outside the budget (at most one per passage), so new
   words always gather evidence; a waiting one isn't.
4. B has two parts: whole sentences may spend up to SENTENCE_SHARE of it (18%), words and phrases the rest
   (levels.js, as intensity.py split says for chat). Going down the one ranking, each is admitted while its cost fits
   what is left of its part, at most PER_SENTENCE in one sentence (one up to normal, two when heavier); a whole
   sentence only in a sentence with nothing budgeted yet, and then nothing more there (a full sentence passes to the
   next candidate). A part stops at its first candidate that doesn't fit. A part whose candidates ran out first hands
   what it has left to the other, which goes on from where it stopped. Then each part's first candidate that didn't
   fit is admitted with probability remaining / cost (so short passages still get learning words in expectation). A
   sentence left English keeps the free words the model offered inside it.
5. The popup's density, when set, caps the total, budgeted candidates first; without it there is no other ceiling.

The price of a whole sentence comes from the native host (`sentencePrice`, the web's from knowledge.json: The price
of a whole sentence, above); without a host it is levels.js SENTENCE_PRICE, 0.08, which is also the fitted price's
prior. That number is from chat, because the web did not log a sentence's translation being opened until 2026-10-20:
of 439 sentence ends in the 123 T3 replies with a word count (2026-10-15 to 20), 24 were opened, 5.5% a sentence
shown, and 8.4% a sentence read when each is weighed by its read chance as the replay weighs words (90% interval 5.4%
to 11.4%, resampling replies). Before, how many sentences a passage got followed the stage (the prompt asked for one
in five from Stage 3, one in ten at Stage 2, none at Stage 1) and they were free when their words were.

The share keeps whole sentences in the plan whatever the supply of learning words. Ranked like a word whose lookup
chance is their price, far from the 25% target, they would otherwise get only what learning words leave: all of the
budget on the web today, where learning words are scarce, but nothing once code finds enough candidates. SENTENCE_SHARE
is 0.18 so that normal asks chat for as many sentences as its brief did at Stage 3 before they were priced: one in
five, about 1.1 per 100 words (about 5.5 prose sentences in 100 words), at 0.08 each is 0.088 of normal's 0.5, and
0.18 * 0.5 / 0.08 = 1.1. (The 123 counted T3 replies held 0.73 whole sentences per 100 words, counting every word of
the replies, code included.)

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
its long-term chance for a free word and its current one, at the time of the brief, for any other, kept on its free
flag's side of 8%; without knowledge.json, from its status (known: k = 2.5, fwlib.KNOWN_PRIOR_K;
otherwise the neutral prior). Words with p under 8% (fwlib.FREE_P), so the free ones, are known at a glance, whatever
their status: they cost nothing and go in wherever the English has their meaning. Numbers and katakana words are left
out of the brief, as it asks for plain English for numbers and no katakana loanwords. The first 12 of the rest that are
connectors or adverbs (tier A in data/lexicon.csv, in its order) are the default swaps, written in Japanese every time.
The others are named with their English, the senses joined by a slash ("中 inside/during"), the most useful in chat first
(weave.priority): those that came up in the user's chat replies on 2 days or more (exposure.json), then the base list's
others in its order (frequency, with connectors and the words technical chat uses early), then the Anki words, whose
meanings are their cards' and often odd. They take at most 4,400 characters (weave.KNOWN_ROOM, 250 to 290 words; on the
2026-10-20 state all 255 fit). The ones left out are still known at a glance: the brief says how many there are and to
keep the English for any word not named, and a reply that uses one anyway is counted as using a known word, free, not as
Japanese off the lists. No kana readings: in the T3 chats with `<abbr>` glosses, Claude's own reading matched the list's
in 400 of 401 glossed uses of listed kanji words, so a reading per word (about 1,000 characters for 212 kanji words)
would buy little.

The rest of the words in use are learning words, known ones included, but for those recency holds under 10% where
their long-term chance isn't (weave.lists; as on the web): read too recently, they wait, in neither list, so the reply
may not use them. The line on the last reply, the next turn, counts them as learning words still, since the brief
offered them before the reply read them. The brief lists up to 24, by learning value (most useful first),
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

- `scripts/knowledge.py`: the model (one fit of the whole log with its recency term and clock; the online model it
  replaced, for comparison), a word's current chance, the free line, the sentence price, selection value helpers,
  calibration, transitions.
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
  chooses the reading model (about a dozen fits, without recency) and fits the whole log from nothing, from two floors.
  On the 2026-10-20 log (measured on a copy of it, 2026-10-21) a refresh takes 0.27 to 0.46 s, the day's first 1.65 s
  and 1.84 s with nothing cached (version 2: 0.22 to 0.30 s, 0.87 s and 1.07 s). On a simulated log of 60 days (52,000
  observations), 2.6 to 3.1 s, the day's first 15.8 s and 105 s with nothing cached, as after a change of the model's
  version (version 2: 1.6 s, 7.4 s and 12.7 s): the fit itself takes 0.9 to 1.2 s from the last one and 7 s from
  nothing (version 2: 0.05 s and 0.38 s), each of the 30 days summed one fit. Reading the log (evaluate.extract) takes
  about 1 s, writing knowledge.json 0.3 s.
- The native host's state reply carries, per word, k, its uncertainty, whether it is free and when it was last read
  (read), and the recency term's floor, the challenge and normal's budget. They are not part of the word-state version,
  so the extension's cache isn't cleared when they change.

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
- `fw.py why WORD`: its prior mean, k and sd, its lookup chance long-term and now, whether it is known at a glance,
  every observation with its weight, and moves. WORD may be a form of the word (なります).
- `fw.py status`: the looked-up words lined up to come in next; `fw.py calibrate`: the model per class against what
  happened, and the 30 words opened most in the last 30 days that the word list doesn't have, which is what chat
  replies slip in beyond it.
- `fw.py evaluate [--models step4,fit,today,constant]`: models scored on the log day by day (Checking the model).

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
each observation's time on screen, words and depth, so a model may fit its own pace. "fit" is knowledge.Fit with its
recency term, refitted from nothing each day, as the day's first refresh fits (evaluate.CURRENT, the model
knowledge.py runs), each observation at its clock (the log up to its showing); "step4" is the same fit without recency
(version 2), the first model and the reference by default; "today" is knowledge.Online, exactly the old replay's
updates; "constant" is one chance per surface. The harness holds the reading model fixed, so the fit runs at the log's
pace and cap there.

On the 2026-10-20 log (5 days scored) the fit with recency predicts as version 2 does (Recency, above). Version 2
against today's model, in log loss per observation with a 90% interval from resampling days: all -0.0013 (-0.0035 to
+0.0007), better on 4 of 5 days; web -0.0026 (-0.0064 to +0.0011); chat +0.0006 (-0.0057 to +0.0081); known words
-0.0017 (-0.0063 to +0.0030), better on 4 of 5; learning words +0.0004 (-0.0052 to +0.0057), better on 1 of 5. Five days
can't tell them apart. On the simulated reader the fit is better on learning words by 0.0275 a day (sd 0.010), which
every 5-day window would show; at the live log's day-to-day spread too, so the live advantage on learning words is far
smaller than the simulated one. At the live log's overall difference (-0.0017 a day, sd 0.0043), a 90% interval needs
about 18 days to exclude no difference.

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
offset against a true surface difference, the harness on a simulated log, the recency term against the truth (its
floor, context and decay, on the reader, one whose reading refreshes recall but never strengthens it, and an inert
one, `inert=True`, whose memory nothing changes), the calibration window, the power of the delayed-start experiment,
and recurrence intake. `run.py log DIR` writes a simulated state dir to look at.

tests/test_yardsticks.py holds the check every model change must pass: a word starting at 27% whose true chance is
5%, shown 3 times a day, crosses 8% within 300 exposures. The fit passes it twice: as fitted (the prior learns from the
100 words of the check, all at 5%, and is under 8% at once; the check's words are at 5% at every gap, and the fit
finds that, its floor above them) and with its prior held at 27%, as for one word among many at 27% (11.8% after 10
exposures, 8.8% after 30, 7.4% after 100, 6.7% after 300). The second is without recency: held at its prior too, the
floor would say these words, at 5% five hours after the last read, are at about 25% a week after, which is right in a
world with that floor and isn't this one. Today's model settles near 11%: the pull toward the prior holds it there.

What the fit does on the simulated reader (3 seeds of 28 days; `run.py recovery`, `harness`, `recency`, `offset`):

- Log loss, each day predicted from the days before (seed 1): fit 0.1050, without recency (version 2) 0.1093, today's
  0.1286, a constant 0.1362, the true chances 0.0948. With recency it closes 30% of the gap between version 2 and the
  truth: better by 0.0044 per observation (0.0032 to 0.0056), on 26 of 28 days; on learning words by 0.0109 (25 of
  28), on new words by 0.049, on known words by 0.0000 (-0.0002 to +0.0003). Version 2 was better than today's by
  0.0193 (27 of 28 days).
- Expected lookups over the truth's, days 8 to 28 (the last week): known words 0.99 (0.98), new words 0.84 (0.90),
  all 0.94 (0.97); version 2's 0.97, 0.89 and 1.01; today's 2.71, 0.52 and 1.51. Learning words are 1.48 (1.49),
  version 2's 2.11, today's 3.57: the simulated reader learns them while they are shown (each reading it recognises
  strengthens memory), and the fit has one k per word; recency took the error by gap away (Recency, above), not the
  lag. With a reader whose reading refreshes recall but never strengthens it (rho = 0), learning words come to 0.80
  (version 2: 1.19 on these seeds).
- Estimates at 08:59 after each week against the true chance then (the fit's current chance, as the planner would
  price it), mean logit bias at 4 weeks: known -0.07, learning +0.34, new -0.05 (version 2's -0.10, +0.60, -0.20;
  today's +1.09, +1.39, -0.78); rank correlation with the truth for known words 0.68 (version 2's 0.58), for new
  words 0.39 (0.06).
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

Version 3 (NEXT_STEPS step 10):

- A shared recency term, two fitted parameters (the floor and the context offset): the odds fall to the floor right
  after a read and climb back linearly to the long-term odds over a week, so how far a word falls depends on how
  unknown it is. A slope on the log-odds scale, the same for every word, fit the simulated reader worse.
- The clock weighs each earlier showing by the chance it was read given what happened there (1 if opened, else
  r (1 - q) / (1 - r q)), not by its read chance alone: an unopened showing of a word the reader doesn't know was
  most likely skipped.
- The floor's prior is "no recency" (above every word), so the term enters the decisions only as the reader's own log
  shows it. A fit from nothing tries a low floor and a high one, since the objective has a minimum near each.
- Free and known are judged on the long-term chance, cost on the current one, and a costly word recency holds under
  10% waits, unless its long-term chance is under 10% too (else words in the free line's hysteresis band would never be
  shown again).
- Not in it: learning from reading. A shared learning rate didn't earn its place on the simulated reader; the memory
  strength per word (NEXT_STEPS step 16) is where that goes.
