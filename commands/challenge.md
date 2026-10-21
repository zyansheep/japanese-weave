---
description: Set normal's Japanese Weave lookup budget (lookups per 100 words; the other levels scale with it), or "off" for the default 0.5
argument-hint: off|0.5|1|1.5|2|3
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" challenge $ARGUMENTS`

Tell the user in one plain-English line what changed, using the output above. No Japanese.
