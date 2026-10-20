---
description: Make the Japanese heavier or lighter (or off, or full) for 3 hours, in chat replies (default) or on web pages; or turn it off in this chat only
argument-hint: "[chat|browser] up|down|normal|off|full, or off|normal here"
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" intensity $ARGUMENTS --session "${CLAUDE_SESSION_ID}"`

`off` and `full` set the ends directly for 3 hours, in every chat (or on web pages, with `browser`): no Japanese at
all, or every sentence you can follow in plain Japanese written in Japanese and every known or learning word swapped
into the rest. `normal` brings back the usual amount.

`off here` turns Japanese off in this chat only, until `normal here` turns it back on (no 3-hour limit); saying "no
japanese here" or "japanese back on" in the chat does the same.

Tell the user in one plain-English line what changed, using the output above. No Japanese.
