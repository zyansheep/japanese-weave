# Ethos

What Japanese Weave believes about learning a language, and what that means for the code. When a design choice is
close, decide it by this page.

## Meaning is refined, not switched on

Nobody learns a word in one step. A reader meets it, guesses roughly what it means from the sentence around it, meets
it again somewhere that narrows the guess, and over many meetings the meaning sharpens: first "something about
reading", then "to read", then which kinds of reading it covers and which it doesn't, then how it bends in a
sentence. Grammar is learned the same way: first a vague sense that a particle marks something, later what it marks
and when it doesn't fit.

So knowing a word is not a yes or no. It is a meaning held with some confidence, and every meeting can move it a
little closer to the real one.

## An approximate meaning beats no meaning

It follows that a fuzzy meeting is still a useful one. A Japanese verb dropped into an English sentence, glossed, read
in the dictionary sense rather than exactly, still teaches the reader that the word exists, roughly what it covers, and
what it looks like. Leaving the sentence all English teaches nothing. The only meeting that does harm is one that
teaches a *wrong* meaning with confidence, so a swap is withheld when it would mislead, never merely because it is
imprecise.

In practice:

- **Prefer showing to hiding.** A word the reader knows goes in wherever its meaning roughly fits: nouns, adjectives,
  adverbs and verbs alike, conjugated to fit the sentence where the writer can. The gloss on hover carries the exact
  sense.
- **Unsure is not wrong.** When the code is unsure which sense an English word has but the Japanese word's own gloss
  matches it, the swap stays.
- **Wrong is wrong.** A swap that changes what the sentence says (a noun standing in for a verb, a sense from another
  field, an idiom translated word for word) stays English. The list of known traps in the brief is about these, not
  about imprecision.

## Steps, not jumps

Between plain English and a full Japanese sentence there are steps, and a reader learns most on the step just past
what they can already read:

1. English with the Japanese words the reader knows woven in (the weave);
2. the same sentence built in Japanese order, with particles and endings, keeping English for the words the reader
   doesn't know yet;
3. the whole sentence in Japanese.

Each sentence carries its ladder, and the reader's dials choose the step. Taking a sentence down a step undoes its
construction back to the weave, never to bare English, so the words already earned stay in view.

## Fix, don't drop

Every rule the code checks is there to keep a meaning from going wrong. When code can see what the writer meant and
mend it (an unglossed particle, an English article inside a Japanese sentence, a plural), it mends it. It drops a
sentence only when it cannot tell what the sentence should say, because a dropped sentence is a meeting lost.

The same goes for the brief: what code can do or fix, the writer is not asked to remember. The brief keeps the
judgment calls (which sense fits, how the sentence reads), and code keeps the mechanics.

## Measure it

A change to the brief, the weave or the check is judged on real replies: `fw.py backtest` replays the drafts written so
far through the current code and reports how much Japanese each level shows and what was dropped, so a change shows
its effect before it ships.
