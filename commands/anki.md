---
description: Put the words on your Anki cards under review at the front of the Japanese Weave list
argument-hint: <path to collection.anki2> [--deck NAME] [--no-known]
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" anki $ARGUMENTS`

Tell the user in two or three plain-English lines what changed, using the output above. No Japanese.
