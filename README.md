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
   pass `--gemini-key-file` to setup.

**Browser on another machine.** The word state lives where Claude Code runs. On the machine with the browser, clone
this repo and run `python3 scripts/fw.py setup --remote <ssh-host>`: the native host there forwards the extension's
messages to `<ssh-host>` over ssh (key login, no password prompt; `BatchMode=yes`).

**claude.ai or ChatGPT.** Run `/japanese-weave:export` (or `--chatgpt`) and paste the block, or paste a ready-made
level from [blocks/](blocks/level-01.md).

## What it does

- **Claude Code, Codex and T3 Code**: a prompt hook gives the model your stage and word lists every turn. Every
  sentence stays an English sentence; listed words drop in as written, with no particles or conjugation below
  Stage 3, suru-nouns standing in for English verbs ("let me 確認 the logs"). New words get a footer gloss with
  romaji, and every third reply opens with a recall cue. Never in code, files, commits or anything meant to be
  shared, and never in scripted runs (`claude -p`, Agent SDK scripts, `codex exec`). T3 Code threads are Agent SDK
  sessions too, so the hook checks for a `t3 serve` parent and weaves there.
- **Any website** (extension): a few English words on the page become Japanese. Hover for the meaning, romaji and
  kana; click to flip back. Swaps are chosen by Gemini on your own key.

All of it shares one word list on your machine, so words you see on the web count toward the chat and the other
way round.

## Commands (Claude Code)

| Command | What it does |
|---|---|
| `/japanese-weave:status` | Stage, word counts, the words you're learning |
| `/japanese-weave:known <word>` | Mark a word known: kanji, kana or romaji (`多分`, `たぶん`, `tabun`) |
| `/japanese-weave:learn <word>` | Put a word back into the learning set |
| `/japanese-weave:stage 1-4\|auto` | Pin your stage, or let it follow your known words |
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
- **Learning (active)**: up to 20 at a time; at most 5 new words a day, and none while 5 or more are shaky.
- **Learning → known**: 3 exposure days, with no flip, `learn` or peek on the last 3.
- **Flip or `learn` → shaky**; **shaky → known** after 7 days with no flip or peek.
- **Stage** follows your known words: 1 to start, 2 at 40 known, 3 at 150, 4 at 400.

| Stage | Japanese in a reply | Per sentence | Shape |
|---|---|---|---|
| 1 | about 6%, never over 10% | at most 2 | English sentences with swapped words |
| 2 | about 12%, never over 20% | at most 4 | same |
| 3 | about 25%, never over 35% | at most 4 | plus one short です/ます sentence in five |
| 4 | about 40%, never over 55% | at most 4 | plus two in five |

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
log is the data for estimating that. `fw.py log` shows the latest entries.

By default it goes to `interactions.sqlite` in the state folder. To keep one log for several machines, run the
collector on an always-on one and point the others at it, over a private network such as Tailscale:

```sh
fw.py collector install --listen 100.64.0.4:7853      # on the always-on machine: a systemd user service
fw.py setup --collector http://functor:7853            # on each browser machine (off: back to local)
```

T3 Code reports chat popovers when its server has `T3CODE_ANNOTATION_EVENTS_URL` set to the collector's
`/v1/interactions` URL; that needs a T3 Code build with the abbreviation popover patches.

## Privacy

Everything stays on your machine in `~/.japanese-weave` (override with `JAPANESE_WEAVE_HOME`). The hook reads your
local Claude Code and Codex transcripts to count exposure and never sends them anywhere. The extension sends
readable page text from sites you allow to Google's Gemini API with your key (free-tier keys allow Google to train
on prompts); webmail, AI chat sites and local addresses start off. It talks to the plugin through a native
messaging host that only this extension may call.

## Develop

```sh
python3 -m unittest discover -s tests            # hook, rules, exports, native host, setup, Anki, Codex
python3 scripts/make_blocks.py                   # regenerate blocks/ and extension/src/starter.js
cd extension && bun install && bun test           # extension (happy-dom)
python3 tests/model_eval.py --out /tmp/jw-eval    # live check via claude -p
```

## License

MIT (French Weave © Jackie Oliver). The word list includes data derived from JMdict, © the Electronic Dictionary
Research and Development Group, CC BY-SA 4.0.
