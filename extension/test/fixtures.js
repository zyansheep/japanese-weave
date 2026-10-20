// Pages captured for the site tests (test/fixtures/*.html; each says where it came from), loaded in happy-dom.
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

export const fixture = (name) => readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8');

// happy-dom matches the ancestors in el.querySelectorAll('#a .b') only inside el; browsers (and the DOM standard) match
// them anywhere in the document, which page.js's site selectors rely on. This makes happy-dom do the same.
export function standardQueries(win) {
  const proto = win.Element.prototype;
  if (proto.querySelectorAll.standard) return;
  const own = proto.querySelectorAll;
  const all = function querySelectorAll(selectors) {
    const root = this.getRootNode();
    if (root.nodeType !== 9 && root.nodeType !== 11) return [...own.call(this, selectors)]; // not in a document
    return [...root.querySelectorAll(selectors)].filter((el) => el !== this && this.contains(el));
  };
  all.standard = true;
  proto.querySelectorAll = all;
  proto.querySelector = function querySelector(selectors) { return all.call(this, selectors)[0] ?? null; };
}

// A fixture as the page at url, scripts off.
export function load(name, url) {
  const win = new Window({ url, settings: { disableJavaScriptEvaluation: true, disableJavaScriptFileLoading: true,
    disableCSSFileLoading: true, disableIframePageLoading: true, disableComputedStyleRendering: true } });
  standardQueries(win);
  win.document.write(fixture(name));
  return win;
}
