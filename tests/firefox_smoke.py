"""Headless Firefox smoke test for the extension, after `fw.py setup`.

Loads extension-firefox as a temporary add-on, syncs words through the native host from its popup page, then gives
it a fake Gemini key and all-sites access and opens a local English page: the content script should find the
passages and the background should call Gemini (which fails: every hostname resolves to 127.0.0.1 in this test).

  python3 -m http.server 8765 --bind 127.0.0.1 --directory <dir with an English index.html> &
  python3 tests/firefox_smoke.py ~ ~/.japanese-weave/extension-firefox "$(command -v firefox)" "$(command -v geckodriver)"

Uses your real word state and native host (HOME is passed to Firefox); the add-on is gone when the run ends.
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

home, ext, firefox, gecko = sys.argv[1:5]
UUID = '11111111-2222-3333-4444-555555555555'
proc = subprocess.Popen([gecko, '--port', '4445', '--allow-system-access'], env={k: v for k, v in dict(os.environ, HOME=home).items() if k != 'LD_LIBRARY_PATH'}, stdout=subprocess.DEVNULL, stderr=open('/tmp/jw-ff/gecko.log', 'w'))
time.sleep(2)

def call(method, path, body=None):
    req = urllib.request.Request('http://127.0.0.1:4445' + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())['value']
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{method} {path}: {e.read().decode()[:600]}')

try:
    caps = {'capabilities': {'alwaysMatch': {'browserName': 'firefox', 'moz:firefoxOptions': {
        'binary': firefox, 'args': ['-headless'],
        'prefs': {'extensions.webextensions.uuids': json.dumps({'japanese-weave@weave.local': UUID}),
                  'devtools.console.stdout.content': True, 'network.dns.forceResolve': '127.0.0.1'}}}}}
    sid = call('POST', '/session', caps)['sessionId']
    print('addon:', call('POST', f'/session/{sid}/moz/addon/install', {'path': ext, 'temporary': True}))
    time.sleep(4)
    before = set(call('GET', f'/session/{sid}/window/handles'))
    call('POST', f'/session/{sid}/moz/context', {'context': 'chrome'})
    call('POST', f'/session/{sid}/execute/sync', {'script': '''
      const win = Services.wm.getMostRecentWindow('navigator:browser');
      win.gBrowser.selectedTab = win.gBrowser.addTab(arguments[0], { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });''',
      'args': [f'moz-extension://{UUID}/src/popup.html']})
    call('POST', f'/session/{sid}/moz/context', {'context': 'content'})
    time.sleep(2)
    new = [h for h in call('GET', f'/session/{sid}/window/handles') if h not in before]
    call('POST', f'/session/{sid}/window', {'handle': new[0]})
    print('page:', call('GET', f'/session/{sid}/url'))
    script = '''const done = arguments[arguments.length - 1];
      (async () => {
        const api = globalThis.browser ?? chrome;
        const sync = await api.runtime.sendMessage({ type: 'syncWords' });
        const status = await api.runtime.sendMessage({ type: 'status' });
        const ping = await api.runtime.sendNativeMessage('com.japaneseweave.host', { type: 'ping' }).catch((e) => String(e));
        done({ sync, words: status.words, stage: status.config.stage, hasKey: status.config.hasKey, ping,
               wordInfo: document.getElementById('wordInfo').textContent, op: document.getElementById('operation').textContent });
      })().catch((e) => done({ error: String(e) }));'''
    print(json.dumps(call('POST', f'/session/{sid}/execute/async', {'script': script, 'args': []}), ensure_ascii=False, indent=1))
    # Content script: fake key, all-sites grant, then a plain English page.
    call('POST', f'/session/{sid}/execute/async', {'script': '''const done = arguments[arguments.length - 1];
      (globalThis.browser ?? chrome).runtime.sendMessage({ type: 'save', apiKey: 'AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE00' }).then(done, (e) => done(String(e)));''', 'args': []})
    call('POST', f'/session/{sid}/moz/context', {'context': 'chrome'})
    print('grant:', call('POST', f'/session/{sid}/execute/async', {'script': '''const done = arguments[arguments.length - 1];
      const { ExtensionPermissions } = ChromeUtils.importESModule('resource://gre/modules/ExtensionPermissions.sys.mjs');
      const policy = WebExtensionPolicy.getByID('japanese-weave@weave.local');
      ExtensionPermissions.add(policy.id, { permissions: [], origins: ['http://*/*', 'https://*/*'] }, policy.extension)
        .then(() => done('ok'), (e) => done(String(e)));''', 'args': []}))
    call('POST', f'/session/{sid}/moz/context', {'context': 'content'})
    time.sleep(2)
    call('POST', f'/session/{sid}/window', {'handle': sorted(before)[0]})
    call('POST', f'/session/{sid}/url', {'url': 'http://weave-smoke.example:8765/'})
    time.sleep(12)
    print('page text:', call('POST', f'/session/{sid}/execute/sync', {'script': 'return document.querySelector("p").textContent', 'args': []}))
    call('POST', f'/session/{sid}/window', {'handle': new[0]})
    print(json.dumps(call('POST', f'/session/{sid}/execute/async', {'script': '''const done = arguments[arguments.length - 1];
      (globalThis.browser ?? chrome).runtime.sendMessage({ type: 'status' }).then((s) => done({ hasKey: s.config.hasKey, origins: s.config.origins, modelStatus: s.modelStatus }));''', 'args': []}), indent=1))
    call('DELETE', f'/session/{sid}')
finally:
    proc.terminate()
