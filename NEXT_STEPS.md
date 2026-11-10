# Next steps

Status: 2026-10-21; the steps were written on 2026-10-20, after the adversarial review. In order, each with what "done"
means. Why each one matters is in [PLAN.md](PLAN.md); the letters in brackets are its simplifications (section 8).

The order follows one rule: build the yardsticks before changing what they measure. Every model change is checked on
a simulated reader with known truth, then on the replayed log, so the measuring tools come first.

## Where each step stands (2026-10-21)

Built on the integration branch `plan`, merged step by step after review and a green test run; not deployed yet (see
"After deployment" at the end). "Met" means the step's own "done when"; where it is only partly met, the reason is
given. The numbers come from the simulated reader (sim), the 5-day snapshot of the live log (snapshot), or the prompt
lab's judged passages (lab, fresh, fresh2).

| Step | State | Done when met? |
|---|---|---|
| 1 T3 word counts | Done: T3 vendored.9 is running; live stays carry paragraph word counts since 2026-10-21 | Met |
| 2 Known words named | Done: first turn names every known word (about 8,000 characters), later turns about 2,200 | Met |
| 3 Yardsticks | Done: tools/sim, `fw.py evaluate`, `calibrate` per class with intervals, a record per chat brief | Met |
| 4 One fitter | Done: a penalized fit of the whole log | Partly: the convergence check passes and known and new words are within 15% in the sim; learning words were 2.1 times over until step 10 (1.5 now); on the snapshot it ties the old model (5 days can't separate them) |
| 5 Fewer controls | Done: no daily controller; sentences take up to 18% of the budget at their price on both surfaces; thinning keeps 20% to 100% of free words only | Met |
| 6 Delayed-start experiment | Built; starts with the first refresh after deployment | Met in the sim (no hidden word shown in 2,482 hidden word-days; the planted effect read back at p = 0.001); power at 80 words a side is 33% to 91%, lower than planned |
| 7 Probe | Done: `fw.py probe`; since 2026-11 in the review stream (T3 Code's review cards, a rolling 30-day estimate in `fw.py status`), the command gone | Met: standard error 4 to 5 points at 60 words; interval coverage 89% to 94% in simulation. The review stream's: standard error about 8 points at 120 probe cards, coverage 17 of 20 simulated readers |
| 8 Status from the model | Built; the switch happens at the first refresh after deployment (`fw.py relabel --dry-run` lists the 73 words it changes; `--restore` undoes it for 30 days) | Met |
| 9 Live check | Waiting: needs 2 to 4 weeks of data after deployment | Not yet |
| 10 Shared recency | Done, self-disabling: it stays off until the reader's own log shows short-term forgetting | Partly: the sim recovers the decay within a factor of 2 and learning words improve from 2.1 to 1.5 times (not within 20%); on the snapshot the fitted floor sits above every word, so the term is off |
| 11 Dictionary and matcher | Done: 26,969 entries; the matcher covers 93% of the lab's judged places | Met: 99.8% of the state's words and 39 of 42 unlisted lookups resolve |
| 12 Sense flags and Haiku's new job | Done and wired into the extension: safe swaps show within a second, the check adds the rest; the extension's own code reproduces the lab on fresh2 (83% to 88% coverage, 0.5% to 0.6% wrong) | Met against a relative bar: on fresh2, 83% coverage and 0.6% wrong senses at full against today's Haiku at 73% and 3.5%; on fresh, 81% and 1.1% after the fixes. Every passage still needs a call (closed-class words are always doubtful); the safe swaps show first |
| 13 Starting values and words from reading | Done: chat and web reading counts reach intake | Partly: features beat a flat start on first exposures in the sim (better on 55 of 84 days); the snapshot can't show it in 5 days (about 2 to 4 weeks) |
| 14 Topical words | Done: code finds them in the last turns, at most 5, never in health, legal or money chats | Met in part: 19% of offers are the wrong sense (from 41%); calibrate has a topical class, linked by time |
| 15 Experiment readout | Waiting: about 8 weeks after deployment | Not yet |
| 16 Per-word memory strength | Waiting: needs 60 days of log and a positive step 15; its place in the code is marked | Not yet |
| 17 One planner, two executors | Deferred: a large refactor that changes exploration (draws per word per refresh instead of per passage); worth doing only after the live check shows the two planners disagree in a way that costs | Not started |
| 18 Prompt iteration | Web: done in the labs (three prompt versions, two fresh sets). Chat: a replay lab would spend the user's Claude limits; deferred | Partly |
| 19 Reading speed as evidence | Built, off (`knowledge.SPEED`): in the sim it recovers the effect and fits nothing on a null reader, but it doesn't improve predictions, since one paragraph's time says little about any one word; on the snapshot it is slightly worse. docs/adaptive-difficulty.md gives the rule for turning it on | Built; gate not passed |

## Now: fix what is broken, build the yardsticks, start the slow experiment (days to two weeks)

1. **Run the T3 build that sends word counts** [I]. Today no T3 stay carries a paragraph word count and no T3 message
   has a shown record, so new chat replies count only when something in them was opened. Done when a live T3
   `visible` stay has `words` in its detail and new T3 contexts have a word count.
2. **Name the known words in the chat brief.** The brief calls them "roughly the 319 most common Japanese words", but
   the Anki sync reordered the list: only 87 of those 319 are among the lexicon's 319 most frequent, and the default
   swaps come out as dog, Italy, Russia and the numbers one to four (the code means connectors and adverbs). List the
   known words by name, take the default swaps from connectors and adverbs, and send the list once per chat with only
   changes after, since the hook's context piles up every turn. Done when the brief names every known word and a
   chat's later turns carry a shorter brief than today's.
3. **The yardsticks.**
   - **A simulated reader** in `tools/sim/`: a reader whose memory, reading and lookup habits are known, producing
     logs at real volumes. Start from the review's scripts in `~/.local/share/japanese-weave-review/2026-10-20/`
     (reader, models, convergence, recency, power, calibration days, recurrence). Add the convergence check to the
     tests: a word starting at 27% with a true chance of 5%, seen 3 times a day, crosses 8% within 300 exposures.
     Mark it as an expected failure until step 4.
   - **The replay** scores chat rows as well as web rows. It reports per class (known, learning, new), compared with
     the current model day by day with a bootstrap interval. It holds reading pace fixed between compared models and
     scores form lookups as their own outcome.
   - **One unit,** lookups per 100 words read, in the web planner, the chat feedback line and `calibrate`.
   - **`calibrate`** prints, per class (costly, free, topical, whole sentences, off-list), seen against predicted with
     a 90% interval from resampling days, and the budget in the same unit. It stops counting Japanese inside code
     spans, since quoted words aren't woven.
   - **One record per chat reply,** with the same fields as a web plan: the budget, the words offered with their odds
     and arms, and the expected lookups.

   Done when `calibrate` prints today's model per class with intervals, and the simulated reader runs from one
   command.
4. **One fitter** [B, D, H, M, Q]. Replace the online updates and the pull toward the starting value with a penalized
   fit of the whole log on each refresh:
   - each word's k, held toward its prior;
   - an uncapped Anki-interval slope;
   - the surface offsets, reading pace and cap;
   - each word's uncertainty, from the fit's curvature.

   With it, change the free line: leave the free set only when the low end of a word's chance passes 8%, rejoin when
   the high end falls under it, and count a lookup of a conjugated form as a weaker label. Done when:
   - the convergence check passes;
   - on the simulated reader every class is within 20% of the truth;
   - one lookup doesn't take a known word out of the free set;
   - on the replay it beats today's model on learning words, by day, without losing on known words.
5. **Fewer amount controls** [F, P].
   - Delete the daily controller, so `challenge` is normal's budget.
   - Price whole sentences on both surfaces at the observed translation-lookup rate per sentence, and let stage stop
     gating sentences.
   - Make full in chat the normal brief with no budget.
   - Make thinning apply to free words only, over 0.2 to 1, and never scale the budget.

   Done when `calibrate` shows sentences as a class against the budget, and a test shows the budget is equal in
   thinned and untouched passages.
6. **Start the delayed-start experiment** [E]. Each new word is randomly held back for 3 weeks, or shown for 2 and
   paused for 1; both are probed at week 3 by a guaranteed showing. Held-back words appear nowhere, chat topical words
   included. Start now: it is the slowest step, and at 5 new words a day 80 words a half take about 5 weeks. Done when
   every new word's arm is in the state and in every plan and brief record, and a test shows held-back words are never
   offered.
7. **The baseline probe** [R]. `fw.py probe`: about 60 words sampled by frequency (by your reading's frequency once step
   13 counts it), each marked known or not, plus 6 made-up words. Run it now, before the big changes, then monthly.
   Done when it prints the goal estimate with its interval, and the share of made-up words claimed. Since 2026-11 the
   probe lives in the review stream instead (scripts/review.py, probe.rolling): no command, an estimate from the last
   30 days of review cards.
8. **Status from the model; intake from the budget.** Labels come from the fitted model: known means free. Up to 5 new
   words a day come in while the budget goes unspent, in lexicon order until step 13 ranks them by your reading. Delete
   the 20 slots, the day-count rules, the evidence moves, the rule that shows a new word first outside the budget, and
   the chat recall cue. Replay the log under both systems and list the words whose labels differ. Keep the old fields
   for a month. Done when the differences look right to you and `fw.py status` reads its labels from the model.
9. **Check the live data**, running alongside the next steps once 1 to 5 are deployed. Done when:
   - per class, the 90% interval of seen against predicted lies within 0.83 to 1.2;
   - Japanese outside the lists causes under 20% of chat lookups (was 72%);
   - chat lands within a factor of 2 of its budget, in the one unit.

   Expect 2 to 4 weeks for the intervals; the off-list share can be read in days. The replay recomputes the intervals
   for any model version, so a model change doesn't restart the clock. The web will stay under budget until step 13:
   its pool of learning words is the limit.

## Next: recency, then the dictionary and code that finds the candidates (weeks)

10. **A shared recency term** [D]. Two parameters for time since the word was last read, with each showing weighted by
    its read chance, plus an indicator for repeats in the same page or thread. With it:
    - free is judged on the long-term chance (a week without the word), cost on the current chance;
    - a costly word under 10% now waits.

    Done when:
    - on the simulated reader it recovers the true decay within a factor of 2;
    - on the replay it beats step 4's model on learning words by day (the simulation expects 1 to 2 weeks of held-out
      days) without losing on known words;
    - a test shows a learning word read an hour ago is neither free nor picked.
11. **The dictionary, and code that finds the candidates** [N].
    - All common JMdict entries with frequency ranks, as a static file built by the same pipeline as
      `data/lexicon.csv`, without the word-by-word review. Anki words map onto entries, and chat exposure matching
      uses the `<abbr>` tags where a reply has them.
    - Then the matcher: an index from English (lemmas, irregular forms, multi-word phrases such as "there is" and
      "look at") to entries. Every span gets its options, and the longest match wins.

    Done when every word in the state and every unlisted lookup in the log resolves to an entry, and the matcher
    covers at least 80% of the judged places in `tools/prompt-lab/oracle.json` (naive matching already covers 83%).
12. **Sense flags, and Haiku's new job** [N].
    - Flag each entry safe or doubtful once, offline: a model pass over the entries, spot-checked against the lab.
    - One Haiku call per batch of passages that have doubtful spans or dense sentences. For each doubtful span it
      picks the right option or none, and it writes the dense sentences and phrases in Japanese.
    - One candidate set serves every level. Delete the normal prompt's target, the second cache, `answerLevel`,
      `swapLimit`, `capSwaps` and the density cap.

    Done when:
    - the lab shows at least 80% coverage with at most 3% wrong senses (Haiku today: 51% at normal, 80% at full, 2.5%
      to 2.7% wrong), at no more cost per 1,000 passages;
    - passages with nothing doubtful are shown without a call.
13. **Starting values, then words from your reading** [H, O].
    - First, predict a new word's starting value from its frequency rank, kanji shared with known words, script, word
      type and length, in the one fitter. This must land before any dictionary word is offered.
    - Then count, per entry, the safe or confirmed matches in what you read: counts only, never text. A word becomes
      eligible after 3 different days in 30. At most 5 a day join, ranked by days, general frequency and predicted
      chance, while the budget goes unspent.

    Done when:
    - the starting values beat a flat start on the replay, on each word's first exposures, by day;
    - the web spends at least 70% of its budget in the one unit;
    - at least 80% of admitted words get 8 exposures in their first two weeks;
    - admitted words come up after joining about as often as forecast, and more often than under a ranking by general
      frequency alone.
14. **Topical words in chat, found by code** [K]. Dictionary entries the matcher finds in the conversation's recent
    turns go into the brief with their odds. This replaces "no Japanese outside the lists". Done when topical words
    stay under half of chat's expected lookups, and their class's calibration interval lies within 0.83 to 1.2.

## Then: read out, refine, unify

15. **Read out the delayed-start experiment**, once 80 words a half have reached week 3 (about 8 weeks from step 6 at 5
    new words a day). This is the direct test of row E.
16. **A memory strength per word** [D], on an FSRS-shaped curve, only after 60 days of log and a positive step 15.
    Done when it beats the shared recency term on the replay, on learning words, by day.
17. **One planner, two executors.** The refresh ranks and prices every word once. The web packs passages from it, and
    the chat brief lists its top costly words. This deletes the constants mirrored between Python and JavaScript and
    gives chat the web's exploration. Done when a test shows both surfaces get the same ranked list and the replay is
    no worse.
18. **Prompt iteration per model and surface.** Web: the lab scores matcher plus checker. Chat: a replay lab built from
    your recorded prompts. Each variant over 3 or more seeds; stop when the gain is smaller than the spread between
    seeds.
19. **Reading speed as evidence.** Slower paragraphs probably held unknown words. Every paragraph has a speed, so this
    is evidence even without a lookup.
20. **Later:** a tolerance model by context [C] and a lookup habit that changes over time [R]. Grammar patterns as
    items are built for chat (2026-11, docs/grammar.md): a sentence-open model by length and the difficulty of each
    pattern not known yet, one pattern learned at a time. Next for them: the web's sentences, and a pattern's
    difficulty per sentence position or with more than one learning pattern.

## After deployment

**Extension 0.11** (docs/swap-prompt.md, "Heavier than normal"): update the host and the collector before the extension,
since a host that doesn't know a plan's `paint` rejects its shown record. Then read `paint` in the log: the share of web
plans that are sure paintings says how much short views depress every density figure, and sentence marks at +1 and +2
say what Haiku makes of last clauses, which nothing has measured.

**The review wedge** (scripts/review.py): update the collector before the T3 Code builds with the wedge, since an older
collector has no `/v1/review/next` and rejects review items. Its first refresh refits from nothing under the new model
version (fit 6). Then watch `fw.py knowledge` for the separation of the reveal times and `fw.py status` for the probe's
rolling estimate.

**Review cards scheduled like Anki** (scripts/srs.py, scripts/review.py; docs/adaptive-difficulty.md, "Review cards on
a schedule"): update the collector before the T3 Code and phone builds with the graded review panel, since an older
collector drops a review item's grade without a word (the item is stored, the grade lost for good) and sends no
queue, intervals or counts. Its database gains the grade columns on first open (ALTER TABLE). Nothing refits: on a
log without grades the model is the same. Then watch `fw.py review`: the review cards' retention against the 90%
aimed for (well above it says the reading outside the reviews is doing part of the work, the conservative side of
feeding FSRS the reviews alone), and the graded reviews' count, until about 1,000 make FSRS's own parameters worth
fitting (`fw.py review revlog` for the optimizer). Later: test whether a gloss opened in reading predicts an Again at
the word's next review, the first candidate for feeding reading to the schedule; and read a probe card's grade in the
probe's rolling estimate, which still reads reveal times alone.

**Sentence calibration and English only** (docs/grammar.md): update the collector before the T3 Code builds with the
English-only shortcut and the panel, since an older collector has no `/v1/english` and doesn't send `english` with the
level. The first refresh on each machine with chats reads every transcript again for its sentence records (once, in
the background). Then watch `fw.py status` for the learning pattern and the sentence open rate (intake waits while it
is 5% or more), `fw.py knowledge` for the length slope and the hardest patterns, and check-reply's flags in replies:
if the 9-character limit leaves too few Japanese sentences, the target (`sentence_model.TARGET`) is the knob.

**Render-time levels in chat** (docs/chat-weave.md, "Levels after the fact"): update the collector first (it takes the
report items' `level` and the sentence records' new fields; an older one rejects those records and keeps every level of
a message in one row), then the T3 Code builds that render `data-on` and `data-jw` (an older build shows both twins of
a sentence), then the hook. Then watch `fw.py calibrate`'s line on the twins: the opens of the twins placed at random
against those the model ranked, and the open chance over every twin written by inverse propensity. If the random ones
are opened clearly more than predicted, weight the sentence fit by 1 / propensity (sentence_model.py says how), or
raise `twins.EXPLORE`. Also watch how many twins a reply carries and how long replies take to write: the cap
(`chatweave.TWIN_SLACK`) is the knob.

**Deploying, in this order** (each newer piece sends records an older one rejects or drops: briefs, probes, labels,
entry counts, and plan fields such as `sentencePrice`, `spent` and `arms`):

1. **The collector first** (functor): update its app to this code and restart the collector service. Optionally run
   `fw.py relabel --dry-run` there first: on the 2026-10-20 snapshot the switch to labels from the model changes 73 of
   1,766 words (49 Anki cards with 11 to 19 day intervals and all 21 shaky words become known). The first refresh
   applies the switch, starts the delayed-start experiment and refits from nothing under the new model version (about
   20 s on today's log, once).
2. **Then this machine:** merge `plan` into `main` (the chat hook runs from this checkout, so this is the moment it goes
   live) and run setup, which copies the host scripts, the data files and the extension. The hook's first run
   re-reads the last 30 days of chats for entry counts (about 15 s and 250 MB, once) and builds the reading index in
   the background (17 MB).
3. **Then the extension:** reload it (Chrome: the unpacked folder; Firefox: re-sign with `tools/sign-firefox.sh`, or
   load it as a temporary add-on). Bump its version at release. A remote native host is updated on its own machine.

**Undoing:** `fw.py relabel --restore` within 30 days puts back the old labels and stops the model from labeling;
then revert the merge on `main` and reload the previous extension.

**What to watch, and when:**

- **Day 1:** `fw.py status` (labels from the model, intake, held-back words), a web page showing its first Japanese
  within a second, and `fw.py calibrate` running.
- **Week 1:** the share of chat lookups on Japanese outside the lists (was 72%; topical words now have their own class),
  and whether the web spends its budget now that its candidates come from code.
- **Weeks 2 to 4, step 9:** each class's interval of seen against predicted within 0.83 to 1.2; `fw.py evaluate` for
  the fit against step 4's, word features against a flat start, and recency, each by day. Decide then whether to turn
  on the per-surface reading model (`SURFACE_TUNING`) and reading speed as evidence: each switch says in
  docs/adaptive-difficulty.md what result should turn it on.
- **Day 30:** the first probe (`fw.py probe`), and every 30 days after. Since 2026-11: the probe's rolling estimate
  in `fw.py status`, once the review cards have run a few weeks; and `fw.py knowledge` for how well the reveal times
  separate known from not known (a weak separation means the cards say little, the made-up words included). Since
  the cards are graded: `fw.py review` for their retention against the 90% aimed for.
- **About week 8, step 15:** `fw.py experiment` reads out the delayed-start experiment.
- **Day 60 or later, step 16:** a memory strength per word, if the experiment shows that exposure teaches.
