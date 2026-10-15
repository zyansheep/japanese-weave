---
description: Set up Japanese Weave (asks a few questions, then installs the word state, browser extension and native host)
argument-hint: [--gemini-key-file <path>] [--anki <collection.anki2>]
allowed-tools: Bash(sh:*), AskUserQuestion
---
Setup plan (a dry run; nothing changed yet; don't show it to the user):
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" setup --dry-run`

Write no Japanese in anything you say during setup. Ask these questions in ONE AskUserQuestion call, the default
option first with "(default)" in its label. Leave Codex CLI out of question 2 if `codex_cli` above is null.

1. "How much Japanese do you know already?" None (default) / A few basic words / I can read simple Japanese.
2. "Where should Japanese show up?" (multi-select) Claude Code (always on with the plugin) / Codex CLI (default when
   installed) / The browser extension on websites, Firefox or Chrome (default) / A claude.ai or ChatGPT preferences
   block to paste.
3. "If you picked the browser extension, which websites?" Every site; turn any off from the toolbar (default) /
   Only sites I turn on.
4. "If you picked the browser extension, your Gemini API key (free at https://aistudio.google.com/apikey)?" Paste it
   into the extension's popup at the end (default) / A file that contains it: type its path under Other. Never
   paste the key itself here.

If the user passed `--anki <path>` in the arguments, keep it. Otherwise also ask whether they use Anki for Japanese:
their cards under review go to the front of the word list (path to collection.anki2, usually
~/.local/share/Anki2/<profile>/collection.anki2, under Other), or skip.

Skipped questions get the default. Never ask for the key in chat, and never print it. If the answer to question 4
looks like a key rather than a file path (for example it starts with `AIza`), don't run setup with it, don't repeat
it, and tell them to paste it into the extension popup instead. Then run, with the `source` folder from the plan
above:

`sh <source>/scripts/run <source>/scripts/fw.py setup $ARGUMENTS` plus:
- question 1: `--known-top 0`, `--known-top 40` or `--known-top 150`
- Codex CLI picked: `--codex`
- browser extension picked: `--sites all` or `--sites ask`, and `--gemini-key-file <path>` if they gave a file
- Anki collection given: `--anki <path>`

Relay the human steps from the output to the user as a short numbered list, in plain English, leaving out the
browser steps if they didn't pick the extension. If they picked the preferences block, run
`sh <source>/scripts/run <source>/scripts/fw.py export` and `... export --chatgpt` and show each block exactly as
printed, in its own code block, with where to paste it. If setup failed, say why and how to fix it.
