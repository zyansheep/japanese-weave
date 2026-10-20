---
description: Make the Japanese heavier or lighter for 3 hours, in chat replies (default) or on web pages; or turn it off in this chat only
argument-hint: "[chat|browser] up|down|normal, or off|normal here"
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" intensity $ARGUMENTS --session "${CLAUDE_SESSION_ID}"`

`off here` turns Japanese off in this chat only, until `normal here` turns it back on (no 3-hour limit); saying "no
japanese here" or "japanese back on" in the chat does the same.

Tell the user in one plain-English line what changed, using the output above. No Japanese.
