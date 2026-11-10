---
description: Make the Japanese heavier or lighter (or off, or full) for 3 hours, in chat replies' words (default) or their Japanese sentences or on web pages; or turn it off in this chat only; or English only in chat for an hour
argument-hint: "[chat|sentences|browser] up|down|normal|off|full, or off|normal here, or english on|off"
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" intensity $ARGUMENTS --session "${CLAUDE_SESSION_ID}"`

Chat replies have two dials: `chat` (the default) is how many words are swapped into Japanese, `sentences` how many
sentences show as Japanese; each has its own level and its own 3 hours. With no step, the level is shown (both chat
dials when no surface is named).

`off` and `full` set the ends directly for 3 hours, in every chat (or on web pages, with `browser`): none of that
dial's Japanese (no swapped words, or no Japanese sentences), or all of it you can follow (every known or learning word
swapped in, or every sentence that can be Japanese). `normal` brings back the usual amount.

`english on` shows chat in English for an hour (T3 Code's shortcut toggles the same switch): the messages on screen
show their English, and new replies keep their Japanese twins, shown once it ends; `english off` ends it.

`off here` turns Japanese off in this chat only, until `normal here` turns it back on (no 3-hour limit); saying "no
japanese here" or "japanese back on" in the chat does the same.

Tell the user in one plain-English line what changed, using the output above. No Japanese.
