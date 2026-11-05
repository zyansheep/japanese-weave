# Grammar patterns and sentence difficulty

How chat replies keep their Japanese sentences within the grammar the reader knows, since 2026-11. The code is
`scripts/grammar.py` (the inventory, the scanner, a reply's sentences and their fingerprint, the reader's statuses),
`scripts/sentence_model.py` (the join, the model, intake), the weave's first line (`chatweave.py` `sentence_plan`,
`chatweave.mjs`), `check_reply.py` (the scores) and `knowledge.py` (`sentences_and_grammar`, run on every refresh).

## Why

Chat replies mix English and Japanese. A Japanese or mixed sentence ends with a 。 or ？ wrapped in an `<abbr>` whose
title is the sentence's translation; hovering it is a sentence open. An analysis of 1,166 sentences shown in T3 Code
from 2026-10-22 to 11-03 (993 of them read, 52 opened) found:

- Length in Japanese characters predicts an open best: the odds rise about 3 times per 10 characters; sentences over
  15 characters were opened 13% of the time, shorter ones 4%.
- Some grammar goes with opens, beyond length: plain forms (past, negative, the plain copula) about 5 times the rate,
  quoting and という 4.5, から 4.7, reason and contrast clauses 2.8, nominalisers 2.5; also the negative potential,
  "not yet" and stacked particles.
- Replies had doubled their Japanese sentences, to 3.9 a reply.
- A sentence skipped because it was hard looks like a sentence understood, so open rates are a lower bound.

So each Japanese sentence should be short and use grammar the reader knows, with one new pattern at a time taught on
purpose.

## The inventory

50 patterns. The first 19 are the basics, known from the start; the rest are new until intake takes them in, one at a
time, the most used in replies first (before the log has counts, in this order).

| id | pattern | what it does | example |
|---|---|---|---|
| wa | は (topic) | marks what the sentence is about | これは問題です |
| ga | が (subject) | marks who or what does or is something | 時間がかかります |
| wo | を (object) | marks what a verb acts on | ログを読みます |
| ni | に (target, time) | where something goes, when, or to whom | 三時に始めます |
| de | で (place, means) | where something happens, or with what | テストで分かります |
| e | へ (toward) | the direction of movement | 東京へ行きます |
| no | の (of) | joins two nouns | 今日の仕事 |
| desu | です (is) | the polite "is", and でした "was" | 大丈夫です |
| masu | ます (polite verb) | the polite ending of a verb | 待ちます |
| masen | ません (polite not) | the polite negative, ではありません "is not" | 分かりません |
| mashita | ました (polite past) | the polite past of a verb | 終わりました |
| mashou | ましょう (let's) | "let's", and ましょうか "shall I" | 始めましょう |
| kudasai | てください (please) | a polite request | 待ってください |
| kosoado | これ, それ, この, ここ | pointing words, and 何, どこ | それは本当です |
| mo | も (also) | "also", in place of は or が | これも大切です |
| ka | か (question) | a question at the end; "or" between nouns | 分かりますか |
| to | と (and, with) | "and" between nouns, "with" someone | 犬と猫 |
| ne-yo | ね, よ | soft endings | いいですね |
| adj-noun | adjective + noun | an i-adjective before its noun | 新しい問題 |
| teiru | ている (is doing) | an action going on, or a state | 待っています |
| dake | だけ, しか (only) | "only"; しか with a negative | 一つだけです |
| suru-verb | noun + する | a noun made a verb | 確認します |
| naru | なる (become) | X になる, adjective-く なる | 速くなります |
| relative | relative clause | a plain verb before the noun it describes | 昨日読んだ本 |
| kara-from | から (from) | after a noun | 東京から来ました |
| potential | can (できる, 〜える) | できる, or a potential form | 読めます |
| plain-neg | ない (plain not) | the plain negative | 分からない |
| cleft | X のは Y です | "what X is, is Y" | 大切なのは時間です |
| conjecture | でしょう, かもしれません | "probably", "might" | 雨かもしれません |
| kedo | けど, が (but) | "X, but Y"; のに "even though" | 高いですが、いいです |
| nara | なら (if it is) | "if it is X" | 明日なら大丈夫です |
| quote | と思う, と言う | quoting a thought or words | いいと思います |
| reason | から, ので (because) | after a clause | 遅いので、待ちます |
| plain-past | た (plain past) | the plain past | 終わった |
| nominal | の, こと | a verb made a noun | 読むのが好きです |
| te-link | て (and then) | two actions joined; also "X で、" | 読んで、書きます |
| to-when | と (when, if) | "whenever X, Y" | 押すと動きます |
| passive | れる, させる | passive and causative | 使われます |
| made | まで (until) | how far, until when | 五時まで待ちます |
| tara | たら (if, when) | "once X happens" | 終わったら、教えてください |
| da | だ (plain is) | the plain "is" | 問題だ |
| ba | ば (if) | 〜ば, and なければ | 読めば分かります |
| yori | より (than) | "Y more than X" | 昨日より速いです |
| tai | たい (want to) | "want to" | 見たいです |
| to-iu | という (called) | "a Y called X" | 「速い」という意味 |
| temo | ても (even if) | "even if" | 遅くても大丈夫です |
| ya | や (and so on) | "X and Y, among others" | 本やノート |
| neg-potential | can't | the negative of a potential | 読めません |
| mada | まだ (not yet, still) | "not yet" with a negative or alone | まだ終わっていません |
| stack | stacked particles | に + は, で + の, から + の | ここには何もありません |

`fw.py grammar` lists them with your status and fitted difficulty; `fw.py grammar known|learning|new ID` sets one.

## The scanner

A sentence comes as segments: each glossed word (an `<abbr>`'s inner text: a word, a conjugated verb whole, or a
particle the rules gloss) and the bare text between them. English runs and code count as one noun. Bare text is split
into grammar units (particles, です, ません and the like); a glossed word that is only grammar units (には) is split the
same way, and one that isn't a dictionary word is split into the fewest known pieces (読むと is 読む と, だと思います is
だ と 思います), a katakana word never. Each pattern looks at the tokens and their neighbours with regexes; where it turns
on a verb or adjective form (a relative clause, a plain past, a potential, a suru-verb) the form is walked back to its
dictionary form (`inflection.deinflect`) and checked against the dictionary's word types.

**Checked against another tagger.** The analysis's sentences were also tagged by a morphological analyser (janome).
Over the same 1,164 sentences, read from the transcripts by this code (every one's Japanese character count agrees),
the patterns found by both, by this scanner only and by the other only:

| id | both | this only | other only | | id | both | this only | other only |
|---|---|---|---|---|---|---|---|---|
| wa | 835 | 5 | 3 | | potential | 49 | 1 | 2 |
| ga | 251 | 0 | 11 | | plain-neg | 21 | 2 | 5 |
| wo | 187 | 0 | 0 | | cleft | 25 | 0 | 1 |
| ni | 227 | 2 | 20 | | conjecture | 3 | 0 | 0 |
| de | 111 | 53 | 12 | | kedo | 22 | 4 | 0 |
| e | 10 | 0 | 0 | | nara | 21 | 0 | 0 |
| no | 462 | 3 | 1 | | quote | 20 | 0 | 1 |
| desu | 517 | 0 | 0 | | reason | 28 | 1 | 0 |
| masu | 381 | 0 | 0 | | plain-past | 18 | 2 | 0 |
| masen | 161 | 0 | 0 | | nominal | 18 | 2 | 3 |
| mashita | 64 | 0 | 0 | | te-link | 15 | 19 | 1 |
| mashou | 20 | 0 | 0 | | to-when | 12 | 0 | 4 |
| kudasai | 34 | 0 | 0 | | made | 9 | 0 | 2 |
| kosoado | 208 | 11 | 9 | | tara | 10 | 0 | 0 |
| mo | 67 | 12 | 5 | | da | 6 | 0 | 3 |
| ka | 57 | 1 | 0 | | ba | 9 | 1 | 0 |
| to | 130 | 4 | 0 | | yori | 8 | 3 | 0 |
| adj-noun | 121 | 1 | 7 | | tai | 7 | 0 | 0 |
| teiru | 64 | 0 | 1 | | to-iu | 4 | 1 | 0 |
| dake | 53 | 1 | 0 | | temo | 4 | 0 | 0 |
| suru-verb | 38 | 3 | 0 | | ya | 3 | 0 | 0 |
| naru | 38 | 2 | 1 | | | | | |
| relative | 37 | 13 | 1 | | | | | |
| kara-from | 37 | 0 | 1 | | | | | |

Where they differ, it is mostly a choice of labels, not a miss: で after これ or それ ("with this") and "X で、" (the
copula's te-form, counted as te-link here); a relative clause before an English noun, which the other tagger can't see;
自分で glossed whole. The other tagger's "passive or potential" label is compared with this scanner's potential,
negative potential and passive together (the potential row); neither ね/よ nor the three patterns it has no label for
(neg-potential 10 sentences, mada 73, stack 121) can be compared. The worst misses it showed were fixed: a polite
question glossed whole (始めましょうか), a glossed word that carries its particle (読むと, なるか), suru-nouns as written,
bare verbs in older replies, and potential forms the dictionary lists as entries of their own (読める).

## What the hook sends

The collector never has a reply's text: T3 Code sends each message's annotation texts and positions. So the hook,
which already reads the local transcripts to count exposure (`fwlib.read_replies`), sends per reply a `sentences`
record: the fingerprint of its annotation texts (the inner texts of its `<abbr>`s in order, outside code, hashed),
and per Japanese or mixed sentence the index of its closing mark among them, its Japanese characters, its English
words and its pattern ids. The collector keeps the records in its ops table and computes the same fingerprint from
each T3 message's annotations; a sentence open is a hover or pin on the closing mark at that index. The first refresh
after an update reads every transcript on the machine once more for these records (`scan.json` notes the scanner's
`VERSION`), so the model starts from the replies the log already has; run it on each machine with chats (`fw.py
refresh`).

## The model

    logit P(open | read) = a + b * (Japanese characters / 10) + the sum of d_p over the patterns p not known yet

A known pattern adds nothing. Fitted on every refresh by penalized maximum likelihood (Newton's method), like the word
model: each d_p has a Gaussian prior (mean 0, sd 0.8), a and b weak ones (an open chance of 3% at no length with sd 2
in log odds; a slope of 0.7 per 10 characters with sd 1). Each sentence counts by its chance of having been read, as a
word does: 1 when opened, else the line rule down to the lowest open in the message or its paragraph's time on screen,
whichever is higher; and by its age, halving every 30 days. The intervals come from the fit's curvature.

Each press that turns English only on (below) names the messages on screen; each Japanese sentence in them counts as
an open with weight 0.3, weak evidence that it was too hard (the press doesn't say which sentence, and some were read
already). They are left out of the counts of reads and opens.

The fit is ready, and replaces the fixed price of a sentence, once it rests on 150 read sentences and 8 opens.

**On the live log (a copy of 2026-11-04, read only).** 594 of 671 T3 messages shown joined to their replies; 1,478
sentences, 984 read and 70 opened (weighted). With the basics known: a = -4.01 (sd 0.40), b = 1.08 per 10 characters
(sd 0.35), odds times 2.9 per 10 characters, as the analysis found. Known patterns alone reach a 5% open chance at 9
characters. The hardest patterns, with 90% intervals: から (from) +1.20 [+0.44, +1.96] over 26 read sentences;
という +1.17 [+0.05, +2.29] over 4; ば +0.97 [-0.04, +1.97]; だ +0.74 [-0.42, +1.91]; けど/が (but) +0.65
[-0.22, +1.52]; ない (plain not) +0.58 [-0.34, +1.50]. ている was about as easy as the basics: +0.06 [-0.72, +0.83] over
57.

## Uses

**The weave's first line** (`fw.py weave`) says how many sentences to make Japanese or mixed: the level's budget times
the sentences' share (18%), over the open chance of a typical allowed sentence (known patterns only, 70% of the length
limit) once the fit is ready, but never more than the same budget over the price gives (the count asked for before
the model, which the reader already found too many), so the smaller of the two; before the fit is ready, the price's
count as before. On the live log a typical allowed sentence's chance (3.4%) is under the price (8%), so the count stays
what it was and the sentences only get shorter and simpler; at most how many Japanese characters each may have (where
known patterns alone reach 5%, between 8 and 24; 14 until the fit is ready); the known patterns by name, plus at most
one learning pattern a sentence; and this week's learning pattern with its explanation and example, to use on purpose
once or twice. Other grammar stays out: the sentence is rephrased or stays English. `DRAFT_RULES` says the same.

**check-reply** (`fw.py check-reply --level N`) scores each Japanese or mixed sentence: its Japanese characters, its
patterns and its predicted open chance. It flags a sentence over the limit, one using a new pattern or two learning
ones, and (once the fit is ready) one predicted over 5%, and ends with the reply's expected sentence opens against the
level's budget for sentences. The brief has the writer fix a flagged sentence by splitting it, simplifying it or
leaving it English, then check again, at most twice.

## Intake

One pattern is learned at a time. It becomes known once 15 sentences with it were read since it began (weighted by
read chance) and its fitted difficulty is at most +0.3 (near zero; at 15 sentences its interval is still too wide to
judge on). With none being learned, the next comes in only while sentence opens per read sentence over the last 7 days
are under 5% (or fewer than 20 were read): the new pattern most used in the replies of the last 30 days. The change is
a `grammar` op, so the collector holds the statuses and every cache sees them. `fw.py status` shows the pattern being
learned, its evidence and the rate. On the live log's copy the first was stacked particles (に + は and the like, in 135
sentences of 30 days), with the rate at 4.4%.

## English only

T3 Code's keyboard shortcut toggles English only in chat: while it is on, every message on screen shows English (words
as their meanings, Japanese and mixed sentences as their translations) and the hook has new replies written in plain
English, as at off. It lasts an hour unless turned off. The heavier and lighter levels are set from the arrows of T3
Code's Japanese Weave panel (`POST /v1/intensity`, unchanged).

- `GET /v1/intensity?surface=chat` adds `"english": {"on", "until" (ms, 0 when off), "at" (ms of the last change, 0 if
  never)}`.
- `POST /v1/english` with `{"on": true|false, "source": "t3", "contexts": [{"view", "context"}, ...]}` (the messages on
  screen, at most 30, ids as in `/v1/interactions`; may be empty) answers `{"ok": true, "english": {"on", "until",
  "at"}}`, or `{"ok": false, "error"}` with a 4xx status. On sets until to an hour from now (a press while on starts the
  hour again); off sets it to 0. Each press is an `english` op with its contexts.
- `fw.py intensity english on|off` does the same from a terminal.

In the model: T3 messages shown during an English spell don't count (the collector drops their shown records and the
refresh leaves them out); each press turning it on counts as a lighter press in `fw.py calibrate`; and the Japanese
sentences of the messages it names are weak opens, as above.
