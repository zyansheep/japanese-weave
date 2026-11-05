# Chat replies: draft and weave

How chat replies in T3 Code get their Japanese since 2026-10-22, and the experiment that chose it. The code is
`scripts/chatweave.py` and `scripts/chatweave.mjs` (the weave), `scripts/check_reply.py` (the check) and
`weave.compose_draft` (the brief). Since 2026-11 the Japanese sentences are also held to the grammar the reader knows
and to a length the log supports ([grammar.md](grammar.md)).

## The steps

1. Claude writes the reply in plain English and passes it to `fw.py weave --level N` on stdin.
2. The weave runs the extension's pipeline on it, with no model call (`chatweave.mjs`, under node or bun):
   - `matcher.js` finds every place one of the words in use could go;
   - `senses.js` says which options are flagged safe;
   - `verify.js` `candidatesOf` and `levels.js` `planSwaps` pick them by the word model and the level's lookup budget.

   Words known at a glance go in line by line. Learning words come from one budget for the whole reply, at most the
   level's number a sentence. A span the web would ask its model about is taken at its first option and marked
   `{{English→Japanese}}`. The first line says how many sentences to make Japanese or mixed: the level's budget times
   the 18% sentence share, divided by the open chance of a typical allowed sentence once the sentence model is ready
   (else by the price of a sentence). It also gives the most Japanese characters a sentence may have, the grammar
   patterns it may use (the known ones, plus at most one learning pattern), and this week's learning pattern with its
   explanation and example, to use on purpose once or twice (`chatweave.sentence_plan`). At full (level 3) there is no
   budget, and every dictionary entry with a sense flagged safe joins the pool, priced by its fitted prior; the length
   and grammar limits still hold.
3. Claude rewrites the output. It keeps the swaps whose sense fits, and in sentences that stay English it keeps only
   nouns and adjectives right before a noun. A learning word that fits is kept even when it isn't one of those: its
   sentence goes Japanese or mixed instead (the rewrite kept 37% of learning words before this rule). It turns the sentences that carry the content into Japanese or mixed
   sentences, each with its translation on the closing mark, each within the first line's length and grammar: a
   sentence that needs more is split, rephrased or kept English. The rules and the list of known sense traps are
   `weave.DRAFT_RULES`.
4. Claude passes the reply to `fw.py check-reply --level N`, which lists what code can see is wrong and scores each
   Japanese or mixed sentence (its Japanese characters, its patterns, its predicted open chance), flagging one too
   long, using grammar not allowed or too likely to be opened, and ends with the expected sentence opens against the
   level's budget. Claude fixes what it lists by splitting, simplifying or keeping sentences English, checks again (at
   most twice in all) and sends the reply.

The full steps go out on a chat's first turn and every 20 turns or after a compaction; other turns carry a short
digest. While English only is on (T3 Code's shortcut, for an hour) or chat intensity is off, the hook sends a one-line
note to reply in plain English instead, and `fw.py weave` refuses. Draft and weave needs `<abbr>` glosses (T3 Code with `fw.py glosses abbr`) and node or bun. It is off in
Codex and terminals, and with `fw.py chat-style classic`, which bring back the brief in which Claude weaves as it
writes. The calibration line and topical words belong to the classic brief: in draft style the weave sets the
amounts. Brief records carry `mode: draft`.

## The experiment

Five prompts (a Bloom filter, a RefCell panic, a Kyoto weekend, TCP or UDP for a game, keeping a side project going)
were answered by Opus 5.5 at extra-high effort. Each run used a copy of the word state, under a stand-in for T3 Code
so the hook gave its real brief. Each round was judged blind by a separate Opus agent, which saw the replies shuffled
and unlabelled.

| Round | Compared | Result |
|---|---|---|
| 1 | Today's brief (A) against a draft woven by code and re-read, at the normal budget (B) | A on all five: B's tool offered 50 to 90 unsure swaps a reply, and fixed English left verbs in "-ing" slots |
| 2 | A, A with new rules (A2), and B with every candidate and freedom to rewrite into Japanese order (B2) | B2 > A2 > A1 on all five: the rewritten sentences teach grammar; A2 halved A's errors but also its Japanese |
| 3 | B2 against B2 with the judge's fixes (B3), at full and at the normal budget | B3 first on three, B3 at normal first on two, B2 last on all five; the word pool mattered less than the rewriting |
| 4 | B3 plus a code check and nouns-only English sentences, at every level on one question | The judge recovered the level order blind. +2 was best for learning; full cost more reading than it taught. Problems per word were a quarter of B3's at the same density |
| 5 | Normal and +2 with stricter rules (no する on English verbs, glossed particles) against round 4 | +2 cleaner (3 problems in 17 glossed words against 9 and 10), normal a draw |

What the judges kept finding, and what now handles it:
- **Wrong senses on basic words:** the trap list in `DRAFT_RULES`.
- **Japanese words in English grammar slots:** nouns only in English sentences, checked with the dictionary's word
  types.
- **Words that follow their noun used as English prepositions, pronouns, "if" with no conditional ending, and
  English verbs with する:** all in `check_reply.py`.

All four ways of writing replies were faster than Claude weaving as it writes at the same effort: about 110 to 150 s a
reply against 220 to 250 s.

The user's verdict on the levels: normal is right for everyday chats, and much heavier (+2) is for studying the
words. Normal leaves part of its budget unspent on purpose.
