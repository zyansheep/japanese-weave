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

- **Claude Code, Codex and T3 Code**: a prompt hook gives the model your stage and word lists every turn. Every
  sentence stays an English sentence; listed words drop in as written, with no particles or conjugation below
  Stage 3, suru-nouns standing in for English verbs ("let me 確認 the logs"). New words get a footer gloss with
  romaji, and every third reply opens with a recall cue. Never in code, files, commits or anything meant to be
  shared, and never in scripted runs (`claude -p`, Agent SDK scripts, `codex exec`). T3 Code threads are Agent SDK
  sessions too, so the hook checks for a `t3 serve` parent and weaves there.
- **Any website** (extension): some English on the page becomes Japanese: single words, short phrases in Japanese
  word order ("before bed" -> 寝る前), and from Stage 2 a few short sentences written fully in Japanese, all built
  only from your words plus particles and endings. A passage is English prose of at least 6 words: a paragraph, list
  item or quote (never links, buttons, menus, code or anything you type); on X a tweet; on Google results a snippet,
  the knowledge panel's description and AI answers (the overview, and People also ask once opened), never titles,
  links, chips, dates or ads; on YouTube comments, community posts and the video description, collapsed or opened,
  never titles or channel names. Hover a word for its meaning, romaji and kana, or a sentence's 。
  for its translation; click to flip back to English. Swaps are chosen by a model on your own key: Gemini 3.8 Flash by
  default, Gemini 4 Argon (popup, Model) for Japanese close to Claude's at about three times the cost (paid keys
  only), or Claude Haiku 5.5 with an Anthropic key: light thinking, near Argon's Japanese at Flash's price, about 20 s
  for a page's first passages. Passages from every tab share calls, up to 16 a call
  ([docs/swap-prompt.md](docs/swap-prompt.md)).
- **From off to full on pages** (extension): Alt+Shift+Comma and Alt+Shift+Period (or the popup's Less and More) step
  the Japanese on pages from **Off** to **Full** for 3 hours after the last press: off (−3), much lighter, lighter,
  normal, heavier, much heavier, full (+3). Off puts every woven page back in English at once, sends nothing to the
  model and logs nothing, until a heavier press or the popup's **Turn back on**. Full asks the model for every listed
  word, learning or known, that a passage says in that sense, and every sentence your words and simple grammar can say
  whole, and shows all of it, still checked word by word; heavier and much heavier show part of that same answer. The
  toolbar badge shows the level (`−2`, `+1`, `off`, `full`) and `···` while the model works on that tab's page; the
  icon turns grey where a page gets no Japanese, and
  its tooltip says why: paused, off until a time, the site turned off, a private site, no access to the site yet, no
  key, or a page extensions can't run on. Once a page has settled, the icon is faded where it has nothing to weave,
  and its tooltip says whether no English paragraphs were found or no Japanese fit the text; it comes back when
  Japanese turns up (more comments, the next results, another video).

All of it shares one word list on your machine, so words you see on the web count toward the chat and the other
way round.

## Commands (Claude Code)

| Command | What it does |
|---|---|
| `/japanese-weave:status` | Stage, word counts, the words you're learning |
| `/japanese-weave:known <word>` | Mark a word known: kanji, kana or romaji (`多分`, `たぶん`, `tabun`) |
| `/japanese-weave:learn <word>` | Put a word back into the learning set |
| `/japanese-weave:stage 1-4\|auto` | Pin your stage, or let it follow your known words |
| `/japanese-weave:challenge off\|0.5-3` | Aim for this many glosses opened per 100 words on web pages |
| `/japanese-weave:intensity [chat\|browser] up\|down\|normal\|off\|full` | More or less Japanese, none (off) or all you can follow (full), for the next 3 hours |
| `/japanese-weave:intensity off\|normal here` | No Japanese in this chat until you turn it back on |
| `/japanese-weave:anki <collection>` | Put your Anki review words at the front of the list |
| `/japanese-weave:pause` / `resume` | Stop or restart Japanese in chat and on websites |
| `/japanese-weave:export [--chatgpt]` | Paste-ready block for claude.ai or ChatGPT |

From a terminal: `sh ~/.japanese-weave/app/scripts/run ~/.japanese-weave/app/scripts/fw.py <command>`.

## The word list

[`data/lexicon.csv`](data/lexicon.csv): `ja, en, hint (romaji), kana`, in teaching order. Built from
[JMdict](https://www.edrdg.org/jmdict/j_jmdict.html) (CC BY-SA 4.0) common words that can stand in for an English
word (nouns, suru-nouns, adverbs, connectors, adjectives), ranked by the geometric mean of their
[JPDB](https://jpdb.io) and BCCWJ frequency ranks, then reviewed word by word: wrong-sense glosses fixed, grammar
words and counters dropped, romaji fixed at morpheme boundaries (場合 baai, 実は jitsu wa). Connectors and words
technical chat uses come first. `tools/` has the pipeline (`build_candidates.py`, the reviews in `curation/`,
`assemble_lexicon.py`).

**Anki.** `fw.py anki <collection.anki2>` reads your collection (a temporary copy, so Anki can stay open) and puts
every word on a card in learning or review (not new, not suspended) at the front of the list, most familiar first.
Mature cards (interval of 21 days or more, `--mature-days`) are marked known; the rest become the first learning
words. Field roles come from the note type (Core 2k/6k, Kaishi, JLPT and Yomitan-mined decks name them; Front/Back
decks are read by content). Rerun it whenever you like; it never resets progress. `--deck NAME` limits it to decks
whose name contains NAME.

## How words advance

- **Exposure day**: a day you saw the word in a chat reply, or on a web page.
- **Learning (active)**: up to 20 learning slots, shared with shaky words; at most 5 new words a day. Words you
  looked up before they were introduced (opened in 2 different messages or passages within 14 days) come in first,
  most opened first; then the list order. A learning or shaky word the gloss log says you know at a glance (under an
  8% chance of a lookup) keeps its status but gives its slot to a new word.
- **Learning → known**: 3 exposure days, with no flip, `learn` or peek on the last 3.
- **`learn` → shaky**, and so is a known word you peek at or flip on 2 different days within a week (one flip is
  often a test click); a flip on a learning word restarts its count. **Shaky → known** after 7 days with no flip or
  peek. With adaptive difficulty on, evidence from the gloss log can also move words (see
  [docs/adaptive-difficulty.md](docs/adaptive-difficulty.md)).
- **Stage** follows your known words: 1 to start, 2 at 40 known, 3 at 150, 4 at 400.

In chat, how much Japanese a reply gets comes from your words, not a share of the reply: every word you know at a
glance goes in wherever the English has its meaning, and learning words go in as often as your intensity's lookup
budget allows (see [Adaptive difficulty](#adaptive-difficulty)). The stage sets the shape:

| Stage | Shape of a chat reply |
|---|---|
| 1, 2 | English sentences with swapped words |
| 3 | plus one short です/ます sentence in five, made of known words |
| 4 | plus two in five |

The claude.ai and ChatGPT blocks (`/japanese-weave:export`) can't see your lookups, so they still ask for a share
of the words: about 6% at Stage 1, 12% at 2, 25% at 3 and 40% at 4.

Japanese has no spaces, so chat exposure is counted with script-aware matching: a kanji word can't sit inside a
longer kanji run (今 isn't counted in 今日), a kana word can't sit inside a kana run, and readings of three or more
kana count for their word.

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
`/v1/interactions` URL; that needs a T3 Code build with the abbreviation popover patches.

## Adaptive difficulty

From the log, Japanese Weave estimates for every word how likely you are to look it up (open its gloss). Each
intensity level is a lookup budget, the lookups per 100 words you put up with (0.5 at normal): words you know at a
glance (under an 8% chance) are free and appear wherever they fit, and the budget goes to the words closest to a 25%
chance, at most one per sentence, on web pages and in chat. A challenge (`fw.py challenge 1.5`, the command above,
or the popup) replaces normal's budget, and while it is set a daily controller nudges the web budget until your real
rate matches it. Strong evidence also moves words between learning and known, a few a day. Why it works this way
and what comes next: [docs/plan.md](docs/plan.md).

Every passage shown on the web counts as evidence, weighted by the chance you read it, from how long it was on
screen while you were reading.

**For now: heavier or lighter.** Alt+Shift+Period gives more Japanese and Alt+Shift+Comma less, for the next 3 hours,
in the browser (a toast on the page and the toolbar badge show the level) and in a T3 Code build with the shortcut
(for chat replies); also the popup's Less/More buttons and `fw.py intensity [chat|browser] up|down|normal`. Each
press is logged, and half of the web passages are thinned at random, so `fw.py calibrate` can tell whether the
default amount is right: reading speed and opens by how much Japanese a passage had, and how often you turned it
down or up.

**In chat, a level is a lookup budget**: how many lookups per 100 words you put up with, spent only on words you
don't know at a glance yet: 0.15 much lighter, 0.3 lighter, 0.5 normal, 0.8 heavier, 1.2 much heavier (a challenge,
when set, is normal's budget and scales the others). Words you know at a glance (under an 8% chance of a lookup)
cost nothing and go in wherever they fit at every level but off, so lighter and heavier change the learning words
only: the brief asks for as many per 100 words as spend about the budget, at most one in a sentence (two at heavier
and much heavier), and the line on the last reply says whether it went over or under.

**The ends: off and full.** Three presses lighter from normal turn Japanese off, and three heavier reach full, each
for 3 hours like the levels between (a press past either end stays there and restarts the clock);
`fw.py intensity [chat|browser] off|full` or `/japanese-weave:intensity off|full` sets them directly. In chat, off
means the hook sends only a one-line note to reply in plain English, until the time runs out, you press heavier,
or you say "japanese back on" in any chat.
Full has no budget: every sentence you could follow in plain Japanese (your known and learning words plus
common grammar) is written in Japanese, and the sentences that must stay English (steps, code, commands, exact
technical detail) swap in every known or learning word. A chat turned off with `off here` stays off at any level.
`fw.py calibrate` counts a press into off as lighter and into full as heavier.

**Just this chat: off.** Ask in the chat ("no japanese for this thread please", "english only from now on", "write no
Japanese in what you say to me") or run `/japanese-weave:intensity off here`, and that chat gets no Japanese from
that reply on, with no time limit, while other chats carry on as usual; "japanese back on" or `... normal here` brings
it back. Only a short request at the start or end of a prompt counts, never one about files, code, commits or the
plugin itself ("no Japanese in commit messages" leaves the chat alone). This works in Codex too, by asking. The
switch is kept per session id, recorded as a `thread` op (so the collector has it, and a change made while it is out
of reach still holds here at once), and forgotten 60 days after its last change. `fw.py calibrate` counts each chat
turned off as a lighter press.

`fw.py knowledge` shows the dial, your recent rate, and the words with the most evidence; `fw.py why <word>` shows
one word's estimate and every observation behind it. The design, with its sources:
[docs/adaptive-difficulty.md](docs/adaptive-difficulty.md).

## Privacy

Everything stays on your machine in `~/.japanese-weave` (override with `JAPANESE_WEAVE_HOME`). The hook reads your
local Claude Code and Codex transcripts to count exposure and never sends them anywhere. The extension sends
readable page text from sites you allow to the model you chose, Google's Gemini API or Anthropic's API, with your key
(free-tier Gemini keys allow Google to train on prompts). Private sites start off even when every site is on: webmail and messaging, AI chat, password
managers, banking and payments, health portals, government, and local or intranet addresses (subdomains included;
the popup can turn any of them on). It talks to the plugin through a native messaging host that only this extension
may call.

## Develop

```sh
python3 -m unittest discover -s tests            # hook, rules, exports, native host, setup, Anki, Codex
python3 scripts/make_blocks.py                   # regenerate blocks/ and extension/src/starter.js
cd extension && bun install && bun test           # extension (happy-dom)
sh tools/make-icons.sh                            # redraw the extension's icons from tools/icons/icon.svg
python3 tests/model_eval.py --out /tmp/jw-eval    # live check via claude -p
```

## License

MIT (French Weave © Jackie Oliver). The word list includes data derived from JMdict, © the Electronic Dictionary
Research and Development Group, CC BY-SA 4.0.
