# Next steps

Status: 2026-10-20, revised after the adversarial review. In order, each with what "done" means. Why each one
matters is in [PLAN.md](PLAN.md); the letters in brackets are its simplifications (section 8).

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
| 7 Probe | Done: `fw.py probe`, `/japanese-weave:probe` | Met: standard error 4 to 5 points at 60 words; interval coverage 89% to 94% in simulation |
| 8 Status from the model | Built; the switch happens at the first refresh after deployment (`fw.py relabel --dry-run` lists the 73 words it changes; `--restore` undoes it for 30 days) | Met |
| 9 Live check | Waiting: needs 2 to 4 weeks of data after deployment | Not yet |
| 10 Shared recency | Done, self-disabling: it stays off until the reader's own log shows short-term forgetting | Partly: the sim recovers the decay within a factor of 2 and learning words improve from 2.1 to 1.5 times (not within 20%); on the snapshot the fitted floor sits above every word, so the term is off |
| 11 Dictionary and matcher | Done: 26,969 entries; the matcher covers 93% of the lab's judged places | Met: 99.8% of the state's words and 39 of 42 unlisted lookups resolve |
| 12 Sense flags and Haiku's new job | Done in the lab and on two fresh sets; the extension wiring (12b) is in progress | Met against a relative bar: on fresh2, 83% coverage and 0.6% wrong senses at full against today's Haiku at 73% and 3.5%; on fresh, 81% and 1.1% after the fixes. Every passage still needs a call (closed-class words are always doubtful); the safe swaps show first |
| 13 Starting values and words from reading | Done (chat counts; web counts with 12b) | Partly: features beat a flat start on first exposures in the sim (better on 55 of 84 days); the snapshot can't show it in 5 days (about 2 to 4 weeks) |
| 14 Topical words | Done: code finds them in the last turns, at most 5, never in health, legal or money chats | Met in part: 19% of offers are the wrong sense (from 41%); calibrate has a topical class, linked by time |
| 15 Experiment readout | Waiting: about 8 weeks after deployment | Not yet |
| 16 Per-word memory strength | Waiting: needs 60 days of log and a positive step 15; its place in the code is marked | Not yet |
| 17 One planner, two executors | Deferred: a large refactor that changes exploration (draws per word per refresh instead of per passage); worth doing only after the live check shows the two planners disagree in a way that costs | Not started |
| 18 Prompt iteration | Web: done in the labs (three prompt versions, two fresh sets). Chat: a replay lab would spend the user's Claude limits; deferred | Partly |
| 19 Reading speed as evidence | In progress, behind a switch that stays off until the replay shows a gain | Not yet |

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
   Done when it prints the goal estimate with its interval, and the share of made-up words claimed.
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
20. **Later:** a tolerance model by context [C], a lookup habit that changes over time [R], and grammar patterns as
    items.
