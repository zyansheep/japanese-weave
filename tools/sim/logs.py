"""A simulated reader's days (world.py) written into a state dir as the live system would write them: the gloss log
(interactions.sqlite, through feedback.record, so the collector's own checks and schema apply), state.json and
anki.csv; and the ground truth beside them in truth.jsonl, one line per annotation shown: {surface, view, context,
word (its entry, or as written for a word off the list), index, kind (word, offlist or sentence), q (its true P(lookup
| read)), read (its true read chance), was_read, looked, status (known, learning or new), form}. Python 3.9+, standard
library only."""
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import feedback  # noqa: E402
import fwlib  # noqa: E402

TRUTH = 'truth.jsonl'
BATCH = feedback.MAX_ITEMS


class Writer:
    """Collects a world's showings into a state dir: add() each day's, finish() at the end."""

    def __init__(self, home, world):
        self.home, self.world = Path(home), world
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = self.home / feedback.DB_NAME
        self.truth = open(self.home / TRUTH, 'w', encoding='utf-8')
        self.uid = 0

    def _uid(self):
        self.uid += 1
        return f'sim{self.uid}'

    def items(self, s):
        """The extension's or T3's items for one showing: its shown record, its stays on screen, its opens."""
        base = {'view': s.view, 'context': s.context, 'box': [0, 0, 600, s.height],
                'annotations': [[m.written, m.x, m.y] for m in s.marks]}
        shown = dict(base, kind='shown', at=s.at, words=s.words)
        out = [dict(shown, plan=s.plan) if s.plan else shown]
        stays = s.stays if s.surface == 't3' else ([{'y': 0, 'height': s.height, 'ms': s.active}] if s.active else [])
        for i, stay in enumerate(stays):
            left = stay['ms']
            while left > 0:  # a stay is at most an hour
                ms = min(left, feedback.MAX_VISIT_MS - 1000)
                item = dict(base, kind='visible', at=s.at + 500 + i, y=stay['y'], height=stay['height'], ms=ms,
                            wall=ms + 500, away=500, media=0, idle=0, uid=self._uid())
                out.append(dict(item, words=stay['words']) if stay.get('words') else item)
                left -= ms
        for i, m in enumerate(s.marks):
            if m.looked:
                out.append(dict(base, kind='peek', at=s.at + 2000 + 1500 * i, word=m.written, position=i,
                                dwellMs=m.dwell, uid=self._uid()))
        if s.noise is not None:
            m = s.marks[s.noise]
            out.append(dict(base, kind='peek', at=s.at + 1000, word=m.written, position=s.noise,
                            dwellMs=self.world.rng.randint(80, 280), uid=self._uid()))
        return out

    def add(self, showings):
        batches = {'browser': [], 't3': []}
        for s in showings:
            batches[s.surface] += self.items(s)
            for i, m in enumerate(s.marks):
                word = m.word.ja if m.word is not None else m.written
                self.truth.write(json.dumps({
                    'surface': s.surface, 'view': s.view, 'context': s.context, 'word': word, 'index': i,
                    'kind': m.kind, 'q': round(m.q, 6), 'read': round(m.read, 6), 'was_read': m.was_read,
                    'looked': m.looked, 'status': m.status, 'form': m.form, 'at': s.at}, ensure_ascii=False) + '\n')
        for surface, items in batches.items():
            items.sort(key=lambda item: item['at'])
            for i in range(0, len(items), BATCH):
                chunk = items[i:i + BATCH]
                result = feedback.record(chunk, surface, self.db, now=chunk[-1]['at'] + 1000)
                if result['rejected']:
                    raise ValueError(f'the collector rejected {result["rejected"]} simulated items')

    def finish(self):
        """state.json and anki.csv as the simulation ends."""
        self.truth.close()
        fwlib.write_json(self.home / 'state.json', self.world.state())
        with open(self.home / fwlib.ANKI_LEXICON, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['ja', 'en', 'hint', 'kana', 'interval'])
            writer.writeheader()
            writer.writerows(self.world.anki_rows())


def simulate(home, days, checkpoints=(), **cfg):
    """A world simulated `days` days into the state dir `home`. Returns (world, {day: snapshot}): at each checkpoint
    day d, every word's truth at 08:59 the morning after day d (World.snapshot)."""
    from world import World, HOUR, MINUTE
    world = World(**cfg)
    writer = Writer(home, world)
    snapshots = {}
    for day in range(days):
        writer.add(world.simulate_day())
        if day + 1 in checkpoints:
            at = world.time() + 9 * HOUR - MINUTE
            snapshots[day + 1] = (at, world.snapshot(at))
    writer.finish()
    return world, snapshots
