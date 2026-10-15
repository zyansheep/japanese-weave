# Installing Japanese Weave (instructions for a coding agent)

You are setting up Japanese Weave for the person you are working with. Follow these steps in order. Run every
command yourself; the person only does the steps marked HUMAN. Write no Japanese in anything you say during setup.

`<repo>` below is what the person gave you: a git URL or a local folder path. Python may be `python3` or only
`python` (some NixOS setups); `sh <repo>/scripts/run` picks whichever is 3.9+, so use it instead of calling Python.

## 0. Ask a few questions and list the human steps, in one batch, before you start

Run `codex --version` first: if it fails, leave Codex CLI out of question 2. Then send the human steps below as a
short message and ask the questions in one AskUserQuestion call (question 2 multi-select, the default option first
with "(default)" in its label). Anyone who skips a question gets the default.

Human steps (send as written):

> Setting up Japanese Weave. A few quick questions below; I'll do the rest, except these steps for you:
> 1. If you pick the browser extension, Firefox: open `about:debugging#/runtime/this-firefox`, click **Load Temporary
>    Add-on…** and pick the manifest file I give you (to keep it across restarts, sign it once with
>    `tools/sign-firefox.sh`). Chrome: `chrome://extensions`, **Developer mode**, **Load unpacked**, the folder I give
>    you. Then click the Japanese Weave toolbar icon and **Allow all websites** (or tick **This site** per site).
> 2. If you pick the browser extension: a Gemini API key (free at https://aistudio.google.com/apikey), as a file path
>    now or pasted into the extension's popup at the end.
> 3. If you pick Codex CLI: the next time you start `codex`, choose **Trust all and continue** for the new hook.

Questions:

1. How much Japanese do you know already? None (default) / A few basic words / I can read simple Japanese
2. Where should Japanese show up? (pick any) Claude Code, including T3 Code (always on with the plugin) / Codex CLI
   (default when installed) / The browser extension, Firefox or Chrome (default) / A claude.ai or ChatGPT block
3. If you picked the extension, which websites? Every site; turn any off from the toolbar (default) / Only sites I
   turn on
4. If you picked the extension, your Gemini API key: Paste it into the popup at the end (default) / A file that
   contains it (type its path under Other; never paste the key itself here)
5. Do you use Anki for Japanese? Skip (default) / The path to collection.anki2 (under Other; usually
   `~/.local/share/Anki2/<profile>/collection.anki2`). Cards under review go to the front of the word list.

Never ask for the key in chat, and never print it. If an answer looks like a key rather than a path (it starts with
`AIza`), don't pass it to setup; tell the person to paste it into the popup instead.

| Answer | Effect |
| --- | --- |
| 1. None / a few basic words / simple Japanese | `--known-top 0` / `--known-top 40` / `--known-top 150` |
| 2. Codex CLI | `--codex` |
| 2. Browser extension | pass answers 3 and 4; give the browser steps at the end |
| 2. Preferences block | run `export` at the end |
| 3. Every site / only sites I turn on | `--sites all` / `--sites ask` |
| 4. A file path | `--gemini-key-file <path>` |
| 5. A collection path | `--anki <path>` |

## 1. Install the Claude Code plugin

```sh
claude plugin marketplace add <repo>
claude plugin install japanese-weave@japanese-weave
claude plugin list | grep -A3 japanese-weave
```

## 2. Run setup

```sh
FW_ROOT="$(dirname "$(find "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins" -path '*japanese-weave*' -path '*/scripts/fw.py' 2>/dev/null | sort | tail -1)")"
sh "$FW_ROOT/run" "$FW_ROOT/fw.py" setup --known-top 0 --sites all --anki /path/to/collection.anki2   # for example
```

If `find` prints nothing, run setup from a clone of `<repo>` (`sh <clone>/scripts/run <clone>/scripts/fw.py setup ...`);
setup copies what it needs into `~/.japanese-weave`. Setup is safe to rerun and never resets word progress. It writes
the state dir, `extension-firefox/` and `extension/` copies, and native host manifests for Firefox
(`~/.mozilla/native-messaging-hosts`), Chrome and Chromium.

If the browser runs on a different machine from Claude Code, run setup there with `--remote <ssh-host-of-this-machine>`
too: its native host then reaches the word state over ssh (key login required).

## 3. Verify

```sh
sh "$FW_ROOT/run" "$FW_ROOT/fw.py" check
sh "$FW_ROOT/run" "$FW_ROOT/fw.py" status
```

Every `check` line must say `ok` (a `note` about the Gemini key or Anki is fine).

## 4. Hand over

Tell the person, in plain English, leaving out what they didn't pick: the browser steps with the exact paths from
`check`; the Gemini key step if no key file was given; the Codex trust step; that chat weaving starts in the next
Claude Code, T3 Code or Codex session they open; the export blocks if they picked them; and the commands
(`/japanese-weave:status`, `known`, `learn`, `stage`, `anki`, `pause`, `resume`, `export`).

## Uninstall

```sh
sh "$FW_ROOT/run" "$FW_ROOT/fw.py" uninstall --codex
claude plugin uninstall japanese-weave@japanese-weave
```

Then remove the extension from the browser, and delete `~/.japanese-weave` to drop the word progress.
