// A stand-in for the model's check (verify.js) in the worker tests: it reads the user message the way the model would,
// passage by passage with its numbered spans, and answers each with picks chosen by pick(english, options) (an option
// index, or null for none) and any swaps swaps(passage) gives ([{ at, p }]).

// The passages of a check request's user message: [{ id, text, spans: [{ n, english, options: [ja], sure }], sentences }].
export function passagesOf(user) {
  return String(user).split('\n\n').map((block) => {
    const [head, ...lines] = block.split('\n');
    const [, id, text] = /^\[([^\]]+)\] (.*)$/.exec(head);
    const spans = [];
    const sentences = [];
    for (const line of lines) {
      const sentence = /^s(\d+) "(.*)"$/.exec(line);
      if (sentence) { sentences.push({ n: Number(sentence[1]), text: sentence[2] }); continue; }
      const n = Number(/^(\d+) /.exec(line)[1]);
      const english = /\[([^\]]+)\]/.exec(line)[1];
      const at = line.lastIndexOf(': a ');
      if (at < 0) spans.push({ n, english, options: [line.slice(line.lastIndexOf(' = ') + 3)], sure: true });
      else spans.push({ n, english, options: line.slice(at + 2).split(/; (?=[a-z] )/).map((o) => o.slice(2, o.indexOf(' = '))), sure: false });
    }
    return { id, text, spans, sentences };
  });
}

// The answer's JSON text for a check request's user message.
export function checkAnswer(user, { pick = () => 0, swaps = () => [] } = {}) {
  return JSON.stringify({ passages: passagesOf(user).map((p) => ({ id: p.id,
    picks: p.spans.filter((s) => !s.sure).map((s) => { const k = pick(s.english, s.options, p); return `${s.n}${k === null ? '-' : 'abcdefgh'[k]}`; }).join(' '),
    swaps: swaps(p) })) });
}

// A fetch reply from either provider with that text.
export function modelReply(url, text, { input = 3500, cached = 3000, written = 200, output = 400 } = {}) {
  if (String(url).includes('anthropic')) {
    return { ok: true, status: 200, json: async () => ({ content: [{ type: 'text', text }], stop_reason: 'end_turn',
      usage: { input_tokens: input - cached - written, cache_read_input_tokens: cached, cache_creation_input_tokens: written, output_tokens: output } }) };
  }
  return { ok: true, status: 200, json: async () => ({ candidates: [{ content: { parts: [{ text }] } }],
    usageMetadata: { promptTokenCount: input, candidatesTokenCount: output } }) };
}

// The user message of a fetch to either provider.
export const userOf = (url, init) => {
  const body = JSON.parse(init.body);
  return String(url).includes('anthropic') ? body.messages[0].content : body.contents[0].parts[0].text;
};
