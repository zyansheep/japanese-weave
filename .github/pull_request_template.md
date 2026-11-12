## What and why

## Checklist
- [ ] A regression test for each bug fixed (one that fails without the fix).
- [ ] Ran on a copy of the real state (`JAPANESE_WEAVE_HOME` = a scratch copy of `~/.japanese-weave` without
      `anthropic_key`) when the change touches labels, Anki, intake, the fit or the collector's schema: the counts
      before and after are in the description.
- [ ] Deploy order stated: collector first? Does the change need `fw.py setup`, `fw.py extension` or a T3 Code release?
- [ ] T3 Code contract: does a request, reply or markup the fork reads change? If so, the fork's branch is linked.
- [ ] Docs (README, docs/) follow the change.
