---
description: A 3-minute monthly check of how much of your reading you would read in Japanese without a lookup
allowed-tools: Bash(sh:*), AskUserQuestion
---
!`sh "${CLAUDE_PLUGIN_ROOT}/scripts/run" "${CLAUDE_PLUGIN_ROOT}/scripts/fw.py" probe --list`

This is a test of the user's Japanese, so give nothing away until it is recorded:

1. Show the numbered list above to the user exactly as printed, in one code block, without its last two lines. Add no
   readings, meanings, romaji, glosses, hints or remarks about any word, and write no other Japanese anywhere in your
   reply. A few of the words are made up on purpose; don't say which, or that any look odd.
2. Ask with AskUserQuestion, one question: "Which of these would you read without looking anything up, if you met
   them in an English sentence? Type their numbers under Other: 1 4 7-9, or all but 3 5." Options: "None of them",
   "All of them".
3. Record the answer with the command on the list's last line, putting what they typed in the quotes (`none` or
   `all` for those options). If it says the answer couldn't be read, ask again.
4. Show its output to the user as-is, in a code block. Now it is fine to talk about the made-up words it names. Add
   nothing else unless something failed.
