"""核对演练资料的完整性及关键的输入关系。"""
from hashlib import sha256
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent / 'records'


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / 'catalog.json').read_bytes())
        self.records = {}
        for name, expected in self.catalog.items():
            raw = (ROOT / (name + '.jsonl')).read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), expected['sha256'])
            rows = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(len(rows), expected['rows'])
            self.assertEqual(len({row['id'] for row in rows}), len(rows))
            self.records[name] = rows

    def test_business_inputs(self):
        d = self.records
        common = {(x['case'],x['item_fact']) for x in d['items'] if x['form']=='common'}
        self.assertTrue(any((x['case'],x['fact']) in common and x['actual'] and x['received'] > d['notices'][0]['as_of'] for x in d['disclosures']))
        self.assertTrue(any(x['actual'] is None and x['scheduled'] < d['forms'][0]['sit'] for x in d['disclosures']))
        self.assertEqual({x['region'] for x in d['forms']},{'east','west'})
