# How the extension picks its Japanese

The extension sends readable passages to Gemini with one prompt (`extension/src/prompt.js`) and paints what comes
back. Since 0.4.0 the answer can hold three kinds of swap:

| Kind | What it replaces | Example |
|---|---|---|
| word | one English word or fixed expression, in English word order | "a 新しい version", "Honestly I 思う this is" |
| phrase | a short phrase, rewritten in Japanese word order with particles | "Even ten minutes 寝る前 makes a difference" |
| sentence | a whole short sentence, written fully in Japanese | "昨日、母と一緒に店に行きました。 We bought 魚 and some お茶." |

Every swap comes back split into pieces, each one either a listed word (or a conjugated form of one) or grammar from a
fixed set: particles, です/だ, ます forms, ない/た/て/ている/たい endings, こと and の. `validateSwaps` in
`extension/src/levels.js` checks every piece and drops the whole swap if any piece is an unlisted word, if the pieces
don't spell the Japanese, or if a sentence doesn't end like one. So a sentence only ever uses words from your list,
and a model slip costs a swap, never a wrong word on the page.

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

Same prompt, same passages (17 + 8), low thinking:

| Model | Time for 8 passages | Cost for 8 passages | Quality |
|---|---|---|---|
| Gemini 4 Argon | 12-15 s | about $0.02 | No sense errors seen in the last rounds; writes natural full sentences ("小さい少年が窓を開けて、山を見ました。") |
| Gemini 3.8 Flash | 6-8 s | about $0.007 | Mostly right with the new rules, but leaves Japanese verb phrases inside English sentences ("The little boy 窓を開けた and looked at the 山") and misses more sentences |
| Gemini 3.8 Flash, medium thinking | 30-80 s | about $0.03 (thinking bills as output) | Careful but too slow for pages |

Argon is not on Google's free tier and doubles its price on 2026-12-01 ($2/$10 to $4/$20 per million tokens in/out).
The prompt keeps the rules first and the word lists last, learning words at the very end (their order changes as words
are shown), so consecutive calls share a prefix that Gemini caches implicitly (about half the prompt in testing).

## Tuning it again

`tools/prompt-lab/lab.mjs` runs passages through the extension's own pipeline and prints them woven, ⟦word or phrase⟧
and ⟪sentence⟫:

```sh
python tools/prompt-lab/words.py > /tmp/words.json      # your word state, as the extension gets it
GEMINI_KEY_FILE=~/path/to/key WORDS=/tmp/words.json bun tools/prompt-lab/lab.mjs gemini-4-argon tools/prompt-lab/heldout.json
```

Bump `PROMPT_VERSION` in `prompt.js` whenever the prompt changes, so cached answers from the old one aren't reused.
