import { describe, test, expect } from 'bun:test';
import { englishForms, wordHints } from '../src/hints.js';

describe('word hints', () => {
  const words = [
    { ja: 'ある', en: 'have / exist', status: 'shaky' },
    { ja: 'なる', en: 'become / result in', status: 'shaky' },
    { ja: '男', en: 'man / male', status: 'shaky' },
    { ja: '違う', en: 'differ / be wrong', status: 'shaky' },
    { ja: 'について', en: 'concerning / regarding', status: 'shaky' },
    { ja: '休む', en: 'rest / take time off', status: 'active' },
    { ja: '一つ', en: 'one', status: 'shaky' },
    { ja: '可愛い', en: 'cute', status: 'shaky' },
    { ja: '止める', en: 'stop', status: 'known' },
    { ja: 'そして', en: 'and then / and', status: 'known' },
    { ja: 'コンピュータ', en: 'computer', status: 'known' },
    { ja: '夢', en: 'dream', status: 'known' },
  ];
  const found = (text) => wordHints(text, words).map((h) => `${h.ja}:${h.found}${h.learning ? '' : ' (known)'}`);
  test('English forms: plural, past, -ing, comparative, irregular', () => {
    expect(englishForms('stop')).toEqual(expect.arrayContaining(['stops', 'stopped', 'stopping']));
    expect(englishForms('cute')).toEqual(expect.arrayContaining(['cuter', 'cutest', 'cuted']));
    expect(englishForms('become')).toEqual(['become', 'became', 'becomes', 'becoming']);
    expect(englishForms('man')).toEqual(['man', 'men']);
  });
  test('learning and known words whose English the passage says, as written there', () => {
    expect(found('Two men became friends and stopped the dreams.')).toEqual(['なる:became', '男:men', '止める:stopped (known)', '夢:dreams (known)']);
    expect(found('That is the cutest one. We took time off.')).toEqual(['休む:took time off', '可愛い:cutest']); // no number words
    expect(found('We are different, and I was wrong about it.')).toEqual(['違う:different', 'について:about']);
    expect(found('There is a computer.')).toEqual(['ある:There is']); // no katakana loanwords
  });
  test('every spelling found, first of each whatever the case, and how many places', () => {
    const [stop] = wordHints('Stop it. They stopped, then stop again.', words);
    expect(stop).toMatchObject({ ja: '止める', found: 'Stop', forms: ['Stop', 'stopped'], places: 3 });
    expect(wordHints('I have been here, but I had a dog.', words)[0]).toMatchObject({ ja: 'ある', forms: ['had'], places: 1 }); // not the helper
  });
  test('"have" as a helper verb is no lead for ある; vague English is no lead for a known word', () => {
    expect(found('I have been there and had to go.')).toEqual([]);
    expect(found('I have never seen it, but I had a dog.')).toEqual(['ある:had']);
    expect(found('Fish and tea.')).toEqual([]);
    expect(wordHints('A man.', [{ ja: '男', en: 'man' }])).toEqual([]); // a word without a status
  });
});
