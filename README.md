# Japanese Weave

Learn Japanese passively from the English you already read. Japanese Weave swaps a few Japanese words into AI chat
replies and the web pages you read, at your level, and moves words from learning to known as you see them.

> The build passes 今, でも the cache is stale, だから I cleared it.
> (今 = now, "ima"; でも = but, "demo"; だから = so, "dakara")

A port of [French Weave](https://github.com/jackieoliver/french-weave) by Jackie Oliver to Japanese, with:

- a Japanese word list (1,500 words, kanji as normally written, romaji and kana for every word);
- your **Anki** cards under review moved to the front of the list (`fw.py anki`);
- the browser extension running in **Firefox** as well as Chrome;
- the chat hook working in **T3 Code**, which runs Claude Code through the Agent SDK.

## Set up

Python 3.9+ as `python3` or `python`, and Claude Code.

```sh
claude plugin marketplace add /path/to/japanese-weave      # or the git URL of your copy
claude plugin install japanese-weave@japanese-weave
```

Then run `/japanese-weave:setup` in Claude Code (it asks a few questions), or directly:

```sh
sh scripts/run scripts/fw.py setup --anki ~/.local/share/Anki2/<profile>/collection.anki2
```

Human steps (browsers don't let scripts do these):

1. **Firefox**: `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → pick
   `~/.japanese-weave/extension-firefox/manifest.json`. A temporary add-on is gone when Firefox quits; to keep it,
   sign it once with `tools/sign-firefox.sh` (free addons.mozilla.org API key, unlisted, nothing gets published) and
   install the `.xpi` from about:addons → gear → **Install Add-on From File…**. Firefox Developer Edition, Nightly
   and ESR can instead set `xpinstall.signatures.required` to false.
   **Chrome/Chromium**: `chrome://extensions` → Developer mode → **Load unpacked** → `~/.japanese-weave/extension`.
2. Click the Japanese Weave toolbar icon → **Allow all websites** (or tick **This site** on each site you want).
3. Paste a free Gemini API key ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)) in the popup, or
   pass `--gemini-key-file` to setup. For Claude Haiku (popup, Model), paste an Anthropic API key instead, or save it
   to `~/.japanese-weave/anthropic_key` (`chmod 600`).

**Browser on another machine.** The word state lives where Claude Code runs. On the machine with the browser, clone
this repo and run `python3 scripts/fw.py setup --remote <ssh-host>`: the native host there forwards the extension's
messages to `<ssh-host>` over ssh (key login, no password prompt; `BatchMode=yes`). For several machines that each
run Claude Code or a browser, use a collector instead ([Several machines](#several-machines)).

**claude.ai or ChatGPT.** Run `/japanese-weave:export` (or `--chatgpt`) and paste the block, or paste a ready-made
level from [blocks/](blocks/level-01.md).

## What it does

- **Claude Code, Codex and T3 Code**: a prompt hook gives the model your stage and word lists. The first turn of a
  chat names the words you know at a glance that are most useful in chat, with their meanings; later turns carry a
  short reminder, the learning words and only what changed, since the hook's text stays in the conversation. Most
  sentences stay English sentences, with listed words dropped in as written (no particles or conjugation below Stage
  3, suru-nouns standing in for English verbs: "let me 確認 the logs"), and a few are short Japanese sentences of
  words you know, as many as your lookup budget pays for. New words get a footer gloss with romaji. Never in code,
  files, commits or anything meant to be shared, and never in scripted
  runs (`claude -p`, Agent SDK scripts, `codex exec`). T3 Code threads are Agent SDK sessions too, so the hook checks
  for a `t3 serve` parent and weaves there.
- **Chat replies in T3 Code: draft and weave** (the default where the chat shows hover glosses and node or bun is
  installed). The brief has Claude write each reply in plain English first and pass it to `fw.py weave`, which weaves
  in Japanese with the extension's own code: every place one of your words could go, picked by your word model and the
  chat's level. Claude then rewrites the result, keeping the swaps whose sense fits and turning about as many sentences
  as the level allows into Japanese or mixed Japanese and English, each with its translation on the closing 。. Each
  such sentence is short (at most as many Japanese characters as your log says you read without opening the
  translation, 9 on 2026-11-04) and uses only grammar you know, plus at most one pattern you are learning, one at a
  time, which the weave names with an example so Claude uses it on purpose ([docs/grammar.md](docs/grammar.md)).
  Finally it runs `fw.py check-reply`, which lists what code can see is wrong (an "if" with no conditional ending,
  pronouns, する on an English verb, unglossed grammar and the like), scores each Japanese sentence (length, grammar,
  the chance you open its translation), and Claude fixes what it flags and sends the reply. In four rounds of blind-judged
  replies this beat Claude weaving as it writes on every question, with fewer errors and more Japanese grammar, in
  about half the time ([docs/chat-weave.md](docs/chat-weave.md)). At full it also brings in dictionary words you
  haven't met. `fw.py chat-style classic` goes back to the brief above; terminals and Codex always use it.
- **English only in T3 Code**: one keyboard shortcut shows every chat message on screen in English (words as their
  meanings, Japanese sentences as their translations) and has new replies written in plain English, for an hour or
  until you press it again. The arrows of T3 Code's Japanese Weave panel set the level (heavier, lighter).
- **Any website** (extension): some English on the page becomes Japanese: single words, short phrases in Japanese word
  order ("before bed" -> 寝る前), and short sentences written fully in Japanese, as many as your lookup budget pays for,
  all built only from your words plus particles and endings. A passage is English prose of at least 6 words: a
  paragraph, list item or quote (never links, buttons, menus, code or anything you type); on X a tweet; on Google
  results a snippet, the knowledge panel's description and AI answers (the overview, and People also ask once opened),
  never titles, links, chips, dates or ads; on YouTube comments, community posts and the video description, collapsed or
  opened, never titles or channel names. Hover a word for its meaning, romaji and kana, or a sentence's 。 for its
  translation; click to flip back to English. Code finds every place one of your words could go and takes the ones it
  is sure of, which show within a second of the page loading; a model on your own key settles the rest and writes the
  sentences and phrases, one call per batch of passages from every tab, and its additions follow in 20 to 30 s
  without moving what you already see: Claude Haiku 5.5 with an Anthropic key (on passages nobody tuned against, 80%
  to 88% of the places your words fit at full, about 1% of them in the wrong sense, about $0.44 per 1,000 passages),
  or Gemini 3.8 Flash or 4 Argon (popup, Model; not yet measured on this job). Without a key you get the sure swaps alone and no text leaves the browser. Each
  answer is kept, so revisiting a page, changing the level or learning a new word costs no new call except where the
  new word adds a doubtful place ([docs/swap-prompt.md](docs/swap-prompt.md), "The web pipeline"). Passages you read
  are counted per dictionary entry, counts only, for the words your reading brings in.
- **From off to full on pages** (extension): Alt+Shift+Comma and Alt+Shift+Period (or the popup's Less and More) step
  the Japanese on pages from **Off** to **Full** for 3 hours after the last press: off (−3), much lighter, lighter,
  normal, heavier, much heavier, full (+3). Off puts every woven page back in English at once, sends nothing to the
  model and logs nothing, until a heavier press or the popup's **Turn back on**. Full shows every candidate: each of
  your words, learning or known, wherever the passage says it in that sense, and every sentence your words and simple
  grammar can say whole, still checked word by word; every level picks from that same set, with no new call. Heavier
  and much heavier also keep every known word, give whole sentences a bigger share and the first choice, ask for a
  sentence's last clause where it can be said whole (a new call only for a passage with such a clause) and add
  dictionary words you don't have yet, ranked by how near they are to what you are ready to learn and within the
  level's lookup budget: only senses flagged safe, no loanwords, nothing medical, legal or financial, and never a word
  the delayed-start experiment is holding back. Normal and the lighter levels don't change. The
  toolbar badge shows the level (`−2`, `+1`, `off`, `full`) and `···` while the model works on that tab's page; the
  icon turns grey where a page gets no Japanese, and
  its tooltip says why: paused, off until a time, the site turned off, a private site, no access to the site yet, or a
  page extensions can't run on. Once a page has settled, the icon is faded where it has nothing to weave,
  and its tooltip says whether no English paragraphs were found or no Japanese fit the text; it comes back when
  Japanese turns up (more comments, the next results, another video).

All of it shares one word list on your machine, so words you see on the web count toward the chat and the other
way round.

## Commands (Claude Code)

| Command | What it does |
|---|---|
| `/japanese-weave:status` | Stage, word counts by label, the words you're learning, new words today |
| `/japanese-weave:known <word>` | Mark a word known: kanji, kana or romaji (`多分`, `たぶん`, `tabun`) |
| `/japanese-weave:learn <word>` | Put a word back into the learning set |
| `/japanese-weave:stage 1-4\|auto` | Pin your stage, or let it follow your known words |
| `/japanese-weave:challenge off\|0.5-3` | Set normal's lookup budget per 100 words (the other levels scale with it) |
| `/japanese-weave:intensity [chat\|browser] up\|down\|normal\|off\|full` | More or less Japanese, none (off) or all you can follow (full), for the next 3 hours |
| `/japanese-weave:intensity off\|normal here` | No Japanese in this chat until you turn it back on |
| `/japanese-weave:intensity english on\|off` | Chat replies in plain English for an hour (T3 Code's shortcut), or back |
| `/japanese-weave:anki <collection>` | Put your Anki review words at the front of the list |
| `/japanese-weave:pause` / `resume` | Stop or restart Japanese in chat and on websites |
| `/japanese-weave:export [--chatgpt]` | Paste-ready block for claude.ai or ChatGPT |

From a terminal: `sh ~/.japanese-weave/app/scripts/run ~/.japanese-weave/app/scripts/fw.py <command>`.

## The word list

[`data/lexicon.csv`](data/lexicon.csv): `ja, en, hint (romaji), kana, tier`, in teaching order. Built from
[JMdict](https://www.edrdg.org/jmdict/j_jmdict.html) (CC BY-SA 4.0) common words that can stand in for an English
word (nouns, suru-nouns, adverbs, connectors, adjectives), ranked by the geometric mean of their
[JPDB](https://jpdb.io) and BCCWJ frequency ranks, then reviewed word by word: wrong-sense glosses fixed, grammar
words and counters dropped, romaji fixed at morpheme boundaries (場合 baai, 実は jitsu wa). Connectors and words
technical chat uses come first; the tier says which (A: connectors and adverbs, T: technical, B: everyday, C: poor
fits), and the chat brief's default swaps are your known A words. `tools/` has the pipeline (`build_candidates.py`,
the reviews in `curation/`, `assemble_lexicon.py`).

**The dictionary.** [`data/dictionary.tsv`](data/dictionary.tsv) has every common JMdict entry (22,647) and the
4,322 others ranked as well as a typical common one, with their frequency ranks, built by the same pipeline without
the review (`tools/build_dictionary.py`). It is planned to become the vocabulary and isn't used at run time yet;
[docs/dictionary.md](docs/dictionary.md) describes it.

**Anki.** `fw.py anki <collection.anki2>` reads your collection (a temporary copy, so Anki can stay open) and puts
every word on a card in learning or review (not new, not suspended) at the front of the list, most familiar first.
Mature cards (interval of 21 days or more, `--mature-days`) are marked known; the rest become the first learning
words. Field roles come from the note type (Core 2k/6k, Kaishi, JLPT and Yomitan-mined decks name them; Front/Back
decks are read by content). Rerun it whenever you like; it never resets progress. `--deck NAME` limits it to decks
whose name contains NAME.

## How words advance

Every word has a label, and the fitted model sets it on each refresh (see [Adaptive difficulty](#adaptive-difficulty)):

- **Known**: you know it at a glance, a lookup chance under 8%, judged with a margin for doubt so that one lookup
  doesn't change it. Known words are free: they appear wherever they fit.
- **Learning** (status `active`): in use, and not known at a glance yet.
- **New** (status `queue`): not introduced yet.

A word moves between known and learning as the evidence does: one you keep reading without a lookup becomes known,
one you look up often enough is learning again. There are no exposure days, learning slots or shaky words. `known` and
`learn` set a word's label at once, and the fit takes them as strong evidence, so the next refresh keeps the label
unless your lookups say otherwise.

- **New words**: up to 5 a day come in, and only while the lookup budget goes unspent: when, over the last 48 hours,
  the web's passages or the chat's briefs (at least 5 of either) planned to spend under 80% of their budget, so there
  aren't enough learning words to spend it on. Words you looked up before they were introduced (opened in 2 different
  messages or passages within 14 days) come first, most opened first; then the words your reading nominates (below).
  Until your chats have been counted on 3 days, the list order instead. A word the model already says you know at a
  glance is labeled known instead.
- **Words from your reading**: each chat reply is counted per dictionary entry and day (`data/dictionary.tsv`, 26,969
  entries; counts only, never text): its English through the code matcher, which marks every place an entry could
  go, and its Japanese through the `<abbr>` tags where it has them, else the listed words. An English place counts
  only where a sense check can trust it: never closed-class words ("about", "one", "so"), and only the most frequent
  option the sense flags (`data/senses.tsv`) call safe for that English (without that file, the most frequent entry
  there when that English is its main meaning). A word is eligible once it came up on 3 different days in 30, can
  stand in for English, isn't a number and isn't in use; the eligible words come in by the days they came up (most
  first), then the predicted chance you'd look one up (nearest 25% first), then general frequency. A dictionary word
  the list doesn't have joins the word state when it comes in. The first scan after an update counts the last 30 days
  of chats again.
- **The delayed-start experiment** (PLAN.md section 7) tests whether being shown a word teaches it. Each new word is
  drawn into one of two arms: held back, appearing nowhere for 3 weeks, or shown like any learning word for 2 weeks
  and then paused for 1. At week 3 both are probed: the first web passage that can show the word shows it outside the
  budget, once a day until one is read, and the chat brief asks for it once; the first of its showings you read is
  the outcome, looked up or not. Then it is an ordinary learning word. The draw comes from a seed and the word alone,
  and every web plan and chat brief record says the arms of the words it offers. `fw.py status` counts the held-back
  words without naming them; `fw.py experiment` shows the arms, the probes, the outcome so far, and when each arm will
  have 80 words probed (`--power`: the simulated reader's power at your rate of showings). The 25 words the fixed rules
  name (でも, だから and the like) come in outside it.
- **Stage** follows your known words: 1 to start, 2 at 40 known, 3 at 150, 4 at 400.

**The switch from day counts.** Until then labels came from rules inherited from French Weave: 20 learning slots, 3
exposure days to graduate, shaky words after two peek days, and evidence moves with a challenge set. `fw.py relabel
--dry-run` lists the words whose labels the model would change, grouped by kind and explained, and changes nothing.
The first refresh with the model's labels switches: each word keeps its old status, days and peeks under `legacy` for
30 days. To undo it in that time, run `fw.py relabel --restore` (where the state lives, or queued for the collector):
it puts the old statuses and fields back (a word taken in since stays learning) and stops refreshes from labeling
words; then check out the code from before the switch.

In chat, how much Japanese a reply gets comes from your words, not a share of the reply: every word you know at a
glance goes in wherever the English has its meaning, and learning words and short です/ます sentences of known words go
in as often as your intensity's lookup budget allows, at any stage (see [Adaptive difficulty](#adaptive-difficulty)).
The brief names the words you know at a glance on a chat's first turn: a dozen connectors and adverbs as default
swaps, written in Japanese every time, then others with their meanings, the most useful in chat first: the ones that
keep coming up in your chats, then the most common, then your Anki words (numbers and katakana words stay English).
That list stops at about 250 to 290 words to stay inside Claude Code's 10,000-character limit on hook text; the words
it leaves out are still known, just not named. Later turns repeat the default swaps and name only the words that
became known or stopped being known. The whole brief comes back after the chat is compacted (Claude Code and Codex
mark it in the transcript) and every 20 turns. Below Stage 3 every other sentence keeps English grammar, with listed
words never conjugated and no particles around them.

Each brief also offers up to 5 **topical words**: dictionary entries the code matcher finds in the chat's last three
turns (your prompts and Claude's replies, the prompt being sent included), the most frequent entry at each place when
the English there is its main meaning (not only the safe ones: Claude checks the sense as it places them). Only words
worth offering: ones that can stand in for English, not numbers or katakana, not in use, never one the delayed-start
experiment holds back, and not ones you know at a glance; the ones your reading keeps repeating first, then common
ones. Each comes with its odds, like a learning word, and counts toward the learning words' amount, at most half of
it. The feedback line on the next reply counts them as their own class, not as Japanese outside the lists, and the
brief record lists them.

The claude.ai and ChatGPT blocks (`/japanese-weave:export`) can't see your lookups, so they still ask for a share
of the words: about 6% at Stage 1, 12% at 2, 25% at 3 and 40% at 4. They describe your known words as the most
common words up to a rank in the base list, then name the known words past it, the more common first, as many as fit
(ChatGPT's fields hold 1,500 characters).

Japanese has no spaces, so chat exposure is counted with script-aware matching: a kanji word can't sit inside a
longer kanji run (今 isn't counted in 今日), a kana word can't sit inside a kana run, and readings of three or more
kana count for their word. With a collector, each machine counts its own transcripts and sends the counts (never the
text) with its chat exposure; the collector keeps the counts by day where intake runs, and the caches get how many
days each entry came up on. The index the matcher reads (`reading-index.sqlite` in the state dir, about 17 MB) is
built in the background on first use, in about 5 seconds.

`JAPANESE_WEAVE_OFF=1` turns the hook off anywhere; `JAPANESE_WEAVE_FORCE=1` weaves in a script. Health,
medication, legal, money, safety, emergency and driving topics get no Japanese at all.

## Gloss interaction log

Every time you open a gloss (hover a word in the browser, or hover or click an `<abbr>` word in T3 Code chats) and
every time you flip a word back to English, Japanese Weave records which word it was, when, how long the gloss
stayed open, and where the other glossed words of the same paragraph or message sat, by position only. A peek shows
you were reading that spot, so the words around it that you didn't open are the evidence that you knew them; this
log is the data for estimating that.

It also keeps evidence of where you were reading, in paragraphs (browser) and messages (T3 Code) that have glossed
words: clicks (and whether one hit a link), text selections (how many characters and which glossed words, never the
text), mouse paths over T3 messages, and how long each paragraph stayed on screen. On-screen time only counts while
you could have been reading: the page visible and its window focused, no sound or video playing on it, and some input
(mouse, keys, scrolling) in the last 30 seconds. The rest of a paragraph's time on screen is kept apart as away, media
or idle, so a page left open or a video doesn't look like reading. `fw.py log` shows totals and the latest entries, and
the extension's popup shows how many peeks have been collected.

Review cards (T3 Code and the phone) are kept there too: each card served (the word, whether it was a learning, probe or
made-up word) and, once shown, how long until the reveal, the grade you gave it (Again, Hard, Good or Easy) and when,
and how long it stayed on screen. A word's graded cards are its review schedule.

By default it goes to `interactions.sqlite` in the state folder.

## Several machines

To learn from one word list on several machines (Claude Code on two computers, a browser on a third), run the
collector on an always-on one, over a private network such as Tailscale. It becomes the one home of the word state
and of the gloss log; every other machine keeps a cache:

```sh
fw.py setup                                           # on the always-on machine: its own state, as usual
fw.py collector install --listen 100.64.0.4:7853      # there too: a systemd user service in the same state folder
fw.py setup --collector http://functor:7853           # on each other machine (off: back to its own state)
```

Every change (`known`, `learn`, the dial, a browser peek, an Anki sync, the words used in chat replies) goes to the
collector, which applies it and keeps a record of it; the other machine then refreshes its cache. The chat hook
never waits on the network: it reads the cache and syncs in the background every couple of minutes. If the
collector can't be reached, changes wait on that machine (and show there at once) until the next sync. The always-on
machine's own plugin uses the shared state folder directly, without `--collector`.

If the machine you point at a collector already has a word list and the collector has none yet, setup stops and
says which files to copy over (state.json and its neighbours) to move that list there. Joining a collector that has
one keeps this machine's old list in `before-collector/` in the state folder.

T3 Code reports chat popovers when its server has `T3CODE_ANNOTATION_EVENTS_URL` set to the collector's
`/v1/interactions` URL; that needs a T3 Code build with the abbreviation popover patches. Its English-only shortcut
goes to the same collector (`/v1/english`), and its panel's arrows to `/v1/intensity`. Its review cards come from the
same collector (`/v1/review/next`), and what happened to each goes with the popover events.

## Adaptive difficulty

From the log, Japanese Weave estimates for every word how likely you are to look it up (open its gloss): one fit of the
whole log, redone on every refresh, which starts each word from its Anki interval and how it came into use (taken in
to learn, marked known at setup or by Anki, or neither; never its label, which the fit itself sets) and moves it by
what you opened and didn't. Each intensity level is a lookup budget, the lookups per 100 words you put up with (0.5 at normal):
words you know at a glance (under an 8% chance, judged with a margin for doubt so one lookup doesn't change it) are free
and appear wherever they fit, and the budget goes to the words closest to a 25% chance, at most one per sentence, on web
pages and in chat. Once your log shows that a word's chance falls right after you read it and climbs back over a week,
the fit follows that too: known at a glance is judged on the chance a week on, a lookup costs the chance now, and a word
read too recently waits. A whole sentence in Japanese costs too: how often a sentence's translation gets opened, fitted
from your log (0.08 until it has enough; in chat, the open chance of a short sentence of grammar you know, from the
sentence model in [docs/grammar.md](docs/grammar.md), once it has enough), and it takes its sentence's one place; sentences may take up to 18% of the
budget and learning words the rest, and what one can't use goes to the other. A challenge (`fw.py challenge 1.5`, the
command above, or the popup) is normal's budget. The same fit labels every word
([How words advance](#how-words-advance)). Why it works this way and what comes next: [PLAN.md](PLAN.md) and
[NEXT_STEPS.md](NEXT_STEPS.md).

Every passage shown on the web counts as evidence, weighted by the chance you read it, from how long it was on
screen while you were reading.

**For now: heavier or lighter.** Alt+Shift+Period gives more Japanese and Alt+Shift+Comma less, for the next 3 hours, in
the browser (a toast on the page and the toolbar badge show the level); in chat, the arrows of T3 Code's Japanese Weave
panel do the same (T3 Code's shortcut toggles English only, below); also the popup's Less/More buttons and
`fw.py intensity [chat|browser] up|down|normal`. Each press is
logged, and half of the web passages keep only a random share (20% to 100%) of their words you know at a glance, so
`fw.py calibrate` can tell what those words cost you and whether the default amount is right: reading speed and opens by
how much Japanese a passage had, and how often you turned it down or up. It also checks the model, per surface and class
of word (costly, free, whole sentences, words off the list): the lookups per 100 words read it expected against those
you made, with a 90% interval from resampling days, and the budget in the same unit.

**In chat, a level is a lookup budget**: how many lookups per 100 words you put up with, spent only on words you
don't know at a glance yet: 0.15 much lighter, 0.3 lighter, 0.5 normal, 0.8 heavier, 1.2 much heavier (a challenge,
when set, is normal's budget and scales the others). Words you know at a glance (under an 8% chance of a lookup)
cost nothing and go in wherever they fit at every level but off, so lighter and heavier change the learning words
and whole sentences only: the brief asks for as many per 100 words as spend about the budget (at normal about 1
sentence per 100 words, the one in five it asked for at Stage 3 before), at most one learning word in a sentence (two
at heavier and much heavier), and the line on the last reply says whether it went over or under, and with which.

**The ends: off and full.** Three presses lighter from normal turn Japanese off, and three heavier reach full, each
for 3 hours like the levels between (a press past either end stays there and restarts the clock);
`fw.py intensity [chat|browser] off|full` or `/japanese-weave:intensity off|full` sets them directly. In chat, off
means the hook sends only a one-line note to reply in plain English, until the time runs out, you press the panel's
up arrow, or you say "japanese back on" in any chat.
In chat, full is the normal brief with the budget lifted: every sentence you could follow in plain Japanese (your
known and learning words plus common grammar) is written in Japanese, and the sentences that must stay English
(steps, code, commands, exact technical detail) swap in every known or learning word, with no cap per sentence. A
chat turned off with `off here` stays off at any level.
`fw.py calibrate` counts a press into off as lighter and into full as heavier.

**English only, for an hour (T3 Code).** T3 Code's keyboard shortcut turns chat to English only: every message on
screen shows English at once, and the hook has new replies written in plain English, as at off, until the hour runs
out or you press it again (or `fw.py intensity english on|off`). Messages shown meanwhile don't count as reading, and
each time you turn it on counts as a lighter press in `fw.py calibrate`; the Japanese sentences that were on screen
count as weak evidence that they were too hard (docs/grammar.md).

**Just this chat: off.** Ask in the chat ("no japanese for this thread please", "english only from now on", "write no
Japanese in what you say to me") or run `/japanese-weave:intensity off here`, and that chat gets no Japanese from
that reply on, with no time limit, while other chats carry on as usual; "japanese back on" or `... normal here` brings
it back. Only a short request at the start or end of a prompt counts, never one about files, code, commits or the
plugin itself ("no Japanese in commit messages" leaves the chat alone). This works in Codex too, by asking. The
switch is kept per session id, recorded as a `thread` op (so the collector has it, and a change made while it is out
of reach still holds here at once), and forgotten 60 days after its last change. `fw.py calibrate` counts each chat
turned off as a lighter press.

**Grammar in chat.** Each Japanese sentence in a chat reply is held to the grammar you know (50 patterns, 19 basics
known from the start) plus at most one you are learning, and to a length your log supports: the sentence model fits
the chance you open a sentence's translation from its length in Japanese characters and the difficulty of each pattern
you don't know yet. One pattern is learned at a time: it becomes known after 15 sentences with it read without extra
opens, and the next, the one replies use most, comes in only while you open under 5% of the sentences you read.
`fw.py status` shows the pattern being learned, `fw.py grammar` all of them, `fw.py knowledge` the fit
([docs/grammar.md](docs/grammar.md)).

`fw.py knowledge` shows normal's budget, the fitted chat offset and reading model, the sentence model, and the words
with the most evidence; `fw.py why <word>` shows one word's estimate and every observation behind it; `fw.py evaluate` scores the
model on your log day by day, each day predicted from the days before, against the online model it replaced and a
constant guess. The design, with its sources:
[docs/adaptive-difficulty.md](docs/adaptive-difficulty.md).

**Review cards, scheduled like Anki (T3 Code and the phone).** T3 Code's review panel (in the sidebar) and the phone's
Review screen show one Japanese word alone. Reveal its reading and meaning, then grade it with Anki's four buttons:
Again (you didn't recall it), Hard, Good or Easy; above each, when the card would come back (`<1m`, `10m`, `3d`,
`1.2mo`). The schedule is FSRS-6 with its default parameters, applied the way Anki applies it (`scripts/srs.py`): a new
word goes through a 1-minute and a 10-minute step, then comes back in days, each review spacing it further while you
recall it, aiming at a 90% chance of recall when it comes back; a word you forget relearns in 10 minutes. The cards come
from the collector (`POST /v1/review/next`) in Anki's order: words in their learning steps that are due, then words due
for review today (a day starts at 4am), then up to 20 new words a day, the words you are learning in today's order
(first those behind on showings in their first two weeks, then those the model is least sure of), and last, when
nothing else is due, a learning step up to 20 minutes early. The panel shows Anki's three counts (new left today,
learning, review due); at 0, 0, 0 you are done for the day, and "Review more" brings in more new words. About 1 card
in 10 is a probe word (below) and 1 in 20 a made-up word, whose meaning says so: they look like new cards, aren't
counted, and grading them schedules nothing. A card you close without grading isn't scheduled; it comes back after the
other cards due. Never a word the delayed-start experiment is holding back; a word you mark known (`fw.py known`)
leaves the deck, while one the model labels known keeps its schedule. A review counts as a showing of its word: right
after it the word's lookup chance is near the floor, like any read.

`fw.py review` shows today's counts, the cards graded today and in the last 30 days, the share of your review cards
you recalled (not Again) against the 90% aimed for, and how many graded reviews FSRS has: its parameters can be fitted
to you once there are on the order of 1,000, and `fw.py review revlog` writes them in the FSRS optimizer's format.

One caveat. FSRS assumes your reviews are the only times you meet a word, but here you also meet your words in chat and
on web pages, glossed, whether you open the gloss or not. This first version feeds FSRS your graded reviews alone, so
its intervals come out conservative: more reviews than strictly needed. A gloss you open while reading is a likely
future "Again" signal, to be checked against your reviews once there is data.

The fit reads a grade as an answer: Again as not known at recall, Hard, Good or Easy as known, at the word's knowledge
with an offset of its own, as it reads a probe's answers. A card without a grade (from a client before grades, or one
you closed) is read from its reveal time: one of two kinds, a word known at recall (quick) or not (slow, or a
give-up), each with its own spread of times and its own chance of moving on without a reveal, shared by all words and
held by a prior so a handful of cards can't swing them. Made-up words are never known, so their times are what "not
known" looks like, give-ups included. If you give up on words you don't know as fast as you reveal words you do, the
two kinds overlap and every such card counts for little; `fw.py knowledge` says how well they separate (how much of a
yes/no answer one card is worth), and so does `fw.py status` for the probe.

**The probe, in the review stream.** The probe and made-up cards of the last 30 days measure the goal directly: the
share of the words in your reading you would read in Japanese without a lookup, each word weighted by how often it
comes up (by general frequency, among the 10,000 most frequent words, until your reading is counted). The probe words
are drawn by frequency band, a word not asked again within 90 days; per band, the share known is the weight of the
"known" kind in its cards' reveal times, fitted without the model's estimates, so it stays the check on the model, whose
own number moves when you get used to the glosses. The made-up words correct for words that only look known, and the
90% interval widens when the two kinds overlap. `fw.py status` shows the estimate and how many cards it rests on;
`fw.py probe --history` lists the probes taken before the review stream and the estimate now. The monthly 3-minute
probe and its command are gone; `fw.py probe` in a terminal still takes one (one word at a time: y, n, s to skip, b
back, q quit). The design is in `scripts/probe.py` and `scripts/review.py`; `tests/test_review.py` checks the estimate on
simulated readers.

## Privacy

Everything stays on your machine in `~/.japanese-weave` (override with `JAPANESE_WEAVE_HOME`). The hook reads your
local Claude Code and Codex transcripts to count exposure and words from your reading, and sends only counts (to your
collector, if you set one), never the text: for each reply's Japanese sentences, a hash of its glossed words and per
sentence its length, English word count and grammar pattern ids. The extension sends
readable page text from sites you allow to the model you chose, Google's Gemini API or Anthropic's API, with your key
(free-tier Gemini keys allow Google to train on prompts); without a key no text leaves the browser. Of what you read on
those sites it sends the plugin counts per dictionary entry and a hash of each passage, never the text. Private sites start off even when every site is on: webmail and messaging, AI chat, password
managers, banking and payments, health portals, government, and local or intranet addresses (subdomains included;
the popup can turn any of them on). It talks to the plugin through a native messaging host that only this extension
may call.

## Develop

```sh
python3 -m unittest discover -s tests            # hook, rules, exports, native host, setup, Anki, Codex, yardsticks
python3 tools/sim/run.py all                     # the simulated reader's experiments (about a minute)
python3 scripts/make_blocks.py                   # regenerate blocks/ and extension/src/starter.js
cd extension && bun install && bun test           # extension (happy-dom)
sh tools/make-icons.sh                            # redraw the extension's icons from tools/icons/icon.svg
python3 tests/model_eval.py --out /tmp/jw-eval    # live check via claude -p
```

## License

MIT (French Weave © Jackie Oliver). The word list and the dictionary (`data/lexicon.csv`, `data/dictionary.tsv`)
include data derived from JMdict, © the Electronic Dictionary Research and Development Group, and are shared under its
license, CC BY-SA 4.0.
