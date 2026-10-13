import json
import os
import tempfile
import unittest
from unittest.mock import patch
import collector

class CollectorFoundationTests(unittest.TestCase):
    def test_event_id_is_stable(self):
        self.assertEqual(collector.event_id('大阪城ホール','YURIN LIVE','2026-10-27'),
                         collector.event_id('大阪城ホール','YURIN  LIVE','2026-10-27'))
    def test_cancellation_requires_explicit_word(self):
        self.assertEqual(collector.explicit_status('LIVE'), 'scheduled')
        self.assertEqual(collector.explicit_status('公演中止'), 'cancelled')
    def test_month_parser_no_upcoming_highlights(self):
        source={'name':'大阪城ホール公式','venue':'大阪城ホール'}
        page='<h2>2026年10月のイベント</h2><p>27（火） YURIN LIVE Vol.4 GUEST：コブクロ 開演 19:00</p>'
        with patch.object(collector,'TODAY',__import__('datetime').date(2026,10,13)):
            rows=collector.johall(page,source,'https://www.osaka-johall.com/event/?ym=202610')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['start'],'2026-10-27')
    def test_atomic_json_output(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(collector,'DATA',temp):
                collector.write_json('test.json',{'value':'正常'})
                with open(os.path.join(temp,'test.json'),encoding='utf-8') as f:
                    self.assertEqual(json.load(f)['value'],'正常')
    def test_source_config_is_valid(self):
        with open('sources.json',encoding='utf-8') as f: sources=json.load(f)
        self.assertIsInstance(sources,list)
        self.assertGreater(len(sources),0)
        for s in sources:
            self.assertTrue(s.get('url','').startswith('https://'))

if __name__=='__main__': unittest.main()
