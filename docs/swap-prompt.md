# How the extension picks its Japanese

The extension sends readable passages to the chosen model (Gemini, or Claude Haiku since 0.5.0) with one prompt
(`extension/src/prompt.js`) and paints what comes back. Since 0.4.0 the answer can hold three kinds of swap:

| Kind | What it replaces | Example |
|---|---|---|
| word | one English word or fixed expression, in English word order | "a 新しい version", "Honestly I 思う this is" |
| phrase | a short phrase, rewritten in Japanese word order with particles | "Even ten minutes 寝る前 makes a difference" |
| sentence | a whole short sentence, written fully in Japanese | "昨日、母と一緒に店に行きました。 We bought 魚 and some お茶." |

A phrase or sentence comes back split into pieces, `"母|と|一緒に|店|に|行きました|。"`, each one either a listed word
(or a conjugated form of one) or grammar from a fixed set: particles, です/だ, ます forms, ない/た/て/ている/たい endings,
こと and の. `validateSwaps` in `extension/src/levels.js` works out which listed word each piece is and drops the whole
swap if any piece is an unlisted word, if the pieces don't spell the Japanese, or if a sentence doesn't end like one.
The pieces are what gets painted, so the Japanese only has to say the same: bars, commas and full stops in it that the
pieces leave out are let through, and a phrase loses a final full stop (the English sentence keeps its own).
A conjugated piece has to be a real form of the word, made from its own conjugation (行きました and 行って for 行く, not
行いて; 見せた is not a form of 見る). So a sentence only ever uses words from your list, and a model slip costs a swap,
never a wrong word on the page. Glosses, romaji and kana all come from the list, so a word swap comes back as just the
English, the Japanese and a few words of context (`{"k": "w", "o": "but", "f": "でも", "c": "x but y"}`). That compact
answer is about 40% shorter than one naming every piece's word and gloss, and output costs five times what input does.

Three mistakes the prompt forbids are also caught in code, because Gemini Flash kept making them on real pages, or Haiku
did once each passage named the listed words in it (below):

- a number word that is a label, a rank or a pronoun: "Day one" (日 一つ), "number one", "chapter two", "the first",
  "the one", "no one", "one day" (someday), "one-on-one", or a counter like 一つ before hours or people
  (`numberTrap`). "two ideas" -> 二つ and "only one of the ideas" stay.
- a Japanese-order phrase ending in a verb with English still hanging after it: "Yesterday I 店に行きました with my mom"
  (`dangles`). It may be followed by punctuation or a word that starts a new clause ("and", "but", "so"... or a subject
  pronoun: "As soon as we ie ni hairimashita we could smell the smoke", in kanji and kana on the page).
- a word swap that can't stand where it is (`misplaced`): mae or ato ("before / front", "after / later") for "before" or
  "after" ("mae you run the script"), and a verb that acts on something, like tomeru "stop (something)", with nothing
  after it to act on ("the program will tomeru with an error"); since prompt 5 also ageru "give (away)" for "gave me",
  and a swap that would leave a contraction's tail on the page ("I've" as watashi've).

On the page a phrase or sentence is one group: each of its words is underlined and glossed on its own (a peek is logged
for that word), a sentence's 。 shows the translation of the whole sentence, and a click anywhere flips the group back
to English. A flip of a group is logged but not counted against the word clicked. Since 2026-10-20 the 。 is logged too,
as T3 logs it: it is in the passage's glossed words, and opening it is a peek of the 。 (the price of a sentence is
fitted from these); before, nothing recorded a sentence's translation being opened on the web.

How many sentences go fully into Japanese is up to the extension's lookup budget, not the stage: since prompt 6 the
model is asked for every sentence the list can say, with the word and phrase swaps inside it, and `planSwaps` prices
each sentence and shows those the budget pays for (below, and docs/adaptive-difficulty.md). Before, the prompt asked
for about one sentence in five at Stage 3 and 4, one in ten at Stage 2 (or Level 2 without the plugin), and none at
Stage 1.

## Which text is a passage

`extension/src/page.js` picks the passages. Anywhere: a paragraph, list item, quote, heading, article or main element
holding no smaller one, in English or unmarked, at least 6 words and at most 2000 characters long, not inside a
link, button, form, menu, header, footer, code, editable or hidden part, and not on a zero-Japanese topic. Three
sites have their own passages, because their prose is not in those elements:

- **X**: each tweet's text.
- **Google results** (`/search` on google.com and the country domains): a result's snippet (its `[data-sncf]`
  container, or the `.VwiC3b` class), a featured snippet, the knowledge panel's description (`[data-attrid]`), and the
  paragraphs and list items of AI answers (`[data-subtree="aimc"]`: the overview, and People also ask answers once
  opened), all inside the results (`#rcnt`), never in ads. A snippet's date, an AI answer's citation chips and hidden
  headings are left out of the text; a part of an AI answer still hidden (collapsed, or fading in) waits until it
  shows. Titles are headings inside links, so they never match; when Google changes its markup, nothing is woven
  rather than its chrome.
- **YouTube** (www and m): comments and community posts (`#content-text`) and the video description, collapsed or
  opened. The description's text runs between its links (timestamps, URLs); each run is a passage, so a long
  description still gets Japanese. On m.youtube.com only the description: each comment there is a button.

On Google and YouTube a part shown later (an attribute like `hidden` or `aria-hidden` removed) is woven then, and
YouTube's moves between videos are woven afresh. Short comments stay English: under 6 words a passage gives the model
too little to fit a word into its sense.

## The meaning rules, and the mistakes they came from

Tested on 2026-10-16 against the 17 passages in `tools/prompt-lab/corpus.json` and 8 unseen ones in
`heldout.json`, with a real Stage 3 word list (297 known, 21 learning). The old prompt on Gemini 3.8 Flash made these
mistakes, each now a rule in the prompt:

- "Day one" -> 日 一つ, "number one" -> number 一つ, "two of the founders" -> 二つ: counters count things only, and
  number words in ranks, orders and labels stay English.
- "the best thing that happened" -> 物: 物 is a physical object; abstract "thing" stays English.
- "Before you run the script" -> 前 you run: 前 and 後 never join clauses.
- "I think" -> 思う with the subject dropped: subjects stay.
- "the program will stop" -> 止める: a verb that acts on something never replaces one where the subject itself stops.
- "You don't need much time" -> 要る "need" passed off as the listed いる "exist": each word only in its listed sense.
- "every time" -> 時間, "for a year now" -> 今: sense rules for 時間 and 今.

## Models

Same prompt, same 16 passages (`tools/prompt-lab/eval16.json`: most of `corpus.json` and `heldout.json` plus two
number traps), low thinking, the real Stage 3 word list, measured 2026-10-17 through the extension's own engine:

| Model | One call of 16 passages | Cost for 16 | Quality |
|---|---|---|---|
| Gemini 4 Argon | 16 s | $0.025 | No sense errors seen; the most phrases and sentences ("昨日、母と一緒に店に行きました。") |
| Gemini 3.8 Flash | 8-12 s | $0.006-0.008 | Mostly right; fewer sentences, and some slips the code checks above now drop |
| Claude Haiku 5.5, light thinking (adaptive, low effort) | 45 s; 24 s as three calls of 6, 6 and 4 at once | $0.006-0.008 | Close to Argon: 4-11 phrases and 3-6 sentences a run, about one dropped swap |
| Claude Haiku 5.5, no thinking | 8-12 s | $0.0015 | Ignores the rules: 15-21 dropped swaps a run, unlisted words, empty answers, almost no phrases |
| Claude Sonnet 5.5, no thinking | 10 s | $0.020-0.032 | Cautious (1-2 phrases), and one sense error ("a new language" as 新しい日本語) |
| Gemini 3.1 / 3.5 Flash-Lite, GPT-6 Luna | | | Too many sense errors and unlisted words |

Argon is not on Google's free tier and doubles its price on 2026-12-01 ($2/$10 to $4/$20 per million tokens in/out);
3.8 Flash doubles on 2027-01-01. Haiku 5.5 is $0.10/$0.50, cache reads $0.01; thinking tokens are billed as output.
It needs an Anthropic API key (paste it in the popup, or save it to `~/.japanese-weave/anthropic_key` and the native
host passes it on); the request asks for adaptive thinking at low effort and a JSON schema, and marks the prompt in two
parts for Anthropic's cache. When a call fails, Haiku falls back to Gemini 3.8 Flash if there is a Gemini key.
The popup offers Flash, Argon and Haiku; Flash-Lite is only Flash's fallback.

The prompt keeps the rules first and the word lists last, learning words at the very end (their order changes as words
are shown), so consecutive calls share a prefix. Gemini caches it implicitly: Google lists 4,096 tokens as the
minimum, but repeated calls of this ~3,950-token prompt had about 2,100 tokens served from the cache.

## Batching

The prompt is about 3,300 tokens before any passage, and calls used to carry about 2 passages each, so most of the bill
was the same prompt sent again. Since 0.5.0 the background puts the cache misses of every tab in one queue and sends
them together, up to 16 passages a call (`MAX_BATCH` in `models.js`), one call at a time: passages that turn up while a
call runs (scrolling, other tabs) all go in the next one, and a passage another tab already got back or is waiting for
is not asked for again. Haiku thinks first, so it gets calls of 6 passages, three at once (`BATCH` and `PARALLEL`):
the 16 test passages came back in 24 s instead of 45 s, at about the same cost ($0.0081). The 16 test passages:

| | Flash | Argon |
|---|---|---|
| 0.4.0: full answers, two calls of 8 | $0.0132 | $0.040 |
| compact answers, two calls of 8 | $0.0108 | |
| compact answers, one call of 16 | $0.0068 | $0.025 |

With 16 passages in a call, Flash wrote about as much Japanese but dropped phrases almost entirely (0-1 per run,
against 4 with 8 per call). One line in the prompt fixed that ("make it a phrase wherever one fits... Give every
passage this care, however many come at once"): 3-6 phrases per run at 16.

## Haiku's thin pages, and what fixed them (2026-10-19)

Two days after the switch to Haiku, pages carried about half as much Japanese (5.7% of words against 11.2% with Flash
on 10-16) and fewer learning words among the swaps (10% against 27%). A day's swap cache held 557 passages: 43% with no
validated candidate and 38% with one, mostly I, but, this, we, you. Measured with Haiku 5.5 at low effort and the real
Stage 3 word list (297 known, 22 learning) on two new sets: `live.json`, 30 short passages written like the cache's
(titles, tweets, comments, median 17 words), and `web.json`, 30 passages each saying the English of a learning word
somewhere. Each passage lists the learning words a careful answer would use (`learn`) and its traps.

What it was:

- Not validation. A run dropped 1 to 12 of 40 to 64 returned candidates, nearly all rightly (hanging verbs, unlisted
  words). The wrong drops were Japanese written with the bars or with a comma the pieces left out, and a phrase followed
  by a new clause that starts with a subject pronoun; both are let through now.
- Haiku returned too little: 1.3 to 2.1 candidates a passage, and none for 10-30% of `live.json`, against "one or two
  word or phrase swaps" a sentence. Its thinking (`display: "summarized"`, lab only) says why: it settles on "one clean
  swap" and drops others as "a bit awkward"; it misses listed words in the long list or takes them for unlisted ("thing",
  "night", "people" are all listed); it read `"c": ... words that contain o` as words containing the letter o; it
  skipped a complaint about parking as "borderline driving-related".
- Calls of six were thinner than calls of one (1.2-1.5 against 1.5-1.7 valid candidates a passage on `live.json`), and
  the extension's queue mostly sends one passage a call anyway (486 calls for 557 passages).
- Learning words were skipped where the passage says them: 8 of 31 found on `live.json` (six a call), 11-14 of 31 (one
  a call), 17-23 of 43 on `web.json`. The grammatical ones most: "about" (nitsuite), "has" or "there is" (aru),
  "became" (naru), "different" (chigau), "entered" (hairu), "in front of" (mae).
- Raising effort to medium found a few more learning words (17 of 31 against 11-16 at low, same prompt) at 1.6 times
  the cost and 1.7 times the time; not taken.

What changed (`PROMPT_VERSION` 4; the cache revision includes it, so every passage is asked once more):

- Every passage carries a `target`: the most swaps `planSwaps` shows in it (two a sentence at Level 2, one at Level 1,
  the density's share when one is set; at first raised 1.4 or 1.8 times when heavier, see below), and the prompt says
  why: the extension
  picks from what comes back, learning words first, so one safe swap teaches little.
- Every passage names the listed words whose English it says (`hints.js`): `"learning": ["aru = have / exist: has"]`
  for learning words, `"listed"` for known ones. The glosses are matched with their English forms (has, became, men,
  cutest) plus a few the gloss leaves out (about, there is, different, myself), but not "have" as a helper verb, not
  vague English for a known word (and, so, that, when, time, ask...), never number words or katakana loanwords. About
  70-90 uncached tokens a passage. The prompt asks for every learning word that fits, even past the target, says the
  hints come by spelling alone and must pass the meaning rules, and that a word swap only has to keep the English
  readable (a phrase still teaches more).
- Zero Japanese only for passages that give or ask for health, legal, money, safety or driving advice, or report an
  emergency; a passing mention is fine. `c` is "2-6 words ... including the English o itself"; no swap without
  Japanese; soshite only between clauses; ja and dewa only as "well then".
- In code: `misplaced` (above), the subject pronoun in `dangles`, and the looser match of f against the pieces.

The same sets before and after, two runs each where there are two numbers:

| Set, passages a call | | Returned a passage | Valid a passage | Passages with none | Shown a passage | Shown with a learning word | `learn` words found | $ per 1,000 passages |
|---|---|---|---|---|---|---|---|---|
| live, 6 | before | 1.3, 1.6 | 1.2, 1.5 | 30%, 10% | 1.1, 1.4 | 25%, 19% | 8, 8 of 31 | 0.29 |
| | after | 2.2, 2.0 | 2.1, 2.0 | 7%, 7% | 1.8, 1.8 | 31%, 28% | 17, 14 of 31 | 0.35 |
| live, 1 | before | 2.1, 1.7 | 1.7, 1.5 | 20%, 17% | 1.5, 1.3 | 36%, 30% | 14, 11 of 31 | 0.44 |
| | after | 2.4 | 2.3 | 3% | 1.9 | 28% | 16 of 31 | 0.55 |
| web, 6 | before | 1.7, 1.7 | 1.7, 1.5 | 10%, 17% | 1.5, 1.4 | 40%, 38% | 20, 17 of 43 | 0.28 |
| | after | 2.8, 2.8 | 2.7, 2.6 | 0%, 0% | 2.3, 2.2 | 50%, 51% | 31, 30 of 43 | 0.33 |
| web, 1 | before | 2.1 | 1.9 | 3% | 1.7 | 46% | 23 of 43 | 0.68 |
| | after | 2.8 | 2.6 | 0% | 2.2 | 39% | 24 of 43 | 0.64 |
| eval16, 6 | before | 3.1, 2.6 | 2.9, 2.5 | 6%, 12% | 2.8, 2.5 | | | 0.50 |
| | after | 4.1, 3.9 | 3.9, 3.8 | 6%, 6% | 3.6, 3.6 | | | 0.52 |

"Shown" is what `planSwaps` keeps (two a sentence), so after the change most passages are at that ceiling. Phrases
held up (7-17 a run of `eval16.json` against 8-14, and 2-3 sentence swaps against 2-4); an earlier wording without "a phrase
teaches more" had half as many. The one passage of `eval16.json` left with none is "Day one of flying...", rightly.

Accuracy, read swap by swap: about 3-4% of swaps carry a wrong or doubtful sense before and after, so there are more of
them in number. Before: chikara for electric "power", "all the hard work" as "all the difficult work", "one more player"
as ichi. After, the same two plus a few the hints invited: chikara for "power users", tanomu "request" for "asked if",
tsumori "intention" for "your plan", kaeru "go home" for "go back", ikaga for "How I Became...", dono for "which one",
motto for "one more player" ("how" and "ask" no longer point at a known word). The two hint-invited rule breaks seen in testing, mae for "Before you run the
script" and tomeru for "the program will stop", are dropped in code now. Number traps held: "Day one", "one thing",
"the last one" were all left English or dropped.

Cost: the prompt grew by about 490 tokens (read from the cache), each passage brings 70-90 tokens of hints, and the
answers grew by 15-30%. A call of one passage went from about $0.00045 to $0.00055 (cache writes aside), a call of six
from $0.0017 to about $0.0021: about a fifth more, or $0.36 a day instead of $0.30 at today's 490 calls.

Batching: calls of six cost about $0.35 per 1,000 passages against $0.55 for calls of one, but take 16-24 s against
5-6 s, and passages that reach the queue together already share a call. A gather delay would only group passages that
come in while scrolling, saving at most about $0.10 a day for seconds more before every passage, so the queue is
unchanged.

Flash and Argon get the same prompt and hints. They were never as timid as Haiku (Flash already filled pages at 11%),
so for them the target mostly confirms the old "one or two a sentence", and the hints mainly add learning words; the
known-word hints may invite more literal swaps from Flash, which has more sense slips than Haiku. Not measured: run
both through the lab once before relying on them.

## Full intensity (+3)

At the top of the reader's intensity scale the page gets all the Japanese the list allows. The amount paragraph is
replaced: passages carry no target, and the model goes through each passage sentence by sentence, writing every sentence
listed words and the grammar list can say completely as a sentence swap (at any stage), and then swapping every listed
word, learning or known, whose listed meaning the passage says, in every place it says it (each with its own `c`),
pronouns included; since prompt 6 also inside the sentences it wrote whole, which +1 and +2 may leave in English. Each
passage's hints come with every spelling found and how many places (`"watashi = i: I (3 places)"`, in Japanese script in
the prompt), as places owed a swap unless a meaning rule forbids it. "Skip a passage if a swap would make it hard to
follow" is left out; the meaning rules and the validation are the same. `planSwaps` shows everything that validates and
can be placed, with no ceiling or thinning: a sentence rather than the word swaps inside it, and a word again where its
English comes again. Since prompt 5 the heavier levels pick from this answer too (below), so it is asked once for +1, +2
and full, in calls of 3 passages for Haiku (`FULL_BATCH` in `models.js`), and has its own cache.

## Intensity levels measured (2026-10-19)

The lab now measures every level at once, as the extension picks them, without the random thinning and with it
(averaged over 200 days of seeds), and judges coverage and sense against `tools/prompt-lab/oracle.json`: for each
passage of `web.json`, `live.json`, `eval16.json` and `heldout.json`, read by hand starting from the hints `hints.js`
gives, the places a listed word fits in its listed sense (`use`, 4-8 a passage), swaps that are fine but not owed
(`ok`), English a listed word matches in another sense (`not`, a swap there is wrong), and the sentences listed words
can say whole. Haiku 5.5 at low effort, the real Stage 3 list (319 words, 22 learning), each main set twice before
and twice after, heldout once each.

What it found, before prompt 5: the heavier levels barely differed from normal, and full barely from +2. Asked for 1.4
or 1.8 times the swaps, Haiku returned about what it returns at normal (normal itself fills about 75% of its target),
so +1 came to 1.05-1.21 times normal and +2 to 0.97-1.42; in one run +2 showed less than normal, in another full less
than +2. Telling the heavier levels to reach their targets with known words got +2 to 1.29-1.55 but doubled its wrong
swaps (to 8%). Full covered 58-76% of the places a listed word fits: it skipped the second "I" of a passage, "this"
before a noun (kono), aru in five or six of its six places in `web.json`, and most sentences it could have said whole.

What changed (prompt 5):

- +1 and +2 pick from the full answer, as -1 and -2 pick from normal's (`answerLevel` in `levels.js`): the ceiling
  stays the level's per-sentence rule times 1.4 or 1.8, and the candidates are full's, at full's accuracy. The heavier
  targets, sentence rates and wording are gone from the prompt, and stepping between +1, +2 and full never waits for
  a model.
- The full prompt as above (sentences first, with examples not in the test sets; every place; pronouns; aru where
  something exists or is owned, not "had a meeting"; leave the English when unsure), and the hints' spellings and
  counts at full.
- Validation: a phrase that is one conjugated verb alone ("said" as itta, "There was" as atta, "is becoming") with
  English after it is kept as a word swap of the listed form instead of dropped as dangling (not where its English
  has a subject); an empty piece from a bar at the end is ignored; the past of suru in a phrase ("studied", benkyou
  shita) is suru, not the kanji for "below" read shita, which it had been glossed as; ageru "give away" never stands
  for "gave me", and no swap may leave a contraction's tail ("I've" as watashi've).

The amount, without the thinning, mean of the six runs of the three main sets (range of the ratio in brackets):

| Level | Factor | Before: shown a passage, Japanese share | x normal | After: shown, share | x normal |
|---|---|---|---|---|---|
| -2 | 0.5 | 1.32, 9.7% | 0.50 | 1.32, 9.7% | 0.50 |
| -1 | 0.7 | 1.84, 12.7% | 0.70 | 1.84, 12.7% | 0.70 |
| 0 | 1 | 2.64, 17.9% | 1 | 2.64, 17.9% | 1 |
| +1 | 1.4 | 2.96, 20.9% | 1.13 (1.05-1.21) | 3.24, 23.9% | 1.25 (1.08-1.34) |
| +2 | 1.8 | 3.18, 21.6% | 1.25 (0.97-1.42) | 3.45, 25.3% | 1.34 (1.08-1.50) |
| full | all | 3.41, 23.2% | 1.34 (1.05-1.72) | 3.93, 28.2% | 1.55 (1.21-1.81) |

With the thinning, as pages look: before 1.15, 1.61, 2.31, 2.58, 2.78 and 3.41 swaps a passage at -2..full (8.5%,
11.3%, 15.7%, 18.4%, 19.1%, 23.2% of the words); after 1.15, 1.61, 2.31, 2.83, 3.01, 3.93 (8.5%, 11.3%, 15.7%, 21.0%,
22.2%, 28.2%). The ratios of -2..+2 are the same as without it (every level is thinned alike); full is not thinned, so
it is 1.77 times a thinned normal page. In the Japanese share of the words +1 and +2 come to 1.34 and 1.42 times normal,
full 1.60. On `heldout.json` (8 passages, once): +1, +2, full 1.50, 1.36, 1.43 before, 1.43, 1.46, 1.61 after.

Coverage of the oracle's places, and wrong-sense swaps among the swaps judged, pooled over the same runs:

| Level | Coverage before | after | Wrong before | after |
|---|---|---|---|---|
| 0 | 51% | 51% | 2.7% | 2.7% |
| +1 | 59% | 66% | 1.8% | 2.4% |
| +2 | 64% | 70% | 2.2% | 2.4% |
| full | 68% | 80% | 2.3% | 2.5% |

Full per set: web 71% and 76% before, 79% and 83% after; live 62% and 58%, 71% and 73%; eval16 69% and 74%, 80% and
91%; heldout 83%, 86%. Sentences the list can say whole and full rendered whole: 8 of 24 before, 10 of 24 after.
Places full missed, over seven runs: 256 before, 166 after, of which 128 not returned and 38 dropped in validation
(nearly all a phrase in Japanese order ending in a verb with English after it, rightly). The most missed: "I" (watashi,
mostly a second one) 10, nitsuite "about" 9, aru 7, yomu "read" 7, omou "think" 7, chigau 6, yameru "stop" 6, iu "say"
6, miru "see" 6, shiru or wakaru "know" 6, mada "still" 6.

The wrong-sense swaps are mostly the same ones at every level, normal included: chikara for electric "power" and
"power users", zenbu for "all for a new phone", tsumori for "your plan", chiisai for "little sister", kaeru for "went
back outside" and "go back". Full adds a few: motto for "one more player", "hard work" as muzukashii shigoto, shimau
for "stopped talking", tetsudau for "helps me rest", and aku (the door opens) for "opened the window". The ageru and
contraction cases are dropped in code now; the shita gloss was wrong on every such phrase before, at every level.

Calls (Haiku, per call):

| Answer | Passages a call | Seconds, mean and max | $ a call | $ per 1,000 passages |
|---|---|---|---|---|
| normal | 6 | 19.8, 29.2 | 0.0026 | 0.45 |
| +1 of its own (before) | 6 | 23.1, 38.0 | 0.0027 | 0.50 |
| +2 of its own (before) | 6 | 22.5, 35.0 | 0.0027 | 0.46 |
| full before | 6 | 23.9, 33.8 | 0.0029 | 0.51 |
| full after, calls of 6 | 6 | 29, 45.4 | 0.0031-0.0048 | 0.52-0.89 |
| full after, calls of 3 | 3 | 16.6, 26.9 | 0.0021 | 0.71 |

The full answer is about twice as long as normal's, so in calls of 6 its longer passages came within 15 s of the 60 s
timeout; calls of 3 cost about the same per passage and halve the wait. +1 and +2 no longer cost a call of their own:
a reader stepping from normal to full pays for one answer instead of three.

Left open: +2 cannot come near 1.8 times normal on these passages, because full itself is only about 1.55 times
(normal already shows about half the places the list fits, so all of them is roughly twice normal); +1 and +2 now sit
at 1.25 and 1.34, in order, and below full in every run. Short passages make the ceilings coarse (a one-sentence
passage goes 1, 1 or 2, 2, 3, 4 from -2 to +2), but the lighter levels round at random and land on 0.50 and 0.70
exactly, and at +1 and +2 the full answer's size, not the ceiling, is what usually limits. No deterministic fallback
was added for the places full still misses: the second "I" was the only common case safe enough, and the prompt now
gets most of them. Flash and Argon were not measured on prompt 5.

## Levels as lookup budgets (2026-10-20)

The measurements above are of the old ceilings (one or two swaps a sentence, times the level's factor). Since then a
web level is a lookup budget (`scripts/intensity.py` BUDGETS, mirrored in `levels.js`; docs/adaptive-difficulty.md):
`planSwaps` shows every candidate whose words are all known at a glance (a mean lookup chance under 0.08) wherever the
model offered it, at every level but off, and spends 0.15, 0.3, 0.5, 0.8 or 1.2 lookups per 100 words (-2..+2) on the
rest, at most one of those a sentence up to normal and two when heavier. The random thinning keeps each known word with
the passage's share (20% to 100% since 2026-10-20, 50% to 100% before) and leaves its budget alone (before, it scaled
the budget by the same share); full is unchanged. The prompt and its per-passage `target` are unchanged too, so the
target now only says how many swaps to offer: with known words free, what the model offers is what bounds them. The
lab's "ceiling" column still computes the old rule.

Since 2026-10-20 a whole sentence is priced too: it costs the chance its translation is opened if it is read
(`SENTENCE_PRICE`, 0.08, until the native host sends a fitted one), plus the lookup chances of any of its words not
known at a glance, and it is its sentence's one budgeted swap. So the budget, not the stage, decides how many
sentences a page gets, and at full, which has no budget, they are free. Sentences may spend up to `SENTENCE_SHARE`
(18%) of a passage's budget and words and phrases the rest, and what one can't use goes to the other, the rule chat
uses too: on today's pages, where learning words are scarce, sentences still get what the words leave, and once code
finds more candidates they keep their share. `planSwaps` also counts only the candidates
`text.js locate` can place, and keeps none that overlaps one already kept, so a plan's swaps, `limit` and `expected`
are what the page shows.

## Sentences by budget, not by stage (prompt 6, 2026-10-20)

`planSwaps` now prices a whole sentence (the chance its translation is opened, 0.08 until the host sends a fitted
one) and lets the budget decide how many a page shows, so the prompt stops rationing them by stage. What changed
(`PROMPT_VERSION` 6, so every passage is asked once more):

- Normal's prompt asks, at every stage, for a sentence swap for every sentence listed words and the grammar can say
  completely, outside the `target` (which now counts word and phrase swaps only), and for the word and phrase swaps
  inside each such sentence too, so a sentence the budget leaves in English keeps its known words. The kinds section
  says "give the sentence swap as well as the word and phrase swaps in it" instead of "prefer the sentence swap". The
  phrase line ("Make it a phrase wherever one fits... Give every passage this care") now reaches Stage 1 too.
- Full's prompt asks for the words inside its sentence swaps as well, since +1 and +2 pick from its answer and price
  the sentences.
- The prompt is about 270 characters longer at Stage 3 (about 70 tokens, in the cached prefix), 510 at Stage 1, 100 at
  full.

Not measured yet: no lab run on prompt 6 (it costs API calls). The risks, against the measurements above, which were
tuned with the old rates:

- More sentence swaps per answer. Sentences are where whole-swap sense errors show ("bad" in the oracle), and
  validation catches unlisted words, not a wrong sense. Watch wrong-sense swaps (2.5-2.7%) and sentences judged bad.
- Haiku settles on "one clean swap" when unsure; asked for a sentence and the words inside it, it may return one or
  the other. If it drops the words, a sentence the budget can't afford leaves its known words in English, and coverage
  at normal (51%) falls; if it drops the sentences, the page gets fewer than before.
- Longer answers: sentence swaps are the longest items (pieces and a gloss), and full's answer, which came within 15 s
  of the 60 s timeout in calls of 6, grows by the words inside its sentences. Watch seconds and output tokens per call
  and $ per 1,000 passages.

To check it: `lab.mjs` with Haiku on `web.json`, `live.json` and `eval16.json`, two seeds each, at normal and full,
against prompt 5's numbers above: sentences offered and shown a passage, coverage, wrong senses, the oracle's
sentences rendered whole, and the call's seconds and cost. `planSwaps` in the lab prices sentences the same way.

## Tuning it again

`tools/prompt-lab/lab.mjs` runs passages through the extension's own pipeline and prints them woven, ⟦word or phrase⟧
and ⟪sentence⟫:

```sh
python tools/prompt-lab/words.py > /tmp/words.json      # your word state, as the extension gets it
GEMINI_KEY_FILE=~/path/to/key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs gemini-4-argon tools/prompt-lab/heldout.json
ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs claude-haiku-5-5
```

`BATCH=8` sends 8 passages a call instead of 16 (`BATCH=1` is how most calls go out live). It asks for normal's
answer and full's, measures every level from -2 to full from them as `planSwaps` picks (`LEVELS=0,3` for fewer),
without the random thinning and with it, and prints per call the time, tokens and cost, and per level the swaps shown
a passage, the Japanese share of the words, swaps with a learning word, phrases and sentences, and the ratios to
normal. With the oracle (`oracle.json`; `ORACLE=` another) it prints coverage, whole sentences, the places full missed
and why, every wrong-sense swap and every shown word the oracle doesn't judge yet, to be added. `SAVE=run.json` keeps
the answers and `REPLAY=run.json` measures them again for free (`REVALIDATE=1` checks them again by today's
validation, `HEAVY_ANSWERS=1` picks +1 and +2 from answers of their own as before prompt 5); `SUMMARY=` writes the
tables as JSON. The header of `lab.mjs` lists the rest.

Bump `PROMPT_VERSION` in `prompt.js` whenever the prompt changes, so cached answers from the old one aren't reused.

## Places found by code (2026-10-20)

The plan (PLAN.md section 6) has code find every place a listed word could go, and a model only check the doubtful
senses. The first half is built but not used yet: a matcher in `extension/src/matcher.js` and `scripts/matcher.py`,
scored on the same judged passages by `tools/prompt-lab/matcher-lab.mjs` (docs/matcher.md). With the 324 introduced
words of 2026-10-20 it covers 379 of the 408 places (93%) at no cost, where Haiku covered 51% at normal and 80% at full,
but 26% of its judged hits are the wrong sense (Haiku: 2.5% to 2.7%). Bringing that down is the model check's job.

## Code finds the places, a model checks them (2026-10-21)

The second half, built and measured in the lab but not wired into the extension: `extension/src/verify.js`, scored by
`tools/prompt-lab/check-lab.mjs` on the same 79 judged passages with the 324 introduced words of the 2026-10-20
snapshot. Haiku stops proposing swaps; code proposes every place, and Haiku only answers questions about them.

**What code does.** For each passage, `planPassage` takes the matcher's spans and numbers them. An option flagged safe
(`senses.js`, docs/matcher.md "Sense flags") is taken by code; a span with no safe option, and every span of
closed-class English, goes to the model with all its options. Code also marks the dense sentences
(`denseSentences`): 3 to 14 words, at least one span, and every word inside a span or English the grammar says without
a listed word (articles, the prepositions particles stand for, be, do and have, will, not, and, or, because, than, the
personal pronouns and want, `GRAMMAR_ENGLISH`). A passage with nothing doubtful and no dense sentence needs no call
(`needsCall`), and a passage on a zero-Japanese topic gets nothing, as the extension never sends one.

**What the model does.** One call per batch of passages that need one (`requestChecks`, Haiku or Gemini, with the
fallback model as `engine.js`). The user message lists each passage, then its spans in place with a few words around
them, lettered options where code is unsure and the sure word where it isn't, then the dense sentences:

```
[tweet-movie] Just watched it again. I really love this movie. The ending gets me every time.
1 Just watched it [again]. I really: a また = again / also; b もう一度 = once more / again
2 watched it again. [I] really love this: a 私 = i; b 僕 = i
3 it again. I [really] love this movie.: a 本当に = really / truly
4 again. I really [love] this movie. The: a 大好き = love / really like
5 I really love [this] movie. The ending: a これ = this / this one; b この = this one
6 really love this [movie]. The ending = 映画
7 this movie. The [ending] gets me every: a 終わる = finish / end; b 終える = end / finish
8 gets me every [time].: a 時間 = time / hours; b 時 = time / when
s1 "I really love this movie."
```

Haiku's answer in the final variant's second seed: `{"id": "tweet-movie", "picks": "1a 2a 3a 4a 5b 7- 8-", "swaps":
[{"at": "s1", "p": "私|は|本当に|この|映画|が|大好き|です|。"}, {"at": "5-6", "p": "この|映画"}]}`: また for "again", この
for "this", nothing for the noun "ending" or for "every time", the sentence whole, and "this movie" as a phrase.

The system prompt (`buildCheckSystem`, about 2,100 tokens, read from the cache after the first call) asks for a pick
per lettered span, the letter of the one option that says exactly what the English says there or `-`, checked against
the meaning rules of `prompt.js` that apply to choosing a word for a place; each s-numbered sentence written whole with
only the words given in the passage's spans and the grammar list; and a short Japanese-order phrase wherever
neighbouring spans make one. The answer (`CHECK_SCHEMA`) is compact, as above: per passage its id, the picks as one
string, and its sentences and phrases, each naming its place by number (`s1`, or `5-6` from the start of span 5 to the
end of span 6) with its pieces. The model never copies English: code fills in the English, its gloss (the English
itself, since the Japanese says exactly that) and its context. Every candidate, the word swaps of safe options and
picks included, goes through `levels.js validateSwaps`, which drops unlisted words, dangling phrases, labels read as
numbers and the rest (`candidatesOf`). The result has `validateSwaps`' shape, so `planSwaps` picks from it as today.

**How it was scored.** As `lab.mjs` scores swaps: each listed word of a candidate where it lands is right, fine,
wrong or unjudged; a phrase or sentence not judged bad covers every place inside it. "All" is every candidate (as
matcher-lab counts every option); "full" is what `planSwaps` shows at full (everything that can be placed, the same
here); "normal" is what it shows at normal with the budget, averaged over 50 days without the thinning. Seeds are
independent runs, each putting the passages into calls of 6 in its own order. Cost per 1,000 passages counts every
judged passage, called or not.

| Variant | Seeds | Coverage, all = full | At normal | Wrong (wrong / judged) | Function / content words wrong | Unjudged | Sentences whole, of 12 | Phrases valid | $ per 1,000 passages | s a call (max) |
|---|---|---|---|---|---|---|---|---|---|---|
| prompt 1, prompt-1 flags of the introduced words | 1 | 86.8% | 68.8% | 3.3% | | 20 | 5 | 0 | 0.343 | 17.4 (25) |
| prompt 1 | 3 | 85.9% (84.3-87.5) | 68.1% | 3.3% (2.8-4.1): 18/440, 12/424, 14/448 | 4.6% / 2.6% | 18-19 | 4.3 (3-5) | 0 | 0.345 (0.317-0.361) | 17.5 (26) |
| prompt 2 | 3 | 84.4% (82.8-86.5) | 66.7% | 0.7% (0.5-0.8): 3/388, 3/410, 2/392 | 1.0% / 0.5% | 8 | 4.7 (4-5) | 0 | 0.362 (0.342-0.377) | 18.6 (29) |
| prompt 2, thinking off | 3 | 60.3% (59.3-60.8) | 48.4% | 10.9% (9.9-11.5) | 13.1% / 10.4% | 16-19 | 5.3 (5-6) | 0-1 | 0.061 (0.056-0.066) | 3.2 (8) |
| prompt 2, dictionary glosses too | 3 | 89.2% (88.0-91.7) | 67.8% | 2.1% (1.5-2.5) | 1.6% / 2.9% | 86-96 | 6.3 (6-7) | 0 | 0.608 (0.589-0.637) | 28.0 (48) |
| prompt 2, calls of 12 | 3 | 86.5% (83.1-88.2) | 68.7% | 1.1% (0.7-1.4) | 1.0% / 1.3% | 6-11 | 4.3 (3-5) | 0 | 0.322 (0.316-0.327) | 31.2 (43) |
| prompt 2, medium effort | 3 | 85.0% (82.6-87.0) | 67.4% | 0.8% (0.3-1.2) | 1.0% / 0.8% | 8-9 | 4.3 (4-5) | 0 | 0.472 (0.457-0.481) | 23.7 (41) |
| prompt 2, phrases wherever one fits | 3 | 84.9% (81.6-87.0) | 67.4% | 0.7% (0.5-1.1) | 0.8% / 0.8% | 10-12 | 4.3 (3-5) | 16, 14, 9 | 0.426 (0.387-0.445) | 21.8 (37) |
| **prompt 3** (the same text), final flags | 3 | **85.1% (84.6-85.8)** | 67.8% | **0.8% (0.7-0.9): 4/430, 3/427, 3/403** | 1.0% / 0.8% | 7-10 | 4.0 | 16, 16, 6 | **0.399 (0.380-0.411)** | 20.6 (36) |

Flags: the first row's were prompt 1's on the introduced words alone (lost with a reboot); the next seven rows use
prompt 2's flags where two samples agree, the last the file's, where three do. Ranges are over the seeds; within a run,
the 90% interval of the wrong share at full from resampling passages is about 0.2% to 2%, and Wilson's 90% interval
of the final variant's pooled 10 wrong of 1,260 judged is 0.5% to 1.3%. No passage needed no call: every one has a
closed-class span. Haiku today, for comparison, on the same passages: 51% at normal and 80% at full, 2.7% and 2.5%
wrong, $0.45 per 1,000 passages at normal in calls of 6 (19.8 s a call, at most 29 s; $0.52 on `eval16.json`, about
$0.55 in calls of one as most calls go live) and $0.52 to $0.89 at full, where it rendered 10 of 24 sentences whole.

What the variants showed:

- **Prompt 1 to 2: the rules, not the format.** Prompt 1 made these in every seed: 一 for "One" in "In One Week", 時
  for "When" in "When I entered the room" and for "time" in "every time", 行く in "go to bed", 日 in "all day", そして
  for "and" inside a sentence (three passages: "huge and beautiful"), する for "does" in "does code review". Prompt 2
  adds the numeral rule `prompt.js` has (一 alone only as a plain numeral), 時 to the clause joiners beside 前 and 後, a
  rule for spans inside a longer expression, helper verbs for する and やる, and そして only between sentences. Wrong
  fell from 3.3% to 0.7%, and coverage by about 1.5 points, inside the spread between seeds. These rules were written
  after reading the lab's errors, on the same passages they are scored on: there is no held-out set, so expect more
  errors on new passages. What prompt 3 still gets wrong: 小さい for "little" in "my little sister" and 開く (the door
  opens) for "open a second store" in every seed, and once each 行く in "go to bed", する or やる for "does code
  review" and 力 for "power" in a chip's power use.
- **Thinking is needed.** Without it Haiku is six times cheaper and faster, but picks wrong on 11% and leaves 40% of
  the places; it returned 19 to 30 sentences a run, of which only 6 or 7 were valid.
- **More effort, bigger calls: no gain.** Medium effort changed nothing measurable at 30% more cost. Calls of 12 cost
  11% less and took 31 s instead of 19; the coverage difference is inside the seeds' spread.
- **The dictionary's glosses** find the places the listed glosses miss ("watch", "about", "mom", "little sister") and
  raise coverage to 89%, but double the doubtful spans (986 against 571), so the call costs $0.61 per 1,000 passages,
  over today's, and leave about 90 hits a run the oracle hasn't judged. Worth it once the oracle judges them and the
  call is cheaper; not now.
- **Phrases had to be asked for.** "Only where one fits well; most passages have none" got none in 9 runs. Asked for
  wherever one fits (prompt 3), Haiku wrote 6 to 18 a run, nearly all valid: この映画, 沢山の時間, 店の前に, 今年読んだ本,
  also some clumsy (もっと本 for "more books") or with their own subject (私はまだ待っている), which the prompt forbids
  and validation doesn't catch. They cost about a sixth more output and don't change coverage; they are what today's
  prompt values for teaching word order.
- **Sentences.** The density rule marks 10 sentences, 7 of them among the oracle's 12; Haiku writes 4 to 6, all valid,
  and leaves out those the list can't say ("I wanted to go home right then"). The oracle's other 5 sentences need a word
  the listed glosses miss ("watched", "mom", "fish", "idea"). This is about what today's full answer does (10 of 24).
- **At normal** the pipeline shows about 68% of the places, against 51% for today's normal answer: known words are
  free and the candidates are all there; what is left out is `planSwaps`' one use per Japanese word.

What did not reach the plan:

- **No passage was spared a call.** Closed-class English is always doubtful, and every passage has some ("I", "this",
  "and"). `needsCall` skips a passage with nothing doubtful and no dense sentence, but on these passages, and on most
  real ones, that never happens. The saving has to come from the call being cheap: about 4,200 output tokens a call
  of 6, nearly all of it thinking, against about 4,900 for today's normal answer (going by its cost).
- **Cost is under today's, by little.** $0.399 against $0.45 at normal in calls of 6. It is still one call per passage,
  as today; full no longer needs a second answer of its own ($0.52 to $0.89), so a reader at +1, +2 or full pays less.

For the integration step (NEXT_STEPS step 12, second half):

- Ship `data/senses.tsv` with the extension (setup copies only `extension/`) and load it with `senses.js load`; build
  the matcher's index from the word list (`buildIndex`, 13 ms for 1,766 words) whenever the list changes.
- Replace `engine.js requestSwaps` with: match, `planPassage`, `requestChecks` for the passages `needsCall` keeps, in
  calls of 6 (`BATCH`, unchanged), and `candidatesOf` with no answer for the rest and for a failed call (the safe
  options alone). Cache by `CHECK_VERSION` as by `PROMPT_VERSION`. `planSwaps` and the painting are unchanged.
- Then one candidate set serves every level: delete the normal prompt's target, the second cache, `answerLevel`,
  `swapLimit`, `capSwaps` and the density cap, as NEXT_STEPS step 12 says.
- Gemini: the request is built and tested (`verify.test.js`) but was not run; measure Flash in the lab before relying
  on it as the fallback.
- Re-run `check-lab.mjs` when the word list grows: every new word adds spans and pairs, and the flags of words added
  later need `sense-flags.mjs` (from the cache, free for words already flagged).

```sh
OFFLINE=1 WORDS=<copy of state.json> bun tools/prompt-lab/check-lab.mjs              # the code part: no calls
SPEND_LOG=~/.cache/japanese-weave-build/spend/senses.jsonl ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key \
  WORDS=<copy of state.json> SEEDS=1,2,3 SAVE=run.json VERBOSE=1 bun tools/prompt-lab/check-lab.mjs
REPLAY=run.json WORDS=<copy of state.json> bun tools/prompt-lab/check-lab.mjs       # score saved answers again, free
```

`GLOSSES=both`, `THINKING=0`, `EFFORT=medium`, `BATCH=12`, `PHRASES=rare` and `SENTENCES=0` give the variants above.
Every paid call's tokens and cost go to the spend log (`tools/prompt-lab/spend.mjs`), which refuses calls past
`SPEND_CAP`.
