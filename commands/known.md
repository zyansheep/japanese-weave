---
description: Mark Japanese words as known (they stop being glossed); kanji, kana or romaji
argument-hint: <word>[, <word>...]
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" known "$ARGUMENTS"`

Tell the user in one plain-English line what changed, using the output above. No Japanese.
