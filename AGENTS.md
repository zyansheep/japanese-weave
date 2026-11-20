# Notes for coding agents

## The checkout is live
Until the machines move to the Nix package ("Deploying"), this checkout's `main` is the user's live chat hook (a
directory plugin marketplace), and functor's checkout (`~/Repos/japanese-weave` there) is its own live hook. Do new
work on a branch in a worktree and merge to `main` only with the user's OK.

## Pull requests
Changes reach `main` through a pull request on GitHub, `zyansheep/japanese-weave` (always pass
`-R zyansheep/japanese-weave` to `gh pr create`: `upstream` is someone else's repository). CI's `check` must pass, and
PRs are squash-merged, so the PR title becomes the commit's title and its commits' messages its body. After a merge,
fast-forward the local `main` (the live hook) to `origin/main` before deploying.

## Tests
- Python: `sh scripts/run -m unittest discover -s tests -q` (or `python3 -m unittest ...` where python3 is on PATH;
  `scripts/run` finds a Python where it isn't, as on functor).
- Extension: `cd extension && bun test`.
- A change to the chat brief, the weave or the check: replay the chats so far through main and through the branch
  (`fw.py backtest --fw <main checkout>/scripts/fw.py --out before.json`, then `fw.py backtest --against before.json`)
  and put the numbers in the PR. It works on copies of the word state and writes nothing live.

## Ethos
Close design calls follow [ETHOS.md](ETHOS.md): show an approximate meaning rather than none, take a sentence down a
step rather than to bare English, and have code fix what it can see rather than drop it.

## Reloading the browser extension during development
`sh scripts/run scripts/fw.py extension` rebuilds `~/.japanese-weave/extension` (Chrome, unpacked) and
`~/.japanese-weave/japanese-weave.xpi` (Firefox) from this checkout, then opens the .xpi in the running Firefox-family
browser. The user's browser is Floorp on isomorph, with the add-on installed unsigned, so all the user does is press
"Add" in the prompt that appears. Run it after any change under `extension/` that should reach the browser, and tell
the user to press "Add". `--no-open` only rebuilds. Release Firefox needs `tools/sign-firefox.sh` instead; Chrome
needs the reload button in chrome://extensions.

Check the browser runs the new build from the collector's log: web plans in `contexts.plan` carry the fields the
current extension writes (since 0.11.0, `paint`).

## Deploying
The user's machines run Japanese Weave from the Nix package, through their NixOS configuration (README "Install with
Nix"): the flake is an input there, the always-on machine runs the collector as the module's user unit, and every
machine with Claude Code or a browser runs the plugin from `~/.local/share/japanese-weave/current` and the native host
from the store. A release is:

1. Merge the PR to `main` (with the user's OK). Fast-forward the local `main` too, for the hook that still runs from
   a checkout.
2. The configuration's bump job advances the `japanese-weave` input once `main`'s CI (`check`) is green; its CI
   builds the hosts with it, and its deployer switches the collector's machine before the clients.
3. On the switch the collector's unit backs up `~/.japanese-weave` (state.json, the other .json and .csv files, and
   interactions.sqlite) into `~/.japanese-weave/backups/<version>-<time>` and restarts on the new version; a client
   backs up its cache on activation. The last 5 are kept.
4. The browser extension stays a manual step: on the browser's machine run `fw extension` (the installed version's,
   on PATH) and tell the user to press "Add" in Floorp.

Check a deploy: `systemctl --user status japanese-weave-collector` on the collector's machine, `fw check` on each
machine, and `cat ~/.local/share/japanese-weave/current/VERSION`. Don't switch or rebuild hosts yourself: the deployer
does, or the user approves a rebuild. `nix flake check -L` runs the Python tests against the package from the
read-only store; a script that writes next to its own code fails there.

## Rules
- Never print, log, copy or commit `~/.japanese-weave/anthropic_key`.
- No Japanese in files, code, commits or PR text beyond word examples the docs already use.
- The live log is on functor: query it read-only (`sqlite3 -readonly`), never write to it by hand.
