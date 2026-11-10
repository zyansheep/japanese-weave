# Notes for coding agents

## The checkout is live
This checkout's `main` is the user's live chat hook (a directory plugin marketplace), and functor's checkout
(`~/Repos/japanese-weave` there) is its own live hook. Do new work on a branch in a worktree and merge to `main` only
with the user's OK.

## Pull requests
Changes reach `main` through a pull request on GitHub, `zyansheep/japanese-weave` (always pass
`-R zyansheep/japanese-weave` to `gh pr create`: `upstream` is someone else's repository). CI's `check` must pass, and
PRs are squash-merged, so the PR title becomes the commit's title and its commits' messages its body. After a merge,
fast-forward the local `main` (the live hook) to `origin/main` before deploying.

## Tests
- Python: `sh scripts/run -m unittest discover -s tests -q` (or `python3 -m unittest ...` where python3 is on PATH;
  `scripts/run` finds a Python where it isn't, as on functor).
- Extension: `cd extension && bun test`.

## Reloading the browser extension during development
`sh scripts/run scripts/fw.py extension` rebuilds `~/.japanese-weave/extension` (Chrome, unpacked) and
`~/.japanese-weave/japanese-weave.xpi` (Firefox) from this checkout, then opens the .xpi in the running Firefox-family
browser. The user's browser is Floorp on isomorph, with the add-on installed unsigned, so all the user does is press
"Add" in the prompt that appears. Run it after any change under `extension/` that should reach the browser, and tell
the user to press "Add". `--no-open` only rebuilds. Release Firefox needs `tools/sign-firefox.sh` instead; Chrome
needs the reload button in chrome://extensions.

Check the browser runs the new build from the collector's log: web plans in `contexts.plan` carry the fields the
current extension writes (since 0.11.0, `paint`).

## Deploying (with the user's OK)
Back up `~/.japanese-weave` on both hosts, push `main` to functor
(`git push --no-verify functor:Repos/japanese-weave main:refs/heads/deploy`, then `git merge --ff-only deploy` there),
reinstall the collector there (`sh scripts/run scripts/fw.py collector install --listen 100.64.0.4:7853`) before
updating clients, then run `fw.py setup` on isomorph (no flags) and `fw.py extension` for the browser.

## Rules
- Never print, log, copy or commit `~/.japanese-weave/anthropic_key`.
- No Japanese in files, code, commits or PR text beyond word examples the docs already use.
- The live log is on functor: query it read-only (`sqlite3 -readonly`), never write to it by hand.
