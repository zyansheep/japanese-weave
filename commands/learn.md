---
description: Put Japanese words back into your learning set
argument-hint: <word>[, <word>...]
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" learn "$ARGUMENTS"`

Tell the user in one plain-English line what changed, using the output above. No Japanese.
