# Adaptive difficulty

Japanese Weave estimates, for every word, how likely you are to open its gloss, from the gloss interaction log.
It uses those estimates three ways: to decide which words appear on a page or in a reply, to spend each intensity
level's lookup budget (lookups per 100 words, on words not known at a glance), and to label every word: known when
it is known at a glance, learning while it is in use and not, new before that (Labels from the model, below). The plan behind it, the simplifications it rests on and
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

- with an Anki card: a_origin + b ln(1 + interval in days). The old prior, min(3, 0.5 + 0.8 ln(1 + interval)), stopped
  rising at a 21-day interval, but live lookups per read keep falling past it: about 7.6% for intervals of 20 to 57
  days, 5.0% for 58 to 89, 2.2% beyond. Here the interval is uncapped and the slope fitted;
- without a card: c_origin;
- origin is how the word had come into use by the fit's day (knowledge.origin_at, the word's origin field): learning
  (taken in to be taught), known (marked known by setup's top words or an Anki sync, or known or shaky before labels
  came from the model) or new (neither, or not yet). The interval alone mixes words that read differently: in the
  simulated reader, young Anki cards in the learning set and the queue words chat uses topically share intervals, but
  are looked up about 5% and 60% of the time when read. Until NEXT_STEPS step 8 the intercepts went by status; now
  that the model sets the status (Labels from the model, below), status in the prior would feed the fit its own output
  (a word labeled known gets the known intercept, which keeps it known), so the prior takes the origin, which only
  intake, setup, Anki syncs and the switch itself set, never a label. Its parameters keep the names by status ("card
  new", "learning", ...), and knowledge.json "prior" keeps its shape;
- each coefficient has a prior: the old Anki formula (a = 0.5, sd 0.5; b = 0.8, sd 0.25) for card words, the old
  neutral prior (1.0, sd 1) for others, and fwlib.KNOWN_PRIOR_K (2.5, sd 1) for known words without a card. A kind with
  no data keeps what the planner assumed, and the planner now takes every word's estimate from the fit, so the two no
  longer disagree about known words without history (the old replay started them at 27%, the planner at 7.6%); and a
  few observations can't swing a kind (with weak priors, one two-word episode moved the card regression by more than 1
  on the log-odds scale);
- for a word not marked known, plus its dictionary entry's word features: frequency rank, kanji known at a glance,
  script, part of speech, length, rarity and source language (Starting values from the word itself, below).

The prior's width tau is fitted per kind, Anki card or none, by EM: each word's squared distance from its prior mean
plus its variance, averaged, held toward 1.0 (card) and 1.5 (none) by 5 words' worth of weight. On the 2026-10-20 log
the widths came out 0.75 and 1.02.

Identification: the web's offset is fixed at 0, so the level of k is set by the prior regression; chat's offset is
identified by the words seen on both surfaces and through the shared prior means, and held toward 0 (sd 0.5).

Fitted on the 2026-10-20 log, by status (sd in brackets): chat's offset +0.20 (0.22) and the form offset -0.04 (0.56);
with an Anki card, a = 0.85 for new words (0.31), 0.41 for learning (0.33) and 0.60 for known (0.34), and b = 0.63
(0.08), so a known word's prior chance is 35% at no interval, 7.3% at 21 days, 4.0% at 60 and 2.3% at 150; without a
card, 1.83 for new words (0.33; 14%) and 0.46 for learning (0.60); known words without a card keep 2.5, as there are
none.

Status against origin in the prior, scored by the evaluation harness (the fit refitted each day from the days before,
log loss per observation, the difference day by day with a 90% interval from resampling days):

- On that log, as of 2026-10-21 (5 days scored), the only words whose origin differs from their status are the 21 shaky
  ones (origin known, status learning): origin minus status, all -0.0003 (-0.0006 to +0.0002), better on 4 of 5 days;
  chat -0.0010 (-0.0017 to -0.0005); learning words -0.0013 (-0.0035 to +0.0011); known -0.0001 (-0.0008 to +0.0005).
- On the simulated reader (3 seeds of 28 days), whose statuses come from its own day rules, origin and status are the
  same and so is the fit. Made to look like the switch, with every word taken in keeping origin learning when it
  graduates, origin against the old status (which knew when each word graduated): all -0.0001, +0.0001 and +0.0000 (the
  intervals within 0.0002 of 0); with the model's own labels from the day before in the prior instead (the circular
  choice): +0.0001, +0.0003 and +0.0002, worse on all three seeds, and on known words by +0.0002 to +0.0003. Both
  differences are far below the fit's gain over the online model (0.0193).

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
its k, sd, prior mean (mu), free flag (below), evidence weight (n), the JMdict id of its dictionary entry and, with
evidence, its observations; and "prior", the regression's coefficients and widths by kind, which the native host uses
for a word knowledge.json hasn't seen, and from which any dictionary entry gets its starting value (below).

How it is solved: Newton's method with the expected information, which is positive definite (the observed one isn't
everywhere: log(1 - r q) is not concave in k when q is large), with step halving. The information has one row per word,
joined to the others only through the shared parameters, so the Schur complement on the shared parameters solves each
step in time linear in the words. A word's unopened observations with the same read weight (rounded to 0.02) pool, so
the work grows with the words more than with the log. A refresh starts from the last one's fit. On the 2026-10-20 log
(3,396 observations, 397 words, 1,870 pooled terms) a fit takes 0.12 s from scratch and 0.03 s from the last one; on a
simulated log of 60 days (52,000 observations, 1,244 words, 16,563 terms), 0.62 s and 0.16 s (with the word features
of step 13, below).

### Starting values from the word itself

NEXT_STEPS step 13, PLAN.md row H. Once the vocabulary is the dictionary, its 27,000 entries with no history would all
start from the intercept of a new word without an Anki card (14% on the 2026-10-20 log), so the planner could not tell
them apart. So the prior mean of a word the reader hasn't vouched for (origin new or learning, card or not) adds its
dictionary entry's word features (knowledge.word_features), each one more shared parameter of the fit:

| Feature | Value | Why |
|---|---|---|
| rank | log10(rank / 1,000): +1 for each tenfold rarer | the most frequent words are met most |
| kanji known | the share of its kanji found in words known at a glance | a compound of known kanji can be read |
| all kanji known | 1 when it has kanji and all are known | |
| kana, katakana, mixed | how it is written, against kanji alone (knowledge.script) | katakana gets its own baseline: an English loanword can be sounded out |
| verb, adjective, adverb, other | the first sense's first JMdict tag, against a noun (knowledge.pos_group) | |
| length | log2(characters / 2): +1 for each doubling | |
| rare | JMdict marks no spelling common (common 0) | |
| loan | JMdict names a source language other than English | |

The reference word, with every feature 0, is a common two-character kanji noun of rank 1,000 none of whose kanji are
known, so the origin intercepts keep their meaning. The loanword flag is the source language, not the loan field as
such: JMdict's field marks every katakana word (3,890 have only "kata", no source), so a plain flag would repeat the
katakana baseline; what it adds is a borrowing an English reader can't sound out (German アルバイト, Portuguese
タバコ). A state word's entry is found once (dictionary.find, by spelling, reading and gloss; knowledge.word_entries)
and kept by its JMdict id in knowledge.json, which later refreshes read back.

A word marked known (setup's top words, an Anki sync) keeps its intercept and Anki interval alone: its lookup chance
sits near its floor whatever the word is like (in the simulated reader, at the 2% checks of a recognised word), and as
the larger part of the words with evidence (242 of 397 on the 2026-10-20 log) it would only pull the coefficients
toward no effect.

Kanji known at a glance come from the free flags of the last refresh (knowledge.json; in the harness, the day before),
counting only free words with evidence of their own (an observation or a command), never the word itself. That closes
three loops: a word's flag can't raise its own prior; a word free by its prior alone can't make its kanji known, so no
chain of priors makes words free (日本 free, so 日曜日's prior rises until it is free, so 曜 is known, ...); and the fit
never reads its own output, as the flags are from before it. What is left is that two words with evidence and a kanji
in common raise each other's prior mean, which is what the feature is for, and each has evidence of its own to answer
it. With no flags (a first refresh), no kanji are known and the feature is 0 until the next.

Each coefficient is held toward 0, no effect, by a Gaussian prior with sd 0.5 (knowledge.WORD_SD). On these scales a
word has about four features of size about 1, so before any data the features move its prior mean with an sd of
about 1, the spread of words around their mean (the widths, 0.74 with a card and 1.0 without). Wider, a few words
with much evidence swing every word without any: in a test refresh with one learning word never opened and 19 always
opened, an sd of 1 moved the rank coefficient to +0.99 and labeled 79 unseen queue words known; 0.5 moved it to +0.29
and labeled none. On the simulated reader 0.5 predicted first exposures best of 0.25, 0.5, 1 and 2 (below).

knowledge.json "prior" carries what any entry's starting value needs: words (the coefficients), featured (the origins
that take them), centre (the reference word's rank and length) and kanji (the kanji known at a glance the fit used).
knowledge.prior_estimate(knowledge, origin, interval, entry) gives (k, sd) for any entry of data/dictionary.tsv (origin
new for a word with no history); extension/src/prior.js priorOf(table, entry, {origin, interval}) gives the same in
the extension, and tests/prior_cases.json holds cases both must agree on.

The fit has 13 more shared parameters, 22 in all (the Schur complement grows with their square, but per word only with
the features it has). On the 2026-10-20 log a fit takes 0.12 s from scratch (0.09 before) and 0.03 s from the last one
(0.013); finding the state's 1,766 words in the dictionary takes 0.15 s once, and later refreshes read their ids back.
A refresh that also chooses the day's reading model takes 1.9 s (1.4 before), a later one 0.3 s (as before). On the
simulated log of 60 days, 0.62 s and 0.16 s a fit (0.50 and 0.12), a first refresh of the day 18.3 s (14.6) and a
later one 1.9 s (1.8).

On the simulated reader (`tools/sim/run.py priors`): every word has its entry in data/dictionary.tsv, 2,000 more
entries spread over the dictionary's ranks join the queue (katakana among them), which chat's topical words follow in
rank order, and each word not known starts at k0 = 0 + sum beta_j x_j + 0.8 z (z standard normal; beta: rank -1.2,
kanji known +0.8, all kanji known +0.4, kana +0.3, katakana +0.8, verb -0.3, adverb -0.4, other +0.5, length -0.3, rare
-0.5, loan -0.5, mixed and adjective 0). Its chance when read, sigmoid(-k0), held between the 2% checks of a word
recognised and the 90% lookups of one not, sets its recall on the first day; a word at 90% was never met, any other
was last met 10 to 100 days before. Known words are as before. Over 3 seeds of 28 days:

- The word features against the flat start (the prior by origin and Anki interval alone), in log loss per observation
  on each word's first 3 exposures while new or learning (4,183 observations), each day predicted from the days
  before: -0.0141 (-0.0187 to -0.0100), better on 58 of 84 days: from 0.4858 to 0.4716, 14% of the way to the true
  chances' 0.3867. With a coefficient sd of 0.25, -0.0122; 1, -0.0132; 2, -0.0108. On all observations, -0.0012.
- The coefficients are recovered with the right sign where the data is rich and toward 0 where it is thin: rank -0.75,
  -0.92 and -1.07 (90% intervals about +-0.3) against -1.18, -1.11 and -1.06 from least squares of the true k0 on the
  features over the same words; kanji known +0.52, +0.22, +0.40 against +0.58 to +0.77. Most others' intervals hold the
  truth and 0 both. They come out smaller than the truth because the fit maximizes over every word's k and the
  coefficients together: with few observations per word its k stays near the prior mean, which then explains little
  (the bias of penalized quasi-likelihood). With a rank slope of -1.2 and nothing else, 1,000 words with 1 observation
  each give -0.80 to -0.86; with 10, -0.99 to -1.15; with 30, -1.11 to -1.22. The planner sees shrunken differences
  between words, never invented ones.
- How far the prior reaches: with the 2,000 extra entries never shown, so the coefficients come from the lexicon's
  words (most within the first 5,000 ranks), their starting chances against the true ones: ranks 3,000 to 9,999, flat
  26% against a true 69%, the features 48%; ranks 10,000 to 29,999, flat 26% against 76%, the features 60%. The
  features halve the error, but rare words still start too well known: the intercepts and coefficients are learned
  from words being shown, which reading and lookups teach, while the truth is the first day's.
- Days of log the replay needs: of 11 simulated logs, replays of their first 5 days show the gain (the 90% interval
  below 0) in 3, of 7 days in 7, of 21 days in 9. Over those 11 seeds of 21 days the gain is -0.0092 per observation
  (-0.0112 to -0.0072), and after the first day (when the two start alike) -0.0109 a day with an sd of 0.0206 between
  days: half of all logs would show it after about 10 days, 80% after about 22.

On the 2026-10-20 log (`fw.py evaluate --models flat,fit`, 5 days), the coefficients barely leave 0: rank -0.31 (sd
0.34 at the last fit), all others within 0.22 of 0; katakana, rare and loan have no featured word with evidence and
stay at 0. On the first 3 exposures of new or learning words (338 observations, 34 opens) the features against the
flat start are +0.0000 (-0.0031 to +0.0022), better on 2 of 5 days (the first day the two are the same by
construction); on all first exposures -0.0000 (-0.0014 to +0.0008). The log can't show it yet: in the simulated reader 5
days show it in 3 logs of 11, and the live gain is likely smaller than the simulated one while the coefficients sit
this close to 0. Expect 2 to 4 weeks of log, and a gain only once words unlike the lexicon's (katakana, rare ones) are
shown, since the coefficients for them have nothing to learn from yet.

Starting chances on the web from that refresh's prior, for dictionary words with no history (origin new, no card; the
flat start is 14% for every one):

| Word | Rank | Script | Kanji known | k | Starting chance |
|---|---|---|---|---|---|
| 今日 | 138 | kanji | 2/2 | 2.35 | 9% |
| 時間 | 117 | kanji | 2/2 | 2.37 | 9% |
| 学校 | 344 | kanji | 2/2 | 2.22 | 10% |
| 先生 | 360 | kanji | 2/2 | 2.22 | 10% |
| 電話 | 579 | kanji | 1/2 | 1.94 | 13% |
| 仕事 | 160 | kanji | 2/2 | 2.33 | 9% |
| 毎日 | 534 | kanji | 2/2 | 2.17 | 10% |
| 天気 | 3,101 | kanji | 1/2 | 1.72 | 15% |
| 日本語 | 19,561 | kanji | 3/3 | 1.81 | 14% |
| 大学生 | 4,962 | kanji | 3/3 | 1.99 | 12% |
| 食べる | 165 | mixed | 0/1 | 2.11 | 11% |
| 美しい | 628 | mixed | 0/1 | 2.36 | 9% |
| 懐かしい | 2,103 | mixed | 0/1 | 2.29 | 9% |
| ちょっと | 112 | kana | - | 2.35 | 9% |
| やっぱり | 429 | kana | - | 2.17 | 10% |
| いきなり | 1,822 | kana | - | 1.98 | 12% |
| テレビ | 1,033 | katakana | - | 1.91 | 13% |
| パソコン | 1,879 | katakana | - | 1.92 | 13% |
| コンピュータ | 5,754 | katakana | - | 1.90 | 13% |
| ストレス | 2,880 | katakana | - | 1.86 | 13% |
| アルバイト | 4,841 | katakana | - | 1.86 | 13% |
| インフレ | 13,866 | katakana | - | 1.65 | 16% |
| リスク | 2,721 | katakana | - | 1.78 | 14% |
| ワークショップ | 19,824 | katakana | - | 1.78 | 14% |
| 概念 | 2,739 | kanji | 0/2 | 1.65 | 16% |
| 措置 | 2,464 | kanji | 0/2 | 1.67 | 16% |
| 憂鬱 | 6,777 | kanji | 0/2 | 1.53 | 18% |
| 斡旋 | 9,629 | kanji | 0/2 | 1.48 | 18% |
| 追徴 | 25,002 | kanji | 0/2 | 1.36 | 20% |
| 範疇 | 9,790 | kanji | 0/2 | 1.48 | 19% |
| 曖昧 | 2,987 | kanji | 0/2 | 1.85 | 14% |
| 関数 | 10,796 | kanji | 0/2 | 1.47 | 19% |

The order is right (frequent words of known kanji lowest, rare words of unknown kanji highest; 日本語 ranks 19,561
because BCCWJ counts it as 日本 and 語), but the spread is far too narrow: 斡旋 or 追徴 would be looked up most times,
not one in five. Until dictionary words are shown, the coefficients only know the lexicon's range, and the data says
little even there; the simulated reader above shows the same, less so. The monthly probe (NEXT_STEPS step 7), known
or not for words sampled across ranks, would be the evidence to pin the rank slope down before dictionary words are
offered, once its marks feed the fit (they don't yet).

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
on its prior's side: free when sigmoid(-m_w) is under 8%. Consumers use the flag where they have it: the labels (a free
word is known, fwlib.label), fwlib.is_free, knowledge.word_estimate (which keeps the chance it reports to weave.py on
the flag's side of 8%, so the chat brief agrees), and the extension through the native host (levels.js costOf).
Without a flag, they compare the chance with 8% as before.

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
   and one knowledge.json hasn't seen yet the fitted prior for its origin; without a host, k = 2.5, fwlib
   KNOWN_PRIOR_K, for a known word, else the neutral prior) and a mean lookup chance p. A word is known at a glance
   when its free flag says so (with hysteresis, above), else when p < 0.08 (fwlib FREE_P). A word or phrase whose
   words are all known at a glance is free: shown wherever the model offered it, bigger swaps first, at every level
   but off, except that the random thinning keeps each with probability jitter.
2. The others cost the sum of p over their words not known at a glance; a whole sentence also costs its price, the
   chance its translation is opened if it is read (below). They are ordered by learning value: closeness of the
   least-known word's predicted lookup chance to the 25% target on the log-odds scale, with Thompson sampling (k drawn
   from N(k_w, var_w)) so uncertain words still get shown; for a sentence of free words, closeness of its price.
3. One with a word whose delayed-start probe is due (the host sends its arm as `held:probe` or `shown:probe`) and that
   wasn't shown today is admitted first, outside the budget, costly or free (at most one per passage): the probe's
   guaranteed showing (The delayed-start experiment, below). Until NEXT_STEPS step 8 any active word not yet shown
   that day went first like this; now a new word gathers evidence like any other learning word, by learning value.
   A candidate with a word the host didn't send is never offered: the host leaves out the words the experiment hides.
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

The passage's plan records B, what the swaps kept within it cost (spent, from extension 0.10), the lookups expected
(the sum of p over every listed word shown, plus the price of each sentence shown), the sentence price, and the
delayed-start arm of each word shown that has one (arms). Randomness is seeded by the passage and the day, so a page repaints the same
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
Japanese outside the lists (the topical words below are one of them). Full is the same brief with the budget lifted:
the same lists, every sentence the user
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

Each brief also lists up to 5 topical words (weave.topical_words, TOPICAL_MAX), after the learning words: dictionary
entries the code matcher finds in the chat's last three turns (weave.recent_turns: the prompt being sent, then the
user's prompts and Claude's main-thread replies back to the third prompt before it, at most 8,000 characters, the
latest kept; tool results and the hook's own text are no turns), counted as the reading counts are (reading.py, under
"Words from your reading" below: only places a sense check can trust). Only words worth offering: an entry that can
stand in for English (the dictionary's swap flag), not a number, counter or katakana word; with no spelling in use,
known or learning, and none hidden by the delayed-start experiment (fwlib.hidden); and not known at a glance (p under
8%: the next refresh labels it known). Each is the state's word when it has one (a word not introduced yet), else the
entry itself, and its p is its estimate in chat when knowledge.json has one, else the starting value of a word without
history (reading.starting_k: the fitted prior of a new word without an Anki card, until the prior by the word's own
features gives one per entry). The ones the user's reading repeats come first (on 3 days or more of the last 30: the
ones intake nominates), then common entries (JMdict's common flag), each by its uses in the turns, then by general
frequency. They are listed with their odds as the learning words are, and they are costly words too: the amount of
learning words is figured over both, and a reply may make at most half of the learning words it uses topical. At
normal the budget buys about 2 costly words per 100 words, 6 in a 300-word reply, so 5 to choose from, at most 3 used,
leaves the learning words, whose spacing is the plan's, most of the budget (PLAN.md row K). The line takes about 360
characters on every turn: on the 2026-10-21 state (Stage 3, 366 known, footer glosses or `<abbr>`, with the longest
feedback line) a whole brief went from 8,041 to 8,403 characters (8,585 to 8,948 with `<abbr>`) and a later one from
2,101 to 2,463; a whole brief stays under 9,500 since the known line takes what is left. The hook finds them in 15 to
41 ms on the user's chats (the reading index, below, read for the turns' words only); measured through the hook on a
synthetic 2 MB transcript, the median hook went from 148 to 174 ms. Without the index (not built yet) there are none.

Each brief is kept as a record of what its reply was offered: a `brief` op (hub.py), which changes nothing and goes to
the ops table where the state lives, through the outbox with a collector. Its fields follow a web passage's plan:
level, intensity, budget, the learning words and whole sentences asked for per 100 words (ask, sentences), the price
of a whole sentence (sentencePrice), the costly words offered with their p (offered: learning words; topical: the
topical words), the delayed-start arms of the words it
offers or asks for (arms: {word: 'shown', 'held:probe' or 'shown:probe'}), the expected lookups (ask times the mean p
of the learning and topical words, plus sentences times their price), per 100 words
where the web's are per passage; and the session id, the words known at a glance, whether the brief was whole, its
length and, from Codex, the turn id. Full has no budget, ask, sentences or expected. feedback.chat_briefs reads them
back, and feedback.brief_for finds a reply's: the last of its chat sent at or before it.

The line on the last reply counts its known, learning, topical and off-list words, its whole sentences in Japanese (a
run from a sentence break to 。, ？ or ！ with no Latin letters in it), and its expected lookups per 100 words: p summed
over the learning (and unlisted) words used, the p its brief offered each topical word at, plus 0.27 (the neutral
prior) per off-list word and the price per whole sentence, over its words. The topical words are the ones the reply's
brief offered (sessions.json keeps each chat's last list: weave.offered_topical); they are named as a class only when
there were some, and a reply that uses more of them than learning words is told to use no more topical words than
learning words. Known words are left out, being free, whether the brief named them or not. Japanese inside code is
left out too: fenced blocks of backticks or tildes (an unclosed one runs to the end), code spans of any number of
backticks, and links (fwlib.strip_code). The particles and です/ます of a whole sentence are never off the lists; other
hiragana is, below Stage 3. Off-list Japanese gets "no Japanese outside the lists"; else over 1.5 times the budget
asks for fewer learning words, whole sentences or both, whichever spent over 1.5 times its part, and under half of
what the brief asks for at most for more of whichever spent under half its part.

## Labels from the model

Since NEXT_STEPS step 8 a word's status is a label the fitted model sets on each refresh (knowledge.decide,
fwlib.label):

- known: free, known at a glance (the free flag, with its hysteresis);
- learning (status `active`): not free, and in use: it has an added day, from being taken in, a command, or having
  been free once;
- new (status `queue`): neither.

`shaky` is gone: a known word that leaves the free set is learning again. `known` and `learn` set the label at once
(fwlib.apply_event), and the fit takes them as one-sided pseudo-observations (above), so the next refresh keeps the
label unless the evidence outweighs the command. Seen, peek and flip events from the extension change nothing in the
word state now: the gloss log has what the model needs, and the ops log keeps the events. The day-count rules from
French Weave went with this: the 20 learning slots, graduation after 3 exposure days, shaky words after two peek
days, the evidence moves that ran with a challenge set, the rule that showed a new active word first outside the
budget, and the chat brief's recall cue.

The refresh's state edit is one `labels` op (hub.py), run where the state lives (the collector with one, else this
machine; a cache gets the result with its next pull), so the ops log keeps every label change, every word taken in
with its draw, and every probe's outcome. A label changes only while the word's status is still the one the refresh
saw, so a command since keeps its label; nothing changes if the log shrank since the last refresh. knowledge.json
"moves" keeps the last 200 label changes, and "spend" what set the intake.

### New words: intake from the budget

Up to 5 new words a day (fwlib.NEW_PER_DAY) come in while the lookup budget goes unspent (knowledge.spending, unspent,
intake_room). Unspent is read from the plans the planners logged over the last 48 hours: on the web, each passage's
budget against what its budgeted swaps cost (the plan's spent, from extension 0.10; older plans have only expected,
which counts every listed word shown and so overstates the spend); in chat, each brief's budget against its expected
lookups, what its ask spends. When a surface with at least 5 such plans planned to spend under 80% of its budget, words
come in: the planners have too few learning words to spend it on. A surface without plans says nothing, so a reader
away for a week takes no words in. On the 2026-10-20 log the web's 174 budgeted passages planned 7.7 lookups of the
19.9 they were allowed (39%, an upper bound), so words would come in at 5 a day: the web's pool of learning words is
its limit (NEXT_STEPS step 9). Chat almost never leaves its budget unspent while it has learning words, since the brief
asks for as many as spend it.

Words not introduced yet are scored like any other. One you opened in at least 2 messages or passages over the last 14
days comes in first, most opened first (fwlib.newcomers). Two encounters, not two opens: opening the same word twice
in one message is one look, while a chat that keeps using a word you don't know is worth teaching it. Then come the
words your reading nominates (below), and while your reading has been counted on fewer than 3 days, the lexicon order
instead. A word the model already says is known at a glance is labeled known instead of being taken in.

### Words from your reading

NEXT_STEPS step 13, the chat side (reading.py). Every main-thread chat reply (Claude Code and Codex transcripts, as for
chat exposure) is counted per dictionary entry (data/dictionary.tsv, by its JMdict id) and day, counts only:

- its English through the code matcher (matcher.py, docs/matcher.md) over every entry, each with its display gloss and
  its first sense's glosses, so the longest match wins against every word the dictionary has, verbs and set phrases
  included ("go back" is 戻る's, so its "back" is never 奥). Code, links, `<abbr>` tags with their glosses, footer
  gloss lines, markup and identifiers (snake_case, paths, dotted and camelCase names) are left out. A place counts for
  at most one entry, and only where a sense check can trust it (reading.Reader.trusted): never closed-class English
  (the matcher's function flag: "about", "one", "so"); with sense flags (data/senses.tsv through senses.py, NEXT_STEPS
  step 12), the most frequent option flagged safe; until then, the most frequent option when that English is its main
  meaning (the first of its display gloss) and no more frequent entry reaches the same English only by a rule's
  inflection ("used" is the past of 使う before it is 中古 "used"). The index is rebuilt when the flags file appears
  or changes, so the counts switch to the flags from then on.
- its Japanese, which Claude wrote and so is checked by a model: through its `<abbr>` tags when it has them
  (dictionary.find on the word, its reading and its meaning, so a conjugated verb counts for its entry), else the
  word state's words found by the script-aware matching.

On the user's chats of the 30 days to 2026-10-21 (Claude Code only; a median of about 10,000 words of replies on the
24 days with over 2,000), without sense flags: the English of 4,218 reply blocks was counted in 7 seconds; a median
of 440 entries came up a day (35 to 932); 2,686 entries came up at all, and 1,246 on 3 days or more, 725 of them
swappable. Requiring the main meaning halves what the most frequent option alone would count (a median of 1,061
entries on those 24 days against 542), mostly wrong senses of technical English ("script" as 本 "book / script",
"data" as 資料 "materials / data", "state" as 国); some remain, "back" as 奥 and "stays" (a term of this codebase) as
滞在 among the most frequent, and they are what the sense flags are for.

The counts go where the state lives as the exposure counts do: each reply in an `exposure` op carries {entries: {entry
id: times}}, and fwlib.fold keeps exposure.json's reading ({day: {entry id: times}}) for the last 30 days, each reply
once (read: its digest, apart from the words' recent, so a reply counted for words before reading was counted still
counts here once) and readingDays ({entry id: days it came up on}), which the caches get (GET /v1/state) for the
brief's topical words; the counts by day stay where intake runs. The first scan that counts entries (scan.json has no
"reading" yet) reads the last 30 days of transcripts again, also from before the state was created; on the user's
chats it took 16 seconds in the background, the index's first build included, and wrote 623 KB of exposure.json, 37 KB
of it readingDays. The index (reading-index.sqlite in the state dir, 17 MB) is the matcher's trie split by first word
into SQLite, each node keeping only the hits that can decide a span; a first word whose subtree passes 2 KB (the
forms of "be" head 60 KB each of phrases, "is wrong" and the like) is stored by its second words too, so a text reads
only the second words it has. It is built in the background in about 5 seconds (reading.ensure), once per dictionary,
English data and sense flags; the hook only reads it.

A word is eligible once it came up on 3 different days of the last 30 (fwlib.ELIGIBLE_DAYS, READING_DAYS), can stand
in for English (swap), isn't a number, has no spelling in use (known or learning, hidden ones included) and isn't known
at a glance by its starting value (reading.starting_k under 8%: labeled known, not taught). The eligible ones come in
best first (reading.nominations): by those days, most first, as in the review's simulation, where admitting words by
the days they came up kept them coming back (PLAN.md section 6); then by their starting chance, nearest 25% first in
steps of half a unit of log-odds, so only a real difference counts (with the prior by origin every new word ties);
then by general frequency. A dictionary word the state doesn't have joins it when it comes in: the labels op's intake
row carries its entry id ([ja, arm, draw, id]), and fwlib.take_in adds it after the list's words with its gloss,
romaji, reading and id. On a copy of the 2026-10-20 snapshot with the 30 days counted, the first refresh took in
情報, 答え, 奥, 滞在 (a dictionary word) and 確認 (named by the rules, outside the experiment), two of them wrong
senses until the flags land; after it 616 words were eligible and not in use (316 of the list's, 300 dictionary
words).

### The delayed-start experiment

Whether being shown a word teaches it (PLAN.md section 7, row E). Each word taken in is drawn into an arm
(fwlib.assign): held back, appearing nowhere for 21 days (fwlib.PROBE_DAY), or shown like any learning word for 14
(SHOW_DAYS) and then paused until day 21. The draw is uniform from sha256(seed:word), the seed drawn once when the
experiment starts (state.json "experiment"), and under one half is held. Each word's "arm" in the state has the arm,
the draw, the day and time it came in and the day its probe is due, and later its outcome or the day the probe was
given up. The 25 words the fixed texts name whatever the lists say (the chat brief's rules and example sentences, the
claude.ai and ChatGPT rules: でも, だから and the like, fwlib.IN_THE_RULES) come in outside it: held back, they would
still be named there.

One predicate, fwlib.hidden (held days, or the shown arm's paused days), decides what no surface may offer.
fwlib.introduced, which every surface's word list goes through, leaves those words out: the chat brief's lists and
its record, the words the native host sends the browser (so neither the web model's word list nor the planner's
candidates; levels.js planSwaps also drops any candidate with a word the host didn't send), the claude.ai and ChatGPT
blocks (fwlib.visible), and the chat's topical words (weave.topical_words drops any entry with a spelling of a word in
use or hidden, so a held word is never topical either). `fw.py
status` counts hidden words without naming them, and a reply that uses one anyway counts it off the lists.

From day 21 both arms are probed. On the web the host sends the word's arm as `held:probe` or `shown:probe`, and the
first passage that can show it shows it outside the budget, once a day until one is read; in chat the brief has a
line asking for each due word once, on top of the amounts (at most 3). The outcome is the word's first showing on or
after its probe day that was read: opened, or a read chance from time on screen of at least 0.1
(knowledge.probe_outcomes; episodes don't count, as they exist only because something was opened): looked up or not
(a glance counts as looked up), with that read chance. A probe with no showing read in 14 days is given up. Then the
word is an ordinary learning word. Every web plan and chat brief record has the arms of the words it offers.

`fw.py experiment` reads it back: the words per arm and where they stand, the showings a day shown words got in their
shown days and any showing of a hidden word (a leak) from the gloss log, the share looked up at the probe per arm
with a two-proportion z test, and from 20 probed words a side a logistic regression of looked up on the arm with the
read chance as a covariate; then the intake and when 80 words a side are due their probe; with `--power`, the
simulated reader's power (tools/sim experiments.delayed_start) at the showings a day measured.

A simulated check, end to end: the tools/sim reader for 7 weeks, shown only what the state offers (its visible
learning words in chat and on the web, topical words only from words not taken in, and one web showing of each due
probe a day), with the real refresh each morning taking 5 words a day in while the web's plans left 70% of their budget
unspent. 245 words came in (115 held, 130 shown); over 2,482 hidden word-days no hidden word was in a chat brief's
lists or record, the browser's words or the log. 135 probes were recorded, none before its probe day, a median of 0.43
days after it began (at most 1.4). Shown words got 2.0 showings a day (1.1 read) in their shown days. The analysis
read the outcome back: held words were looked up at 17 of 65 probes (26%), shown ones at 4 of 70 (6%): z -3.27, p
0.001, and the logistic regression gives the shown arm -1.74 on the log-odds scale (se 0.59, p 0.003). At 2.0 showings
a day the simulated power at 80 words a side is 33%, 68% and 91% as a reading strengthens memory by 0, 0.03 and 0.1
(rho), and 4% when exposure teaches nothing: lower than PLAN.md's 73% to 95%, which assumed 4.6 showings a day. At 5
new words a day the first 80 a side are due their probe about 8 weeks after the experiment starts.

### The switch, and undoing it

`fw.py relabel --dry-run` (scripts/relabel.py) computes every word's label both ways from a state dir and its log
(`--home`), writing nothing: the old one is its status under the day rules, the new one what the next refresh would
give it (the fit as refresh makes it, free flags carried from knowledge.json, the prior by origin). It lists the words
that differ by kind, each with its lookup chance and the ends of its uncertainty, which way the hysteresis held it,
and its evidence or that its prior alone decides. On a copy of the 2026-10-20 state and log, 73 of 1,766 words differ:
49 new words would be known, all Anki cards under review with intervals of 11 to 19 days, whose prior is under 8% (35
by their prior alone, 14 with a little evidence, such as 一番, opened 0 of 22 times shown); all 21 shaky words known
(lookup chances 2% to 13%: the ones over 8%, such as 男 at 10%, started free on their prior's side and the hysteresis
keeps them there); 2 known words learning (場所 and 家族, each opened once, 8% with a high end of 15 to 18%); and one
learning word known (休む, a 20-day Anki interval, no evidence).

The first refresh with the model's labels switches (fwlib.switch): each word's status, added and changed days and
day-count fields go under its "legacy" for 30 days (fwlib.LEGACY_DAYS), and it gets its origin. To undo the switch in
that time, `fw.py relabel --restore` (a `restore` op, where the state lives) puts them back, keeps a word taken in
since as a learning word, and marks the state ("labels": {"from": "rules"}) so refreshes stop labeling words and
taking new ones in; then check out the code from before the switch, which runs the day rules on the restored fields.

## Where it runs

- `scripts/knowledge.py`: the model (one fit of the whole log; the online model it replaced, for comparison), the free
  line, the sentence price, selection value helpers, calibration, and the refresh's state edit (labels, intake,
  probes).
- `scripts/evaluate.py` and `tools/sim`: the evaluation harness and the simulated reader (Checking the model), run
  by hand; nothing in the live path uses them.
- With a collector, the fit runs there, next to the log and the word state: the collector refreshes
  knowledge.json every 10 minutes, and every other machine gets it with its cached copy of the state (hub.py). The
  collector only answers requests addressed to its own host name, so a web page can't read it through DNS rebinding.
- Without one, `fw.py refresh` fits the whole log (evaluate.extract reads it as the harness does), makes its state
  edit (one labels op: the labels, the day's new words with their arms, the probes' outcomes), and writes
  knowledge.json. It runs one at a time (a non-blocking lock). The hook
  starts it in the background when exposure is stale; the native host starts it in the background when knowledge.json
  is over 15 minutes old. Nothing waits on the network.
- What a refresh costs. Calibrate's records need the model as it stood at the start of each day, so each day is
  fitted on the days before it once, and its sums are kept in knowledge.json ("daily", with "dailyFree", the free
  flags at the start of the last day summed), reused while nothing they depend on changes: the model's version, the
  reading model, the challenge, and how much of the log, its interactions and the intensity changes came before the
  day's end. A refresh then fits twice (the whole log, and the log before today), and the first one of a day also
  chooses the reading model (about a dozen fits). On the 2026-10-20 log a refresh takes 0.26 to 0.30 s, and 0.9 s with
  nothing cached and the reading model to choose (version 1: 0.21 s). On a simulated log of 60 days (52,000
  observations), 1.4 to 1.5 s, the day's first 6.7 s and 11 s with nothing cached (version 1: 1.25 s, and 5.9 s for the
  day's first, which tuned its reading model by 12 replays). Reading the log (evaluate.extract) takes about 1 s of
  that 1.5 s.
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
not known at a glance and on whole sentences in Japanese, which may take up to 18% (SENTENCE_SHARE): known words are
free everywhere but off. On web pages (see Choosing words) a lighter level picks from each passage's cached
candidates (no model call), a heavier one from the full answer (one answer and one cache for +1, +2 and full; asked for
more on their own, the heavier levels got little more than normal, see docs/swap-prompt.md).

In chat, off replaces the brief with a one-line note to reply in plain English, without the calibration line or the
export notice. Full is the normal brief with the budget lifted, said in one line: a sentence the reader could follow in plain Japanese (listed words plus common grammar, plain desu/masu) is written fully in
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

- `fw.py knowledge`: normal's budget; the chat and form offsets and the reading model; the latest label changes; the
  words with the most evidence, with their lookup chance and whether they are free (none the experiment hides).
- `fw.py why WORD`: its prior mean, k and sd, whether it is known at a glance, every observation with its weight, and
  its label changes. WORD may be a form of the word (なります).
- `fw.py status`: the labels' counts and where they come from, the learning words with their lookup chance and arm, how
  many words the experiment hides (never which), the day's new words and whether the budget went unspent, and the
  looked-up words lined up to come in next; `fw.py calibrate`: the model per class against what happened, and the 30
  words opened most in the last 30 days that the word list doesn't have, which is what chat replies slip in beyond it.
- `fw.py relabel --dry-run` and `fw.py experiment`: The switch, and the delayed-start experiment (above).
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
- topical: chat words found by code (weave.topical_words). Not sorted out yet: a topical word from the list is
  counted as costly and one from the dictionary as off-list, until calibrate reads the brief records' topical lists
  (NEXT_STEPS step 14's check needs it);
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
free and costly by each model's q; first, each word's first 3 exposures, where its starting value decides most) it
gives the mean loss and seen over expected opens with an interval from
resampling days; for each model against the first, the paired loss difference day by day with its interval and the
days it did better; and on a simulated log, the loss of the true chances, the floor no model can beat.

A model is any object with fit(log, until), which learns from the log's events before `until`, and predict(obs),
its P(lookup | read) for one observation; the module docstring lists what a log and an observation carry, including
each observation's time on screen, words and depth, so a model may fit its own pace. "fit" is knowledge.Fit, refitted
each day from the day before's, with the free flags carried from the day before as a refresh carries them
(evaluate.CURRENT, the model knowledge.py runs); "flat" is the same fit without the word features, the prior by origin
and Anki interval alone; "today" is knowledge.Online, exactly the old replay's updates; "constant" is one chance per
surface. The harness holds the reading model fixed, so the fit
runs at the log's pace and cap there.

On the 2026-10-20 log (5 days scored), the fit against today's model, in log loss per observation with a 90% interval
from resampling days: all -0.0013 (-0.0035 to +0.0007), better on 4 of 5 days; web -0.0026 (-0.0064 to +0.0011);
chat +0.0006 (-0.0057 to +0.0081); known words -0.0017 (-0.0063 to +0.0030), better on 4 of 5; learning words
+0.0004 (-0.0052 to +0.0057), better on 1 of 5. Five days can't tell them apart. On the simulated reader the fit is
better on learning words by 0.0275 a day (sd 0.010), which every 5-day window would show; at the live log's
day-to-day spread too, so the live advantage on learning words is far smaller than the simulated one. At the live
log's overall difference (-0.0017 a day, sd 0.0043), a 90% interval needs about 18 days to exclude no difference.

### The simulated reader

tools/sim (`python3 tools/sim/run.py all`, about five minutes) is a reader whose memory, reading and lookup habits are
known: each word's recall decays on an FSRS-4.5 curve and grows more after longer gaps, and a lookup lowers it; a
word is read with the chance the replay's own formula gives (one depth per passage, so the line rule holds); a word
read and not recognised is looked up with chance 0.9, one recognised with chance 0.02 (0.1 for a conjugated form),
both scaled by a mood factor per day (and in chat by chat_factor, 1 unless an experiment sets it). At the live log's
volumes (web about 8,000 words and 5 lookups a day, chat about
12,000 words and 35 lookups), its days go through feedback.record into a state dir with the truth beside them
(truth.jsonl), so the real fit, calibrate and the harness read them unchanged. Its experiments: volumes,
convergence of one word, recovery per class at 1, 2 and 4 weeks (and the reading model the fit picks), the chat
offset against a true surface difference, the harness on a simulated log, the calibration window, the power of the
delayed-start experiment, recurrence intake, and starting values from the word itself (with `prior`, every word has
its dictionary entry and words not known start from their features; Starting values from the word itself, above).
`run.py log DIR` writes a simulated state dir to look at.

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
- Selection ranks only learning words, explores by Thompson sampling, guarantees new words exposure (since step 8,
  only a due delayed-start probe), and uses a stochastic budget so short passages aren't starved; candidates are not
  overgenerated (that hurt Gemini's fit).
- Evidence moves use uncertainty bounds, are rate-limited, and sit on top of the day rules instead of racing them
  (until step 8, when labels came from the model and the moves and day rules went).
- Log integrity: client ids against resends, a 7-day time window, gap-split episodes, sentence translations.
- Deferred: retries for T3 (chat has no peek-rate dial yet), merging browser and T3 contexts. T3 shown records
  (word counts) came later, so chat messages without lookups count too.

Version 2 (NEXT_STEPS step 4, after the review of the plan):

- One fit of the whole log replaces the online updates and their pull toward the prior, which held a 5% word at 11%.
- Status returns to the prior, but as a fitted feature with a prior of its own, not a fixed starting value: the Anki
  interval alone mixed learning words and the queue words chat uses (about 5% and 60% a read in simulation). It counts
  some evidence twice (day rules use lookups too), and went when status came from the model (step 8): the prior takes
  each word's origin instead, which no label sets.

Step 8 (labels from the model) and step 6 (the delayed-start experiment):

- Labels come from the free flag, and the prior from the origin, so the fit never reads its own output.
- New words come in while the logged plans leave budget unspent, not into fixed slots.
- One predicate (fwlib.hidden) gates every surface, and the words the fixed texts name stay out of the experiment.
- The old fields stay 30 days under "legacy", and `fw.py relabel --restore` undoes the switch.
- The reading model is chosen by the likelihood of the reading model itself (one depth a passage), on both surfaces,
  and kept at its defaults when the best pair is on the grid's edge.
- The free line has hysteresis on each word's uncertainty, decided once per refresh and sent to every consumer.
- The daily controller is gone: the challenge is normal's budget.

Step 13, starting values from the word itself:

- The prior's word features apply to words not marked known, so known words, most of the evidence, don't flatten them.
- Kanji known at a glance come from the last refresh's free words with evidence, never the word itself, so neither a
  word's own flag nor a chain of priors raises a prior.
- The coefficients are held toward no effect by an sd of 0.5, so a few words with much evidence can't move every word
  without any.
- The prior of any dictionary entry is computed the same way in Python and JavaScript from knowledge.json, checked on
  shared cases.
