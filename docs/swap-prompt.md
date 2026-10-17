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
A conjugated piece has to be a real form of the word, made from its own conjugation (行きました and 行って for 行く, not
行いて; 見せた is not a form of 見る). So a sentence only ever uses words from your list, and a model slip costs a swap,
never a wrong word on the page. Glosses, romaji and kana all come from the list, so a word swap comes back as just the
English, the Japanese and a few words of context (`{"k": "w", "o": "but", "f": "でも", "c": "x but y"}`). That compact
answer is about 40% shorter than one naming every piece's word and gloss, and output costs five times what input does.

Two mistakes the prompt forbids are also caught in code, because Gemini Flash kept making them on real pages:

- a number word that is a label, a rank or a pronoun: "Day one" (日 一つ), "number one", "chapter two", "the first",
  "the one", "no one", "one day" (someday), "one-on-one", or a counter like 一つ before hours or people
  (`numberTrap`). "two ideas" -> 二つ and "only one of the ideas" stay.
- a Japanese-order phrase ending in a verb with English still hanging after it: "Yesterday I 店に行きました with my mom"
  (`dangles`). It may be followed by punctuation or a word that starts a new clause ("and", "but", "so"...).

On the page a phrase or sentence is one group: each of its words is underlined and glossed on its own (a peek is logged
for that word), a sentence's 。 shows the translation of the whole sentence, and a click anywhere flips the group back
to English. A flip of a group is logged but not counted against the word clicked.

How many sentences go fully into Japanese follows the stage: about one in five at Stage 3 and 4, one in ten at Stage 2
(or Level 2 without the plugin), none at Stage 1. The rest of the density rules are unchanged.

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
| Claude Haiku 5.5, no thinking | not measured yet | about $0.001 at list price | Close to Argon with thinking on (55 s for 8 passages through `claude -p`, which can't turn it off) |
| Gemini 3.1 / 3.5 Flash-Lite, GPT-6 Luna | | | Too many sense errors and unlisted words |

Argon is not on Google's free tier and doubles its price on 2026-12-01 ($2/$10 to $4/$20 per million tokens in/out);
3.8 Flash doubles on 2027-01-01. Haiku 5.5 is $0.10/$0.50, cache reads $0.01. It needs an Anthropic API key (paste it
in the popup, or save it to `~/.japanese-weave/anthropic_key` and the native host passes it on); the request turns
thinking off, asks for low effort and a JSON schema, and marks the prompt in two parts for Anthropic's cache. When a
call fails, Haiku falls back to Gemini 3.8 Flash if there is a Gemini key.

The prompt keeps the rules first and the word lists last, learning words at the very end (their order changes as words
are shown), so consecutive calls share a prefix. Gemini caches it implicitly: Google lists 4,096 tokens as the
minimum, but repeated calls of this ~3,950-token prompt had about 2,100 tokens served from the cache.

## Batching

The prompt is about 3,300 tokens before any passage, and calls used to carry about 2 passages each, so most of the bill
was the same prompt sent again. Since 0.5.0 the background puts the cache misses of every tab in one queue and sends
them together, up to 16 passages a call (`MAX_BATCH` in `models.js`), one call at a time: passages that turn up while a
call runs (scrolling, other tabs) all go in the next one, and a passage another tab already got back is not asked for
again. The 16 test passages:

| | Flash | Argon |
|---|---|---|
| 0.4.0: full answers, two calls of 8 | $0.0132 | $0.040 |
| compact answers, two calls of 8 | $0.0108 | |
| compact answers, one call of 16 | $0.0068 | $0.025 |

With 16 passages in a call, Flash wrote about as much Japanese but dropped phrases almost entirely (0-1 per run,
against 4 with 8 per call). One line in the prompt fixed that ("make it a phrase wherever one fits... Give every
passage this care, however many come at once"): 3-6 phrases per run at 16.

## Tuning it again

`tools/prompt-lab/lab.mjs` runs passages through the extension's own pipeline and prints them woven, ⟦word or phrase⟧
and ⟪sentence⟫:

```sh
python tools/prompt-lab/words.py > /tmp/words.json      # your word state, as the extension gets it
GEMINI_KEY_FILE=~/path/to/key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs gemini-4-argon tools/prompt-lab/heldout.json
ANTHROPIC_KEY_FILE=~/.japanese-weave/anthropic_key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs claude-haiku-5-5
```

`BATCH=8` sends 8 passages a call instead of 16.

Bump `PROMPT_VERSION` in `prompt.js` whenever the prompt changes, so cached answers from the old one aren't reused.
