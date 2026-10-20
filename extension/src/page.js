import { isZeroJapanese, wordCount } from './levels.js';
import { flatten } from './text.js';

export const TWEET = '[data-testid="tweetText"]';
export const PROSE = 'p,li,blockquote,h1,h2,h3,h4,article,main';
const BLOCKED = 'a,button,input,textarea,select,form,nav,header,footer,code,pre,kbd,samp,script,style,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="button"],[hidden],[aria-hidden="true"],[translate="no"],.notranslate';
// Inside a passage these mean typing or text the reader can't see, so the passage is left alone (unless the site's
// skip covers them: then they are no part of its text).
const UNSEEN = 'input,textarea,select,form,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[hidden],[aria-hidden="true"],script,style';
const within = (scopes, parts) => scopes.flatMap((scope) => parts.map((part) => `${scope} ${part}`)).join(',');

// Google results pages (/search on google.com and the country domains). Their prose is plain divs and spans with class
// names that change, so passages are picked by the attributes Google keeps (a snippet's [data-sncf] container, a
// knowledge panel's [data-attrid], the [data-subtree="aimc"] around AI answers) with long-lived class names as a second
// way in, and only in the results (#rcnt). A result's title is a heading inside its link, its URL a cite, the chips
// under it links and buttons: none of that matches, so when Google changes its markup nothing is woven rather than the
// page's chrome. A snippet's date, an AI answer's citation chips and a hidden heading are no part of the text; a part of
// an AI answer still hidden (collapsed, or fading in) holds its passage back until it shows. Ads are never woven.
const GOOGLE_HOST = /^(www\.)?google\.(com|[a-z]{2}|co\.[a-z]{2}|com\.[a-z]{2})$/;
export const GOOGLE = within(['#rcnt'], [
  '[data-sncf="1"] > div', '.VwiC3b', // a result's snippet (its container's attribute, its class; "2" is forum threads)
  '.hgKElc', '[data-attrid="wa:/description"]', // a featured snippet
  '.kno-rdesc', '[data-attrid="description"]', // the knowledge panel's description
  '[data-subtree="aimc"] li', '[data-subtree="aimc"] .n6owBd', // an AI answer's list items and paragraphs
]);
const GOOGLE_ADS = '#tads,#tadsb,#bottomads,[data-text-ad]';
const GOOGLE_SKIP = '[translate="no"],.notranslate,button,[role="button"],h1,h2,h3,h4,[role="heading"],cite,.YrbPuc';

// YouTube (www and m): comments and community posts (#content-text), and the video description, collapsed or expanded.
// The description is an attributed string whose text runs between its links; when it has runs each run is a passage,
// so a long description still gets Japanese (a passage is at most 2000 characters) and its timestamps and links stay
// out. Titles, channel names and chapter titles are links. Live chat runs in a frame of its own, where content scripts
// don't run. On m.youtube.com each comment is a button with its text hidden from screen readers, so only the
// description (its panel) is woven there.
const YOUTUBE_HOST = /^(www\.|m\.)?youtube\.com$/;
const DESCRIPTION = ['#description-inline-expander #snippet', '#description-inline-expander #expanded',
  'ytm-expandable-video-description-body-renderer collapsible-string'];
export const YOUTUBE = ['#content-text', within(DESCRIPTION, ['yt-attributed-string', 'yt-attributed-string > span',
  'yt-attributed-string > span > span', '.ytAttributedStringHost', '.ytAttributedStringHost > span'])].join(',');

// Per site: the passages, what else never counts, the parts of a passage that are no part of its text, and the
// attributes whose change can show a passage (watched, so an answer opened later is woven then).
const SITES = [
  { test: (u) => /^(www\.)?(x\.com|twitter\.com)$/.test(u.hostname), passages: TWEET },
  { test: (u) => GOOGLE_HOST.test(u.hostname) && u.pathname === '/search', passages: GOOGLE, blocked: GOOGLE_ADS,
    skip: GOOGLE_SKIP, reveal: ['hidden', 'aria-hidden'] },
  { test: (u) => YOUTUBE_HOST.test(u.hostname), passages: YOUTUBE, reveal: ['hidden', 'aria-hidden'] },
];
const DEFAULT = { passages: PROSE };
export function site(url) {
  let u;
  try { u = new URL(url); } catch { return DEFAULT; }
  return SITES.find((s) => s.test(u)) || DEFAULT;
}
export function selector(url) { return site(url).passages; }
// Parts of a passage left out of its text: not sent to the model and never woven ('' for none).
export function skipped(url) { return site(url).skip || ''; }
// Attributes to watch for passages shown later, or [] for none.
export function revealing(url) { return site(url).reveal || []; }

const hiddenPart = (node, el, skip) => {
  const part = skip && node.closest(skip);
  return !!part && part !== el && el.contains(part);
};
export function readable(el, url) {
  const { passages, blocked, skip } = site(url);
  if (!el.isConnected || el.closest(BLOCKED) || (blocked && el.closest(blocked))) return false;
  if ([...el.querySelectorAll(UNSEEN)].some((node) => !hiddenPart(node, el, skip))) return false;
  if (/\/(?:login|signin|sign-in|checkout|payment|billing|settings|account)(?:\/|$)/i.test(new URL(url).pathname)) return false;
  const lang = el.closest('[lang]')?.getAttribute('lang') || '';
  if (lang && !/^(en(?:-|$)|und$|qme$|zxx$|art$)/i.test(lang)) return false;
  if (el.querySelector(passages)) return false; // the inner one is the passage
  const text = flatten(el, skip).text;
  return text.length <= 2000 && wordCount(text) >= 6 && !isZeroJapanese(text);
}
