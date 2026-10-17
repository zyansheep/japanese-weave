#!/usr/bin/env python3
"""Japanese Weave command line: setup, status, word and stage changes. Python 3.9+, standard library only.

  fw.py setup [--gemini-key-file FILE] [--user-data-dir DIR]... [--known-top N] [--sites all|ask]
              [--browsers firefox,chrome] [--anki COLLECTION] [--remote SSH_HOST] [--dry-run]
                                                     install the state dir, extension copies (Firefox and
                                                     Chrome) and native host; --user-data-dir also registers
                                                     the host for a Chromium run with that --user-data-dir;
                                                     --known-top marks the top N lexicon words known unless
                                                     already introduced (40: some basics, 150: simple Japanese);
                                                     --sites: extension on every site (all) or only ones you
                                                     turn on (ask); --anki: run `anki` too; --remote: this
                                                     machine only runs the browser, the word state lives on
                                                     SSH_HOST (the native host talks to it over ssh);
                                                     --collector URL|off: the word state and the gloss log
                                                     live on that collector (see `collector`) and this
                                                     machine keeps a cache of them; off: back to local
  fw.py anki COLLECTION [--deck NAME] [--mature-days N] [--no-known] [--dry-run]
                                                     put the words on your Anki cards under review (learning
                                                     or review, not new or suspended) at the front of the
                                                     list; mature cards (interval >= N days, default 21)
                                                     become known unless --no-known
  fw.py status                                       stage, word counts, learning words
  fw.py log [N]                                      the latest N gloss peeks and flips (default 20), with how
                                                     long each stayed open and the glossed words around it
                                                     (from the collector when one is set)
  fw.py collector run|install --listen HOST:PORT [--db PATH]
                                                     the collector: the one home of the word state and the gloss
                                                     log for every machine and surface, reached over a private
                                                     network such as Tailscale; run it in the state dir of the
                                                     always-on machine (its own plugin uses it directly);
                                                     install: a systemd user service running the installed copy
                                                     (enable lingering so it runs logged out)
  fw.py known WORD...                                mark words as known
  fw.py learn WORD...                                put words back in the learning set (shaky)
  fw.py stage N|auto                                 pin the stage (1-4) or let it follow known words
  fw.py pause | resume                               stop or restart Japanese in chat and on websites
  fw.py glosses abbr|footer                          T3 Code chats: gloss each Japanese word in an <abbr> shown
                                                     on hover or click (needs a T3 build that renders them), or
                                                     in a footer line (default)
  fw.py refresh                                      rescan chat transcripts and apply the promotion rules
                                                     (with a collector: send them and anything queued, and
                                                     refresh the cached state)
  fw.py setup --codex [--dry-run]                    also add the chat hook to Codex CLI (~/.codex or $CODEX_HOME)
  fw.py export [--claude-ai | --chatgpt] [--log-day] paste-ready block for claude.ai preferences or ChatGPT
  fw.py log-chat-day                                 opt-in: count today as exposure for your learning words
  fw.py check                                        verify the state, extension folder and native host
  fw.py uninstall [--codex] [--dry-run]              remove the native host manifests (and the Codex hook)
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwlib  # noqa: E402
import export  # noqa: E402
import anki  # noqa: E402
import feedback  # noqa: E402
import hub  # noqa: E402
import knowledge  # noqa: E402

HOST_NAME = 'com.japaneseweave.host'
EXTENSION_ID = 'memhbflhgmgbmoaphojljmdmcamnmljb'  # Chrome: fixed by the "key" in extension/manifest.json
GECKO_ID = 'japanese-weave@weave.local'  # Firefox: browser_specific_settings.gecko.id in extension-firefox/
FIREFOX_MIN = '128.0'  # optional_host_permissions in MV3


def host_dirs(extra=()):
    """(folder, browser) for user-level native messaging hosts on this OS: Firefox, Google Chrome and Chromium, plus
    any extra Chromium user-data dirs (a browser started with --user-data-dir looks only inside that dir)."""
    home = Path.home()
    extra = [(Path(d).expanduser().resolve() / 'NativeMessagingHosts', 'chrome') for d in extra]
    if sys.platform == 'darwin':
        base = home / 'Library' / 'Application Support'
        return [(base / 'Mozilla' / 'NativeMessagingHosts', 'firefox'),
                (base / 'Google' / 'Chrome' / 'NativeMessagingHosts', 'chrome'),
                (base / 'Chromium' / 'NativeMessagingHosts', 'chrome')] + extra
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    return [(home / '.mozilla' / 'native-messaging-hosts', 'firefox'),
            (config / 'google-chrome' / 'NativeMessagingHosts', 'chrome'),
            (config / 'chromium' / 'NativeMessagingHosts', 'chrome')] + extra


def host_manifest(browser, launcher):
    manifest = {'name': HOST_NAME, 'description': 'Japanese Weave word state', 'path': str(launcher), 'type': 'stdio'}
    if browser == 'firefox':
        manifest['allowed_extensions'] = [GECKO_ID]
    else:
        manifest['allowed_origins'] = [f'chrome-extension://{EXTENSION_ID}/']
    return manifest


def python_path():
    """A Python 3.9+ path that survives upgrades, for launchers: python3 or python on PATH (some systems have only
    one of them), else this interpreter."""
    here = Path(sys.executable).resolve()
    for name in ('python3', 'python'):
        found = shutil.which(name)
        if found and (Path(found).resolve() == here or subprocess.run(
                [found, '-c', 'import sys; sys.exit(sys.version_info < (3, 9))'], capture_output=True).returncode == 0):
            return found
    return sys.executable


def firefox_manifest(manifest):
    """The Chrome manifest adapted for Firefox: event-page background scripts instead of a service worker, a gecko
    id (native messaging needs it), and no Chrome key."""
    manifest = dict(manifest)
    manifest.pop('key', None)
    manifest['background'] = {'scripts': [manifest['background']['service_worker']], 'type': 'module'}
    manifest['browser_specific_settings'] = {'gecko': {
        'id': GECKO_ID, 'strict_min_version': FIREFOX_MIN,
        'data_collection_permissions': {'required': ['websiteContent']}}}  # page text goes to Gemini
    return manifest


def plan(extra=()):
    root, home = fwlib.PLUGIN_ROOT, fwlib.home()
    return {
        'python': python_path(),
        'python_version': platform.python_version(),
        'platform': sys.platform,
        'source': str(root),
        'state_dir': str(home),
        'extension_dir_firefox': str(home / 'extension-firefox'),
        'extension_dir_chrome': str(home / 'extension'),
        'host_launcher': str(home / 'app' / 'fw-host'),
        'host_manifests': [str(d / f'{HOST_NAME}.json') for d, _ in host_dirs(extra)],
        'extension_id': EXTENSION_ID,
        'gecko_id': GECKO_ID,
        'codex_cli': shutil.which('codex'),
    }


CODEX_MARK = 'japanese-weave'  # our Codex hook command always contains this (the state dir path)


def codex_command():
    return f'"{python_path()}" "{fwlib.home() / "app" / "scripts" / "weave.py"}" --hook'


def is_ours(handler):
    command = handler.get('command', '') if isinstance(handler, dict) else ''
    return 'weave.py' in command and '--hook' in command and str(fwlib.home()) in command


def merge_codex_hooks(data, command):
    """hooks.json with our UserPromptSubmit handler added or updated in place; other hooks untouched."""
    data = data if isinstance(data, dict) else {}
    groups = data.setdefault('hooks', {}).setdefault('UserPromptSubmit', [])
    for group in groups:
        for handler in group.get('hooks', []):
            if is_ours(handler):
                handler['command'] = command  # same place, so Codex keeps the trust if nothing changed
                return data
    groups.append({'hooks': [{'type': 'command', 'command': command}]})
    return data


def enable_codex_hooks(text):
    """config.toml text with `hooks = true` in its [features] table (added if missing)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == '[features]'), None)
    if start is None:
        return text.rstrip('\n') + ('\n\n' if text.strip() else '') + '[features]\nhooks = true\n'
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith('[')), len(lines))
    for i in range(start + 1, end):
        if re.match(r'\s*hooks\s*=', lines[i]):
            lines[i] = 'hooks = true'
            break
    else:
        lines.insert(start + 1, 'hooks = true')
    return '\n'.join(lines) + '\n'


def setup_codex(dry_run):
    codex = fwlib.codex_home()
    hooks_path, config_path = codex / 'hooks.json', codex / 'config.toml'
    data = merge_codex_hooks(fwlib.read_json(hooks_path, {}) if hooks_path.exists() else {}, codex_command())
    config = enable_codex_hooks(config_path.read_text() if config_path.exists() else '')
    if dry_run:
        print(json.dumps({'codex_hooks': str(hooks_path), 'codex_config': str(config_path), 'command': codex_command()},
                         indent=1))
        return
    if hooks_path.exists() and fwlib.read_json(hooks_path) is None:
        sys.exit(f'{hooks_path} is not valid JSON; fix it, then rerun setup --codex.')
    codex.mkdir(parents=True, exist_ok=True)
    for path in (hooks_path, config_path):
        backup = path.with_name(path.name + '.before-japanese-weave')
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
    fwlib.write_json(hooks_path, data)
    config_path.write_text(config)
    print(f'Codex: chat hook in {hooks_path}, hooks enabled in {config_path}.')
    print('  Human step: start `codex` once; at "Hooks need review" pick "Trust all and continue" (or review first).')
    print('  Codex never runs a new hook before that. `codex exec` runs stay Japanese-free either way.')


KEY_SHAPE = re.compile(r'^[A-Za-z0-9_-]{30,64}$')


def read_key_file(arg):
    """The key from a file. Never echoes the argument, the path or the contents, since any of them may be the key."""
    path = Path(arg).expanduser()
    if not path.is_file() and (arg.startswith('AIza') or KEY_SHAPE.match(arg)):
        sys.exit('That looks like a key, not a file path; paste the key into the extension popup instead.')
    try:
        key = path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        sys.exit('Could not read the Gemini key file.')
    if not KEY_SHAPE.match(key):
        sys.exit('The Gemini key file should hold only the key (letters, digits, - and _).')
    return key


def write_launcher(home, remote=None, remote_home=None):
    """The native host launcher the browser starts. Remote: the word state lives on another machine, so the
    browser's messages go to that machine's own launcher over ssh (keys only, never a password prompt)."""
    launcher = home / 'app' / 'fw-host'
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if remote:
        target = shlex.quote(f'{remote_home or ".japanese-weave"}/app/fw-host')
        launcher.write_text(f'#!/bin/sh\n# Japanese Weave: the word state lives on {remote}.\n'
                            f'exec ssh -T -o BatchMode=yes -o ConnectTimeout=10 {shlex.quote(remote)} {target}\n')
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{python_path()}" "{home / "app" / "scripts" / "fw_host.py"}" "$@"\n')
    launcher.chmod(0o755)
    return launcher


XPI_NAME = 'japanese-weave.xpi'


def install_app(root, home):
    """Stable copies of the scripts and word lists: launchers and services need fixed paths."""
    for name, ignore in (('scripts', shutil.ignore_patterns('__pycache__')), ('data', None)):
        target = home / 'app' / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(root / name, target, ignore=ignore)


def install_extensions(root, home):
    """Stable copies: the plugin cache path changes on every update, browsers need fixed paths."""
    install_app(root, home)
    target = home / 'extension'
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(root / 'extension', target,
                    ignore=shutil.ignore_patterns('test', 'node_modules', 'package.json', 'bun.lock'))
    firefox = home / 'extension-firefox'
    if firefox.exists():
        shutil.rmtree(firefox)
    shutil.copytree(home / 'extension', firefox)
    manifest = fwlib.read_json(firefox / 'manifest.json')
    fwlib.write_json(firefox / 'manifest.json', firefox_manifest(manifest))
    # A permanent install for Firefox builds that allow unsigned add-ons (Floorp, Developer Edition, Nightly, ESR).
    with zipfile.ZipFile(home / XPI_NAME, 'w', zipfile.ZIP_DEFLATED) as xpi:
        for path in sorted(p for p in firefox.rglob('*') if p.is_file()):
            xpi.write(path, path.relative_to(firefox).as_posix())


def setup(args):
    info = plan(args.user_data_dir)
    if sys.version_info < (3, 9):
        sys.exit(f'Japanese Weave needs Python 3.9 or newer; this is {info["python_version"]}.')
    root, home = fwlib.PLUGIN_ROOT, fwlib.home()
    browsers = [b.strip() for b in args.browsers.split(',') if b.strip()]
    if not (root / 'extension' / 'manifest.json').exists():
        sys.exit(f'No extension/ folder next to {root / "scripts"}; run setup from a full copy of the repo.')
    key = read_key_file(args.gemini_key_file) if args.gemini_key_file else None
    if args.dry_run:
        print(json.dumps(dict(info, dry_run=True, gemini_key='would be stored' if key else 'not given',
                              known_top=args.known_top, sites=args.sites, browsers=browsers, remote=args.remote,
                              anki=args.anki), indent=1))
        if args.codex:
            setup_codex(True)
        return
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    install_extensions(root, home)
    launcher = write_launcher(home, args.remote, args.remote_home)
    for directory, browser in host_dirs(args.user_data_dir):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{HOST_NAME}.json').write_text(json.dumps(host_manifest(browser, launcher), indent=1) + '\n')
    remote_file = home / 'remote'
    if args.remote:
        remote_file.write_text(args.remote + '\n')
    elif remote_file.exists():
        remote_file.unlink()
    previous = fwlib.collector_url()
    if args.collector == 'off':
        (home / 'collector').unlink(missing_ok=True)
        (home / hub.SYNCED).unlink(missing_ok=True)  # the cache is this machine's own state again
    elif args.collector:
        (home / 'collector').write_text(args.collector.rstrip('/') + '\n')
    if fwlib.collector_url() and not args.remote:
        problem = join_collector(home, fwlib.collector_url())
        if problem:
            if previous:
                (home / 'collector').write_text(previous + '\n')
            else:
                (home / 'collector').unlink(missing_ok=True)
            sys.exit(problem)
    if key:
        path = home / 'gemini_key'
        path.write_text(key + '\n')
        path.chmod(0o600)
    if args.remote:
        print(f'Japanese Weave browser side is set up; the word state stays on {args.remote}.')
        print(f'  native host: {launcher} (ssh {args.remote}; needs key login without a password prompt)')
        print(f'  Gemini key:  {"stored here" if key else "from " + args.remote + " if setup stored one there, else paste it in the popup"}')
        human_steps(home, browsers, args.sites or 'all', True)  # the key may come from the remote side
        return
    collector = fwlib.collector_url()
    if args.anki:  # first, so your Anki words are the first learning words
        anki_sync(argparse.Namespace(collection=args.anki, deck=None, mature_days=anki.MATURE_DAYS, no_known=False,
                                     dry_run=False))
    if args.known_top or not collector:  # here, the first run creates the state; later runs add new lexicon words
        hub.submit({'kind': 'seed', 'n': args.known_top})  # seeds only words not yet introduced
    if args.sites:
        hub.submit({'kind': 'set', 'prefs': {'extension_sites': args.sites}})
    state, error = hub.current()
    if args.codex:
        setup_codex(False)
    print('Japanese Weave is set up.')
    where = f'on {collector}, cached in {home / "state.json"}' if collector else str(home / 'state.json')
    print(f'  word state:  {where} (Stage {fwlib.stage_for(state)}, '
          f'{sum(w["status"] == "known" for w in state["words"])} known, '
          f'{sum(w["status"] == "active" for w in state["words"])} learning words)')
    if error:
        print(f'  ({collector} did not answer: {error}; the copy here is from the last sync)')
    sites = (state.get('prefs') or {}).get('extension_sites', 'all')
    print(f'  extension:   {"every site; turn any off from the toolbar" if sites == "all" else "only sites you turn on"}')
    print(f'  native host: {", ".join(info["host_manifests"])}')
    print(f'  Gemini key:  {"stored for the extension" if key or (home / "gemini_key").exists() else "not stored; paste it in the extension popup"}')
    human_steps(home, browsers, sites, key or (home / 'gemini_key').exists())
    print('Chat weaving runs wherever the plugin is enabled; sessions opened from now on pick it up.')


def join_collector(home, collector):
    """None when this machine can keep a cache of the collector's word state, else why not. The first pull keeps this
    machine's own state (if any) in before-collector/ (hub.keep_own_state), since the collector's replaces it."""
    health = feedback.remote_health(collector)
    if health.get('ok') and health.get('host') == socket.gethostname() and health.get('home') == str(home):
        return (f'{collector} is this machine, serving this state dir: the word state already lives here, so set up '
                'without --collector.')
    own = (home / 'state.json').exists() and not (home / hub.SYNCED).exists()
    if health.get('ok') and health.get('state') is False:
        if own:
            return (f'{collector} has no word state yet. To move this machine\'s there, stop the collector, copy '
                    f'{", ".join(n for n in hub.MOVED if (home / n).exists())} from {home} into its state dir, start '
                    'it, and rerun this.')
        return f'{collector} has no word state yet: run fw.py setup on that machine first, then rerun this.'
    if own and health.get('state') is not True:  # unreachable, or an older collector that only keeps the gloss log
        return (f'{collector} did not confirm it holds a word state ({health.get("error") or "an older collector?"}). '
                'This machine has its own, so nothing changed: update and start the collector, then rerun this.')
    synced, error = hub.sync(scan=False)
    if not synced and not (home / 'state.json').exists():
        return (f'The word state lives on {collector}, which did not answer: {error}. Set it up there first '
                '(fw.py setup on that machine, then fw.py collector install), then rerun this.')
    if own:
        print(f'From now on the word state on {collector} is the one in use; this machine\'s own is kept in '
              f'{home / hub.BEFORE}.')
    return None


def human_steps(home, browsers, sites, has_key):
    print('Human steps left (browsers block scripts from doing these):')
    n = 0
    if 'firefox' in browsers:
        n += 1
        print(f'  {n}. Firefox: open about:debugging#/runtime/this-firefox, click "Load Temporary Add-on..." and pick')
        print(f'     {home / "extension-firefox" / "manifest.json"}')
        print('     (temporary: gone when Firefox quits). To keep it in a build that allows unsigned add-ons (Floorp,')
        print('     Developer Edition, Nightly, ESR): set xpinstall.signatures.required to false in about:config, then')
        print('     about:addons > gear > "Install Add-on From File" and pick')
        print(f'     {home / XPI_NAME}')
        print('     (release Firefox needs it signed first: tools/sign-firefox.sh).')
    if 'chrome' in browsers:
        n += 1
        print(f'  {n}. Chrome/Chromium: chrome://extensions, switch on Developer mode, "Load unpacked", pick:')
        print(f'     {home / "extension"}')
    n += 1
    print(f'  {n}. Pin the Japanese Weave toolbar icon and click it:')
    if sites == 'all':
        print('     click "Allow all websites" (the browser asks once; until then it runs only on X).')
    else:
        print('     on each site you want, tick "This site" (the browser asks for that site).')
    if not has_key:
        print('     Paste a Gemini API key (https://aistudio.google.com/apikey).')


def anki_sync(args):
    words, report = anki.review_words(args.collection, deck=args.deck, mature_days=args.mature_days,
                                      base=fwlib.read_lexicon(fwlib.LEXICON))
    mature = [w for w in words if w['mature']]
    print(f'Anki: {report["cards"]} cards under review on {report["notes"]} notes; {len(words)} usable words '
          f'({len(mature)} mature, interval >= {args.mature_days} days).')
    for reason, count in sorted(report['skipped'].items()):
        print(f'  skipped {count}: {reason}')
    if args.dry_run:
        for w in words[:40]:
            print(f'  {w["ja"]} = {w["en"]}, "{w["hint"]}"{"  (mature)" if w["mature"] else ""}')
        return
    known = [] if args.no_known else [w['ja'] for w in mature]
    result = hub.submit({'kind': 'anki', 'words': words, 'known': known})
    if result.get('queued'):
        print(f'{len(words)} Anki words are saved here and go to the front of the list once {fwlib.collector_url()} '
              'can be reached (the next sync sends them).')
        return
    print(f'Front of the list: {len(words)} Anki words; {result.get("known", 0)} mature ones known. '
          f'Rerun `fw.py anki` whenever you like; it never resets progress.')


def check(_args):
    """Prints one line per check; exits 1 if any failed."""
    import struct
    home, ok = fwlib.home(), True

    def report(passed, what):
        nonlocal ok
        ok = ok and passed
        print(f'{"ok  " if passed else "FAIL"} {what}')

    remote = (home / 'remote').read_text().strip() if (home / 'remote').exists() else None
    collector = fwlib.collector_url()
    if remote:
        print(f'note word state lives on {remote} (this machine runs only the browser side)')
    elif collector:
        state = fwlib.load_state(create=False)
        pulled = (fwlib.read_json(home / hub.SYNCED, {}) or {}).get('pulled')
        when = time.strftime('%Y-%m-%d %H:%M', time.localtime(pulled)) if pulled else 'never'
        report(state is not None, f'copy of the word state on {collector} (last refreshed {when})'
               + (f', Stage {fwlib.stage_for(state)}' if state else ''))
        waiting = len(hub.pending())
        if waiting:
            print(f'note {waiting} change(s) wait here for {collector}; `fw.py refresh` sends them')
    else:
        state = fwlib.load_state(create=False)
        report(state is not None, f'word state at {home / "state.json"}' + (f' (Stage {fwlib.stage_for(state)})' if state else ''))
    report((home / 'extension-firefox' / 'manifest.json').exists(),
           f'Firefox extension for "Load Temporary Add-on": {home / "extension-firefox" / "manifest.json"}')
    report((home / 'extension' / 'manifest.json').exists(), f'Chrome extension folder for Load unpacked: {home / "extension"}')
    launcher = home / 'app' / 'fw-host'
    for directory, browser in host_dirs():
        path = directory / f'{HOST_NAME}.json'
        manifest = fwlib.read_json(path)
        report(bool(manifest) and manifest == host_manifest(browser, launcher), f'native host manifest {path}')
    try:
        message = json.dumps({'type': 'ping'}).encode()
        out = subprocess.run([str(launcher)], input=struct.pack('=I', len(message)) + message, capture_output=True,
                             timeout=30).stdout
        report(json.loads(out[4:]).get('ok') is True, f'native host answers: {launcher}')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report(False, f'native host answers: {launcher} ({error.__class__.__name__})')
    hooks = fwlib.read_json(fwlib.codex_home() / 'hooks.json', {}) or {}
    groups = (hooks.get('hooks') or {}).get('UserPromptSubmit') or []
    if any(is_ours(h) for g in groups for h in g.get('hooks', [])):
        config = (fwlib.codex_home() / 'config.toml')
        enabled = config.exists() and enable_codex_hooks(config.read_text()) == config.read_text().rstrip('\n') + '\n'
        report(enabled, f'Codex hook in {fwlib.codex_home() / "hooks.json"} with [features] hooks = true '
                        '(Codex asks you to trust it on first start)')
    anki_csv = home / fwlib.ANKI_LEXICON
    if not remote and not collector:
        print(f'{"ok  " if anki_csv.exists() else "note"} Anki words '
              f'{"at the front of the list (" + str(len(fwlib.read_lexicon(anki_csv))) + ")" if anki_csv.exists() else "not synced; run fw.py anki <collection.anki2>"}')
    if collector:
        health = feedback.remote_health(collector)
        report(health.get('ok') is True and health.get('state') is not False, f'collector {collector} answers'
               + (f' ({health.get("interactions")} interactions)' if health.get('ok') else f': {health.get("error")}')
               + (' but has no word state yet' if health.get('state') is False else ''))
    has_key = (home / 'gemini_key').exists()
    print(f'{"ok  " if has_key else "note"} Gemini key {"stored by setup" if has_key else "not stored; paste it in the extension popup"}')
    if not ok:
        sys.exit(1)


def uninstall(args):
    if args.codex:
        path = fwlib.codex_home() / 'hooks.json'
        data = fwlib.read_json(path, {}) or {}
        groups = (data.get('hooks') or {}).get('UserPromptSubmit') or []
        kept = [g for g in ({**g, 'hooks': [h for h in g.get('hooks', []) if not is_ours(h)]} for g in groups) if g['hooks']]
        if len(kept) != len(groups) or sum(len(g['hooks']) for g in kept) != sum(len(g.get('hooks', [])) for g in groups):
            print(('would remove' if args.dry_run else 'removed') + f' the Japanese Weave hook from {path}')
            if not args.dry_run:
                data['hooks']['UserPromptSubmit'] = kept
                fwlib.write_json(path, data)
    for directory, _ in host_dirs():
        path = directory / f'{HOST_NAME}.json'
        if path.exists():
            print(('would remove ' if args.dry_run else 'removed ') + str(path))
            if not args.dry_run:
                path.unlink()
    print(f'Your word state stays in {fwlib.home()}; delete that folder to start over.')


def status(_args):
    state, error = hub.current()
    stale_note(error)
    by = {}
    for w in state['words']:
        by.setdefault(w['status'], []).append(w)
    stage = fwlib.stage_for(state)
    print(f'Japanese Weave: Stage {stage}{" (pinned)" if state.get("stage_pinned") else ""}'
          f'{", PAUSED" if state.get("paused") else ""}')
    print(f'  known {len(by.get("known", []))}, learning {len(by.get("active", []))}, '
          f'shaky {len(by.get("shaky", []))}, not yet introduced {len(by.get("queue", []))}')
    chat = (fwlib.read_json(fwlib.home() / 'exposure.json', {}) or {}).get('words', {})
    for name in ('shaky', 'active'):
        for w in by.get(name, []):
            days = len(fwlib.exposure_days(w, chat))
            print(f'  {name:6} {w["ja"]} = {w["en"]}, "{w["hint"]}"  (seen on {days} day{"s" if days != 1 else ""})')
    nxt = next((n for n, s in sorted(fwlib.STAGES) if fwlib.stage_for(state) < s), None)
    if nxt and not state.get('stage_pinned'):
        print(f'  next stage at {nxt} known words')


def stale_note(error):
    if error:
        print(f'({fwlib.collector_url()} did not answer: {error}. Showing the copy here from the last sync.)')


def queued_note(result):
    if result.get('queued'):
        print(f'(Saved here: {fwlib.collector_url()} did not answer, so the next sync sends it.)')


def mark(kind):
    def run(args):
        result = hub.submit({'kind': 'mark', 'words': args.words, 'as': kind})
        if result.get('done'):
            print(f'{"Known" if kind == "known" else "Back to learning"}: {", ".join(result["done"])}')
        if result.get('missing'):
            print(f'Not in the word list: {", ".join(result["missing"])}')
        queued_note(result)
    return run


def stage(args):
    queued_note(hub.submit({'kind': 'set', 'stage': 'auto' if args.value == 'auto' else int(args.value)}))
    state = fwlib.load_state()
    print(f'Stage {fwlib.stage_for(state)}{" (pinned)" if state.get("stage_pinned") else " (automatic)"}')


def log_cmd(args):
    collector = fwlib.collector_url()
    since = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))) * 1000
    try:
        rows = feedback.remote_recent(collector, args.limit) if collector else feedback.recent(args.limit)
        totals = feedback.remote_stats(collector, since) if collector else feedback.stats(since)
    except OSError as error:
        sys.exit(f'Collector {collector}: {error}')
    if not rows:
        print('No gloss peeks or flips logged yet.')
        return
    print(collected(totals) + '\n')
    for row in rows:
        when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['at'] / 1000))
        glossed = len(row['annotations'])
        if row['kind'] == 'click':
            print(f'{when}  {row["surface"]:<7} click  on {row["detail"]["target"]} at {row["x"]:.0f},{row["y"]:.0f} in a '
                  f'passage with {plural(glossed, "glossed word")}')
        elif row['kind'] == 'select':
            d = row['detail']
            print(f'{when}  {row["surface"]:<7} select {plural(d["chars"], "character")}, '
                  f'{len(d["covered"])} of the passage\'s {plural(glossed, "glossed word")}')
        elif row['kind'] == 'visible':
            d = row['detail']
            left_out = ', '.join(f'{d[k] / 1000:.0f}s {k}' for k in ('away', 'media', 'idle') if d[k] >= 1000)
            print(f'{when}  {row["surface"]:<7} on screen {row["ms"] / 1000:.0f}s of {d["wall"] / 1000:.0f}s'
                  f'{f" ({left_out})" if left_out else ""}, a paragraph with {plural(glossed, "glossed word")}')
        elif row['kind'] == 'trace':
            print(f'{when}  {row["surface"]:<7} mouse path {plural(len(row["detail"]["points"]), "point")} over '
                  f'{row["ms"] / 1000:.1f}s, a message with {plural(glossed, "glossed word")}')
        else:
            print(f'{when}  {row["surface"]:<7} {row["kind"]:<4} {row["word"]}  open {row["dwellMs"] / 1000:.1f}s, '
                  f'{plural(glossed - 1, "other glossed word")} in the passage')


def plural(n, noun):
    return f'{n} {noun}{"" if n == 1 else "s"}'


def collected(totals):
    """One line of totals from feedback.stats, with today's share."""
    if not totals.get('ok', True):
        return f'Totals unavailable: {totals.get("error")}'
    counts = totals.get('counts') or {}
    def kind(*names):
        pairs = [v for k, v in counts.items() if k.split(':')[1] in names]
        return sum(p[0] for p in pairs), sum(p[1] for p in pairs)
    parts = []
    for names, noun in ((('peek', 'pin'), 'peek'), (('flip',), 'flip'), (('click',), 'click'), (('select',), 'selection'),
                        (('trace',), 'mouse path')):
        total, today = kind(*names)
        parts.append(f'{plural(total, noun)} ({today} today)')
    shown = totals.get('shownWords') or [0, 0]
    reading = [round(ms / 60000) for ms in totals.get('readingMs') or [0, 0]]
    return (f'Collected: {", ".join(parts)}; {plural(shown[0], "word")} shown on pages ({shown[1]} today); '
            f'{reading[0]} min reading on screen ({reading[1]} today).')


def collector_arg(value):
    if value != 'off' and not re.match(r'^https?://[^/\s]+/?$', value):
        raise argparse.ArgumentTypeError('a URL like http://functor:7853, or off')
    return value


COLLECTOR_UNIT = 'japanese-weave-collector.service'


def collector_cmd(args):
    import collector
    if args.action == 'run':
        collector.main(['--listen', args.listen] + (['--db', args.db] if args.db else []))
        return
    home = fwlib.home()
    install_app(fwlib.PLUGIN_ROOT, home)
    unit = Path.home() / '.config' / 'systemd' / 'user' / COLLECTOR_UNIT
    unit.parent.mkdir(parents=True, exist_ok=True)
    command = [python_path(), str(home / 'app' / 'scripts' / 'collector.py'), '--listen', args.listen]
    if args.db:
        command += ['--db', str(Path(args.db).expanduser().resolve())]
    unit.write_text('[Unit]\nDescription=Japanese Weave collector (gloss interactions from every machine)\n'
                    'After=network-online.target\nWants=network-online.target\n\n'
                    f'[Service]\nExecStart={shlex.join(command)}\nRestart=on-failure\nRestartSec=10\n\n'
                    '[Install]\nWantedBy=default.target\n')
    for step in (['daemon-reload'], ['enable', COLLECTOR_UNIT], ['restart', COLLECTOR_UNIT]):
        subprocess.run(['systemctl', '--user', *step], check=True)
    print(f'Collector installed as the systemd user service {COLLECTOR_UNIT}, listening on {args.listen}.')
    linger = subprocess.run(['loginctl', 'show-user', os.environ.get('USER', ''), '--property=Linger'],
                            capture_output=True, text=True).stdout.strip()
    if linger != 'Linger=yes':
        print('Lingering is off, so it stops when you log out: sudo loginctl enable-linger $USER')


def glosses(args):
    queued_note(hub.submit({'kind': 'set', 'prefs': {'chat_glosses': args.style}}))
    print('T3 Code chats: each Japanese word shows its romaji and meaning on hover or click.' if args.style == 'abbr' else
          'T3 Code chats: learning words are glossed in a footer line.')


def pause(value):
    def run(_args):
        queued_note(hub.submit({'kind': 'set', 'paused': value}))
        print('Japanese Weave paused: no Japanese in chat or on websites until you resume.' if value else 'Japanese Weave resumed.')
    return run


def export_cmd(args):
    result = hub.submit({'kind': 'exported', 'target': 'chatgpt' if args.chatgpt else 'claude-ai',
                         'log_day': bool(args.log_day)})
    state, logged = fwlib.load_state(), result.get('logged', 0)
    if args.chatgpt:
        about, respond = export.chatgpt(state)
        print(f'ChatGPT > Settings > Personalization > Custom instructions. Each field holds {export.CHATGPT_LIMIT} '
              'characters on every plan.\n')
        print(f'--- "What would you like ChatGPT to know about you?" ({len(about)} characters) ---\n{about}\n')
        print(f'--- "How would you like ChatGPT to respond?" ({len(respond)} characters) ---\n{respond}')
    else:
        block = export.claude_ai(state)
        print(f'claude.ai > Settings > Profile > personal preferences. Replace the old Japanese Weave block '
              f'({len(block)} characters):\n\n{block}')
    if logged:
        print(f'\n(Counted today as chat exposure for {logged} learning words.)')


def log_chat_day(_args):
    result = hub.submit({'kind': 'chat_day'})
    logged = result.get('logged', 0)
    queued_note(result)
    print(f'Counted today as chat exposure for {logged} learning words (never enough on its own to make a word known).')


def refresh(_args):
    if fwlib.collector_url():
        synced, error = hub.sync(timeout=hub.FLUSH_TIMEOUT)  # in the background: it can wait longer
        if not synced:
            print(f'sync failed: {error}', file=sys.stderr)
        return
    fwlib.refresh_exposure()
    try:
        knowledge.refresh()
    except Exception as error:  # never let the estimates break the exposure refresh
        print(f'knowledge refresh failed: {error.__class__.__name__}: {error}', file=sys.stderr)


def fresh_knowledge():
    """(state, knowledge): replayed here, or the collector's, refreshed in the cache."""
    if fwlib.collector_url():
        state, error = hub.current()
        stale_note(error)
        return state, knowledge.load()
    know = knowledge.refresh() or knowledge.load()
    return hub.current()[0], know


def knowledge_cmd(args):
    state, know = fresh_knowledge()
    words = know.get('words') or {}
    rate = know.get('rate') or {}
    challenge = knowledge.challenge_of(state)
    print('Adaptive difficulty: ' + (f'aiming at {challenge} peeks per 100 words shown (budget {know.get("budget")})'
                                     if challenge else 'off (fw.py challenge 1.5 turns it on)'))
    per100 = rate.get('per100')
    print(f'  browser, last {rate.get("days", 3)} days: '
          + (f'{per100} peeks per 100 words shown ({rate.get("peeks")} peeks, {rate.get("shownWords")} words)'
             if per100 is not None else 'nothing shown yet'))
    print(f'  log: {know.get("rows", 0)} interactions; T3 offset {(know.get("offset") or {}).get("t3", 0)}')
    status = {w['ja']: w['status'] for w in state['words']}
    ranked = sorted(words.items(), key=lambda kv: -kv[1]['n'])[:args.limit]
    if ranked:
        print('  most evidence (word, status, peek chance in the browser, evidence weight):')
    for ja, entry in ranked:
        p = knowledge.sigmoid(-entry['k'])
        print(f'    {ja}  {status.get(ja, "?"):<6} p {p:.2f}  n {entry["n"]:.1f}  opened {entry["opened"]} of {entry["obs"]}')
    moves = know.get('moves') or []
    for m in moves[-5:]:
        print(f'  moved {m["word"]}: {m["from"]} -> {m["to"]} on {m["day"]}')
    if know.get('veto'):
        print(f'  held back from graduating: {", ".join(know["veto"])}')


def why(args):
    state, know = fresh_knowledge()
    w = fwlib.find(state, args.word)
    if w is None:
        sys.exit(f'Not in the word list: {args.word}')
    entry = (know.get('words') or {}).get(w['ja'])
    print(f'{w["ja"]} ({w["en"]}, "{w["hint"]}"): {w["status"]} since {w["changed"] or "never"}')
    if not entry:
        print('  no evidence in the gloss log yet: the day rules decide (3 exposure days without a peek)')
        return
    p = knowledge.sigmoid(-entry['k'])
    print(f'  prior {entry["mu"]}  knowledge {entry["k"]} +- {entry["sd"]}  browser peek chance {p:.2f}; '
          f'opened {entry["opened"]} of {entry["obs"]} observations, {entry["recent"]} in the last 14 days')
    for h in entry['history']:
        when = time.strftime('%Y-%m-%d %H:%M', time.localtime(h['at'] / 1000))
        print(f'    {when}  {h["surface"]:<7} {"opened" if h["y"] else "not opened"}'
              f'{" (glance)" if 0 < h["y"] < 1 else ""}  weight {h["w"]}  -> k {h["k"]}')
    for m in (know.get('moves') or []):
        if m['word'] == w['ja']:
            print(f'  moved {m["from"]} -> {m["to"]} on {m["day"]}')
    if w['ja'] in (know.get('veto') or []):
        print('  held back from graduating: still peeked too often')


def challenge_cmd(args):
    value = float(args.value) if args.value != 'off' else 0
    value = int(value) if value == int(value) else value
    if value not in knowledge.CHALLENGES:
        sys.exit(f'Choose one of: off, {", ".join(str(c) for c in knowledge.CHALLENGES[1:])}')
    queued_note(hub.submit({'kind': 'set', 'prefs': {'challenge': value}}))
    print(f'Adaptive difficulty: {"off" if not value else f"aiming at {value} peeks per 100 words shown"}.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Japanese Weave', formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('setup')
    p.add_argument('--gemini-key-file')
    p.add_argument('--user-data-dir', action='append', default=[])
    p.add_argument('--codex', action='store_true')
    p.add_argument('--known-top', type=int, default=0, metavar='N')
    p.add_argument('--sites', choices=['all', 'ask'])
    p.add_argument('--browsers', default='firefox,chrome', help='human steps to print: firefox, chrome or both')
    p.add_argument('--anki', metavar='COLLECTION', help='also run `anki COLLECTION`')
    p.add_argument('--remote', metavar='SSH_HOST', help='browser-only machine; the word state lives on SSH_HOST')
    p.add_argument('--collector', metavar='URL|off', type=collector_arg,
                   help='send gloss interactions to this collector, e.g. http://functor:7853; off: keep them local')
    p.add_argument('--remote-home', metavar='DIR', help='state dir on SSH_HOST (default ~/.japanese-weave)')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(run=setup)
    p = sub.add_parser('anki')
    p.add_argument('collection', help='collection.anki2 (or a .colpkg / .apkg export)')
    p.add_argument('--deck', help='only decks whose name contains this')
    p.add_argument('--mature-days', type=int, default=anki.MATURE_DAYS)
    p.add_argument('--no-known', action='store_true', help="don't mark mature cards known")
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(run=anki_sync)
    p = sub.add_parser('uninstall')
    p.add_argument('--codex', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(run=uninstall)
    sub.add_parser('status').set_defaults(run=status)
    sub.add_parser('check').set_defaults(run=check)
    for name, kind in (('known', 'known'), ('learn', 'asked')):
        p = sub.add_parser(name)
        p.add_argument('words', nargs='+')
        p.set_defaults(run=mark(kind))
    p = sub.add_parser('stage')
    p.add_argument('value', choices=['1', '2', '3', '4', 'auto'])
    p.set_defaults(run=stage)
    p = sub.add_parser('knowledge')
    p.add_argument('--limit', type=int, default=15)
    p.set_defaults(run=knowledge_cmd)
    p = sub.add_parser('why')
    p.add_argument('word')
    p.set_defaults(run=why)
    p = sub.add_parser('challenge')
    p.add_argument('value', help='off, ' + ', '.join(str(c) for c in knowledge.CHALLENGES[1:]))
    p.set_defaults(run=challenge_cmd)
    p = sub.add_parser('collector')
    p.add_argument('action', choices=['run', 'install'])
    p.add_argument('--listen', required=True, metavar='HOST:PORT')
    p.add_argument('--db', metavar='PATH')
    p.set_defaults(run=collector_cmd)
    p = sub.add_parser('log')
    p.add_argument('limit', nargs='?', type=int, default=20)
    p.set_defaults(run=log_cmd)
    p = sub.add_parser('glosses')
    p.add_argument('style', choices=['abbr', 'footer'])
    p.set_defaults(run=glosses)
    sub.add_parser('pause').set_defaults(run=pause(True))
    sub.add_parser('resume').set_defaults(run=pause(False))
    sub.add_parser('refresh').set_defaults(run=refresh)
    p = sub.add_parser('export')
    target = p.add_mutually_exclusive_group()
    target.add_argument('--claude-ai', action='store_true')
    target.add_argument('--chatgpt', action='store_true')
    p.add_argument('--log-day', action='store_true', help='also count today as exposure (see log-chat-day)')
    p.set_defaults(run=export_cmd)
    sub.add_parser('log-chat-day').set_defaults(run=log_chat_day)
    args = parser.parse_args(argv)
    try:
        args.run(args)
    except fwlib.NoState as error:
        sys.exit(f'Japanese Weave: {error}')


if __name__ == '__main__':
    main()
