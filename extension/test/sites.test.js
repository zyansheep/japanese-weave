import { describe, expect, test, afterAll } from 'bun:test';
import { load, fixture } from './fixtures.js';
import { selector, readable, skipped, revealing, site, GOOGLE, YOUTUBE, PROSE, TWEET } from '../src/page.js';
import { flatten } from '../src/text.js';

// Which elements page.js picks as passages on pages captured from Google and YouTube (test/fixtures), and which it leaves
// alone: titles, links, chips, buttons, channel names, dates, ads.
const windows = [];
afterAll(async () => { for (const win of windows) await win.happyDOM.close(); });
function page(name, url) {
  const win = load(name, url);
  windows.push(win);
  return win.document;
}
// The passages on the page, as the text the model would get.
const picked = (doc, url) => [...doc.querySelectorAll(selector(url))].filter((el) => readable(el, url));
const texts = (doc, url) => picked(doc, url).map((el) => flatten(el, skipped(url)).text);
const starts = (list) => list.map((t) => t.slice(0, 40));

describe('which sites have their own passages', () => {
  test('Google: the results path on google.com and the country domains; YouTube: www and m', () => {
    for (const url of ['https://www.google.com/search?q=x', 'https://google.com/search?q=x', 'https://www.google.co.uk/search?q=x',
      'https://www.google.de/search?q=x', 'https://www.google.com.au/search?q=x', 'https://www.google.co.jp/search?q=x']) {
      expect(selector(url)).toBe(GOOGLE);
    }
    for (const url of ['https://www.google.com/', 'https://www.google.com/maps/place/x', 'https://news.google.com/search?q=x',
      'https://www.google.example.com/search?q=x', 'https://notgoogle.com/search?q=x', 'https://www.googleblog.com/search']) {
      expect(selector(url)).toBe(PROSE);
    }
    expect(selector('https://www.youtube.com/watch?v=x')).toBe(YOUTUBE);
    expect(selector('https://m.youtube.com/watch?v=x')).toBe(YOUTUBE);
    expect(selector('https://youtube.com/@someone/posts')).toBe(YOUTUBE);
    expect(selector('https://www.youtube-nocookie.com/embed/x')).toBe(PROSE);
    expect(selector('https://x.com/home')).toBe(TWEET);
    expect(selector('https://example.com/')).toBe(PROSE);
    expect(site('not a url')).toEqual({ passages: PROSE });
    expect(skipped('https://example.com/')).toBe('');
    expect(revealing('https://example.com/')).toEqual([]);
    expect(revealing('https://www.google.com/search?q=x')).toEqual(['hidden', 'aria-hidden']);
  });
});

describe('Google results', () => {
  const url = 'https://www.google.com/search?q=how+do+rainbows+form';
  const doc = page('google-search.html', url);

  test('snippets, the AI overview and an opened People also ask answer; their text without dates or chips', () => {
    expect(starts(texts(doc, url))).toEqual([
      'A rainbow is formed when sunlight passes', // the AI overview, as far as it shows before "Show more"
      'Refraction (Bending): White sunlight ent',
      'Light enters a water droplet, slowing do', // the first result's snippet
      'Double rainbows are not extremely rare; ', // People also ask: the second answer, in the page (shown when opened)
      'How They Form: They happen when sunlight',
      'The rainbow we normally see is called th',
      'When sunlight goes through a water dropl',
    ]);
    // A forum result's second part (its top answer, cut short, or its other threads with post counts and dates) is not.
    expect(readable(doc.querySelector('[data-sncf="2"] > div'), url) && doc.querySelector('[data-sncf="2"] > div').matches(selector(url))).toBe(false);
    const all = texts(doc, url).join('\n');
    expect(all).not.toContain('Sep 2, 2025'); // the snippet's date
    expect(all).not.toContain('Rocio Carreon'); // an AI answer's citation chip
    expect(all).not.toContain('National Oceanic and Atmospheric Administration (.gov)'); // a source card
  });

  test('never a title, URL, link, question, chip, button or the search box', () => {
    for (const el of picked(doc, url)) {
      expect(el.closest('a,h1,h2,h3,[role="heading"],cite,button,[role="button"],form,[aria-hidden="true"],#tads,#bres')).toBeNull();
    }
    const unpicked = (sel) => [...doc.querySelectorAll(sel)].filter((el) => el.closest(selector(url)) && readable(el.closest(selector(url)), url));
    expect(doc.querySelectorAll('h3.LC20lb').length).toBeGreaterThan(2); // result titles are there
    expect(unpicked('h3.LC20lb, cite, [data-q], #bres a, #top_nav *, form *, [role="listitem"] a')).toEqual([]);
    expect(doc.querySelector('#bres').textContent).toContain('People also search for');
  });

  test('a part of an AI answer is picked once it shows, and a fading-in paragraph once it is whole', () => {
    const hidden = doc.querySelector('#rcnt li[aria-hidden="true"]');
    const fading = [...doc.querySelectorAll('#rcnt .n6owBd')].find((el) => el.textContent.startsWith('A rainbow forms'));
    expect(readable(hidden, url)).toBe(false);
    expect(readable(fading, url)).toBe(false);
    hidden.removeAttribute('aria-hidden');
    for (const strong of fading.querySelectorAll('strong[aria-hidden]')) strong.removeAttribute('aria-hidden');
    expect(readable(hidden, url)).toBe(true);
    expect(readable(fading, url)).toBe(true);
    expect(flatten(fading, skipped(url)).text).toBe('A rainbow forms when sunlight passes through water droplets in the air and undergoes three steps: refraction, reflection, and dispersion.');
    hidden.setAttribute('aria-hidden', 'true');
    for (const strong of fading.querySelectorAll('strong')) strong.setAttribute('aria-hidden', 'true');
  });

  test('ads are never picked, even with a snippet inside', () => {
    doc.querySelector('#rcnt').insertAdjacentHTML('afterbegin', `<div id="tads"><div data-text-ad="1"><span>Sponsored</span>
      <div class="VwiC3b">Buy the best rainbow prisms online today with free shipping on every order.</div></div></div>`);
    const ad = doc.querySelector('#tads .VwiC3b');
    expect(ad.matches(selector(url))).toBe(true);
    expect(readable(ad, url)).toBe(false);
    doc.querySelector('#tads').remove();
  });

  test('outside the results nothing counts, and a German result is left alone', () => {
    doc.body.insertAdjacentHTML('beforeend', '<div><div class="VwiC3b">Some text that is long enough to be a passage of its own.</div></div>');
    expect(doc.body.lastElementChild.firstElementChild.matches(selector(url))).toBe(false);
    doc.body.lastElementChild.remove();
    const snippet = picked(doc, url).find((el) => el.matches('.VwiC3b'));
    snippet.closest('[lang]').setAttribute('lang', 'de');
    expect(readable(snippet, url)).toBe(false);
    snippet.closest('[lang]').setAttribute('lang', 'en');
  });

  test('the knowledge panel description, without its heading or source link; snippets around it', () => {
    const kp = 'https://www.google.com/search?q=marie+curie';
    const kpDoc = page('google-knowledge.html', kp);
    expect(starts(texts(kpDoc, kp))).toEqual([
      'Curie was the first woman to win a Nobel',
      'For her research in “radiation phenomena',
      'Marie Curie, a Polish-born French physic',
      'Maria Salomea Skłodowska Curie, better k',
    ]);
    const description = picked(kpDoc, kp).at(-1);
    expect(description.matches('.kno-rdesc')).toBe(true); // the inner one, not [data-attrid="description"] around it
    const text = flatten(description, skipped(kp)).text;
    expect(text).not.toMatch(/^Description/);
    expect(text).toMatch(/Becquerel"\. Wikipedia$/); // the source link's text stays as context, never woven
    expect(picked(kpDoc, kp).some((el) => el.closest('#rhs [data-attrid^="kc:"]'))).toBe(false); // facts: born, spouse...
  });
});

describe('YouTube', () => {
  const url = 'https://www.youtube.com/watch?v=24GfgNtnjXc';

  test('a video page: the collapsed description and the comments, nothing else', () => {
    const doc = page('youtube-watch.html', url);
    expect(starts(texts(doc, url))).toEqual([
      'You probably don’t understand how a rain', // the description's first run of text, up to its link
      'to get 20% off your annual premium subsc',
      '- Dad... why are rainbows curved? - Give',
      'The rate of Veritasium and Mentour Pilot',
      'Just beautiful, happy to be able to help',
    ]);
    const all = texts(doc, url).join('\n');
    expect(all).not.toContain('brilliant.org'); // the link between the runs
    // Not the title, the channel, buttons, commenters, chapter titles or other videos.
    expect(doc.querySelector('ytd-watch-metadata h1').textContent).toContain('Rainbows At School');
    for (const el of picked(doc, url)) {
      expect(el.closest('h1,a,button,ytd-channel-name,#author-text,ytd-watch-next-secondary-results-renderer,#masthead-container')).toBeNull();
      expect(flatten(el).text).not.toMatch(/@commenter|Veritasium\s*$|Subscribe/);
    }
    // A description with no links is one passage; short comments (under 6 words) are left alone.
    const snippet = doc.querySelector('#attributed-snippet-text > span');
    snippet.innerHTML = 'Rainbows are circles of light, and this video shows you why.';
    expect(picked(doc, url)[0]).toBe(snippet);
    const comment = doc.querySelector('#content-text > span');
    comment.textContent = 'Great video!';
    expect(readable(comment.parentElement, url)).toBe(false);
  });

  test('the expanded description, run by run, timestamps and links left out; the hidden snippet not', () => {
    const doc = page('youtube-watch.html', url);
    doc.querySelector('#description-inline-expander').outerHTML = fixture('youtube-description-expanded.html').replace(/^<!--[^]*?-->\n/, '');
    const list = texts(doc, url);
    expect(list).toHaveLength(13);
    expect(list.slice(0, 4)).toEqual([
      'You probably don’t understand how a rainbow really works. Get a little smarter every day with Brilliant. Visit',
      'to get 20% off your annual premium subscription. Special thanks to Mats Vermeeren.',
      'Why can’t you see a rainbow with sunglasses?',
      'Why is it brighter underneath a rainbow?',
    ]);
    expect(list.join('\n')).not.toMatch(/\d\d:\d\d|https:/);
    // The whole description is over 2000 characters: as one passage it would get nothing.
    expect(flatten(doc.querySelector('#description-inline-expander #expanded yt-attributed-string')).text.length).toBeGreaterThan(2000);
    expect(doc.querySelector('#snippet-text').hasAttribute('hidden')).toBe(true);
  });

  test('community posts', () => {
    const posts = 'https://www.youtube.com/@veritasium/posts';
    const doc = page('youtube-posts.html', posts);
    expect(starts(texts(doc, posts))).toEqual(['Is spider web really stronger than steel', 'Happy World Quantum Day! Here\'s a look b']);
    expect(texts(doc, posts).join(' ')).not.toContain('An element of truth'); // the channel's own blurb is page chrome
  });

  test('m.youtube.com: the description panel; comments are buttons there, and the crawler copy is not shown', () => {
    const mobile = 'https://m.youtube.com/watch?v=24GfgNtnjXc';
    const doc = page('m-youtube-watch.html', mobile);
    const list = picked(doc, mobile);
    expect(list).toHaveLength(10);
    for (const el of list) expect(el.closest('ytm-expandable-video-description-body-renderer')).not.toBeNull();
    expect(doc.querySelectorAll('.YtmCommentRendererText').length).toBe(3);
    expect(doc.querySelector('ytm-crawler-description').textContent).toContain('You probably'); // for search engines
    expect(doc.querySelector('ytm-crawler-description .ytAttributedStringHost').matches(selector(mobile))).toBe(false);
  });

  test('live chat is not woven (its frame gets no content script, and its messages match nothing)', () => {
    const doc = page('youtube-watch.html', url);
    doc.body.insertAdjacentHTML('beforeend', `<yt-live-chat-text-message-renderer><div id="content"><span id="author-name">viewer</span>
      <span id="message">This stream is really great and I am learning so much today</span></div></yt-live-chat-text-message-renderer>`);
    expect(doc.querySelector('#message').closest(selector(url))).toBeNull();
  });
});
