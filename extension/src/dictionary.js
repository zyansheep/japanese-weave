// The dictionary (data/dictionary.tsv, built by tools/build_dictionary.py; docs/dictionary.md): every common JMdict
// entry and the others ranked as well as a typical common one, one per line in rank order. parse() gives the entries
// as scripts/dictionary.py does: id (JMdict sequence number), ja, kana, romaji, en, glosses ([[gloss, ...] per
// sense]), pos, score, jpdb, bccwj, common (0 for an entry kept for its rank alone), loan, swap, forms, rank (1 is the
// most frequent) and kanji (of the written form).
export const FIELDS = ['id', 'ja', 'kana', 'romaji', 'en', 'glosses', 'pos', 'score', 'jpdb', 'bccwj', 'common', 'loan',
  'swap', 'forms'];
const NUMBERS = ['id', 'score', 'jpdb', 'bccwj', 'common'];
const KANJI = /[㐀-䶿一-鿿豈-﫿]/g;

export function entry(cells, rank) {
  const e = {};
  FIELDS.forEach((field, i) => { e[field] = cells[i] ?? ''; });
  for (const field of NUMBERS) e[field] = e[field] ? Number(e[field]) : null;
  e.common = e.common || 0;
  e.glosses = e.glosses ? e.glosses.split('|').map((sense) => sense.split(';')) : [];
  e.pos = e.pos ? e.pos.split(',') : [];
  e.forms = e.forms ? e.forms.split(',') : [];
  e.swap = e.swap === '1';
  e.rank = rank;
  e.kanji = [...new Set(e.ja.match(KANJI) || [])];
  return e;
}

// The file's text -> entries in rank order. Comment lines (#) come first, then the header row.
export function parse(text) {
  const lines = text.split('\n');
  let start = 0;
  while (start < lines.length && lines[start].startsWith('#')) start++;
  const header = (lines[start] || '').split('\t');
  if (header.join('\t') !== FIELDS.join('\t')) throw new Error(`dictionary fields ${header}, expected ${FIELDS}`);
  const entries = [];
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i]) entries.push(entry(lines[i].split('\t'), entries.length + 1));
  }
  return entries;
}

// From the extension package: load(chrome.runtime.getURL('data/dictionary.tsv')).
export async function load(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`dictionary: ${response.status} ${url}`);
  return parse(await response.text());
}
