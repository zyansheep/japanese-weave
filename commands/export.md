---
description: Paste-ready Japanese Weave block for claude.ai preferences or ChatGPT custom instructions
argument-hint: "[--claude-ai | --chatgpt] [--log-day]"
allowed-tools: Bash(sh:*)
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" export $ARGUMENTS`

Show the output above to the user exactly as printed, inside one code block per pasted field so it is easy to copy, with no Japanese and nothing added inside the blocks. After the blocks, one plain-English line: paste it where the output says, and re-export about once a week.
