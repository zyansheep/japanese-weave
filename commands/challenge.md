---
description: Set the Japanese Weave challenge (target glosses opened per 100 words shown), or turn it off
argument-hint: off|0.5|1|1.5|2|3
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" challenge $ARGUMENTS`

Tell the user in one plain-English line what changed, using the output above. No Japanese.
