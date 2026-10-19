---
description: Make the Japanese heavier or lighter for 3 hours, in chat replies (default) or on web pages
argument-hint: "[chat|browser] up|down|normal"
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" intensity $ARGUMENTS`

Tell the user in one plain-English line what changed, using the output above. No Japanese.
