import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from output_rates import CCSwitchRateReader, best_sample, codex_request_sample, rate_info, request_sample
from server import Collector, CodexAdapter


class RequestRateTests(unittest.TestCase):
    def test_cc_switch_proxy_and_session_definitions_differ(self):
        exact = request_sample(900, 10000, 100, 'cc-switch', estimated=False, first_token_ms=1000)
        estimate = request_sample(900, 10000, 100, 'cc-switch', estimated=True, first_token_ms=1000)
        self.assertEqual(exact['value'], 100)
        self.assertEqual(exact['windowMs'], 9000)
        self.assertFalse(exact['includesFirstTokenWait'])
        self.assertEqual(estimate['value'], 90)
        self.assertTrue(estimate['includesFirstTokenWait'])
        self.assertIsNone(request_sample(900, 10000, 100, 'cc-switch', estimated=False))
        self.assertIsNone(request_sample(50, 1000, 100, 'cc-switch', estimated=False, first_token_ms=500))
        self.assertIsNone(request_sample(900, 1000, 100, 'cc-switch', estimated=False, first_token_ms=999))
        self.assertIsNone(request_sample(199, 10000, 100, 'codex-log', estimated=True))
        self.assertIsNone(request_sample(900, None, 100, 'codex-log', estimated=True))
        self.assertIsNone(request_sample(None, 10000, 100, 'codex-log', estimated=True))

    def test_modern_usage_uses_request_boundary_not_two_second_poll(self):
        records = [
            {'timestamp': 100, 'type': 'event_msg', 'payload': {'type': 'task_started'}},
            {'timestamp': 109, 'type': 'response_item', 'payload': {'type': 'reasoning'}},
            {'timestamp': 110, 'type': 'token_usage_record', 'payload': {
                'response_id': 'first', 'usage': {'output_tokens': 200}, 'thread_token_usage': {'output_tokens': 200}}},
            {'timestamp': 110.001, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                'last_token_usage': {'output_tokens': 200}, 'total_token_usage': {'output_tokens': 200}}}},
            {'timestamp': 115, 'type': 'response_item', 'payload': {'type': 'function_call_output'}},
            {'timestamp': 124, 'type': 'response_item', 'payload': {'type': 'reasoning'}},
            {'timestamp': 125, 'type': 'token_usage_record', 'payload': {
                'response_id': 'second', 'usage': {'output_tokens': 300}, 'thread_token_usage': {'output_tokens': 500}}},
        ]
        rate = codex_request_sample(records, float)
        self.assertEqual(rate['value'], 30)
        self.assertEqual(rate['windowMs'], 10000)
        self.assertEqual(rate['outputTokens'], 300)
        self.assertEqual(rate['sampleAt'], 125)
        self.assertEqual(rate['kind'], 'estimated_request_average')
        repeated = records + [{'timestamp': 130, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'last_token_usage': {'output_tokens': 300}, 'total_token_usage': {'output_tokens': 500}}}}]
        self.assertEqual(codex_request_sample(repeated, float), rate)

    def test_cumulative_only_first_read_is_baseline(self):
        def usage(at, total):
            return {'timestamp': at, 'type': 'event_msg', 'payload': {
                'type': 'token_count', 'info': {'total_token_usage': {'output_tokens': total}}}}
        first = usage(100, 100000)
        self.assertIsNone(codex_request_sample([first], float))
        self.assertEqual(codex_request_sample([first, usage(110, 100200)], float)['value'], 20)
        self.assertIsNone(codex_request_sample([first, usage(110, 5)], float))
        self.assertIsNone(codex_request_sample([{'timestamp': 100, 'type': 'token_usage_record',
            'payload': {'usage': {'output_tokens': 900}}}], float), 'No observed start must remain unknown')

    def test_modern_only_codex_log_keeps_latest_cumulative_total(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'session.jsonl'
            records = [
                {'timestamp':100,'type':'event_msg','payload':{'type':'task_started'}},
                {'timestamp':109,'type':'response_item','payload':{'type':'reasoning'}},
                {'timestamp':110,'type':'token_usage_record','payload':{'usage':{'output_tokens':200},'thread_token_usage':{'output_tokens':200}}},
                {'timestamp':115,'type':'response_item','payload':{'type':'function_call_output'}},
                {'timestamp':124,'type':'response_item','payload':{'type':'reasoning'}},
                {'timestamp':125,'type':'token_usage_record','payload':{'usage':{'output_tokens':300},'thread_token_usage':{'output_tokens':500}}},
            ]
            log.write_text('\n'.join(json.dumps(r) for r in records)+'\n',encoding='utf-8')
            row={'id':'test','rollout_path':str(log),'title':'Test','name':None,'model':'test',
                 'model_provider':'test','cwd':directory,'agent_role':None}
            session=CodexAdapter(directory).parse_log(row,130)
            self.assertEqual(session['outputTokens'],500)
            self.assertEqual(session['_rateSample']['value'],30)

    def test_idle_and_waiting_keep_timestamp_without_faking_live_zero(self):
        self.assertIsNone(rate_info(None, active=True, now=100)['value'])
        self.assertEqual(rate_info(None, active=True, now=100)['status'], 'waiting')
        self.assertEqual(rate_info(None, active=False, now=100)['status'], 'idle')
        sample = request_sample(900, 10000, 100, 'codex-log', estimated=True)
        recent = rate_info(sample, active=True, now=110)
        waiting = rate_info(sample, active=True, now=300)
        idle = rate_info(sample, active=False, now=300)
        self.assertEqual(recent['status'], 'recent')
        self.assertEqual(waiting['status'], 'waiting')
        self.assertEqual(waiting['value'], 90)
        self.assertIn('等待新请求', waiting['reason'])
        self.assertEqual(idle['status'], 'idle')
        self.assertEqual(idle['sampleAt'], 100)


class CCSwitchReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'cc-switch.db'
        self.parent = '11111111-1111-4111-8111-111111111111'
        self.child = '22222222-2222-4222-8222-222222222222'
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE proxy_request_logs(request_id TEXT,app_type TEXT,output_tokens INT,latency_ms INT,first_token_ms INT,status_code INT,session_id TEXT,is_streaming INT,created_at INT,data_source TEXT,model TEXT)')
        conn.execute('CREATE TABLE providers(api_key TEXT)')
        conn.execute("INSERT INTO providers VALUES ('PRIVATE_DO_NOT_READ')")
        conn.commit();conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def insert(self, *, request_id='proxy-id', session=None, app='codex', at=100, source='proxy', first=1000):
        conn = sqlite3.connect(self.db)
        conn.execute('INSERT INTO proxy_request_logs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (request_id, app, 900, 10000, first, 200, session or self.parent, 1, at, source, 'safe-model'))
        conn.commit();conn.close()

    def test_strict_thread_attribution_and_read_only_no_credentials(self):
        self.insert()
        self.insert(request_id='codex_session:thread-v1:'+self.child+':1', session=self.parent, at=101, source='codex_session')
        self.insert(request_id='unknown-imported-format', session=self.parent, at=102, source='codex_session')
        before = self.db.read_bytes()
        reader = CCSwitchRateReader(self.db)
        result = reader.samples([{'id': self.parent, 'source': 'codex'}, {'id': self.child, 'source': 'codex'}], 105)
        self.assertEqual(result['codex:'+self.parent]['value'], 100)
        self.assertEqual(result['codex:'+self.child]['value'], 90)
        self.assertEqual(result['codex:'+self.child]['kind'], 'estimated_request_average')
        self.assertNotIn('PRIVATE_DO_NOT_READ', json.dumps(result))
        self.assertEqual(before, self.db.read_bytes(), 'Read-only collection must not modify CC Switch DB')
        self.assertEqual(reader.samples([{'id': 'unrelated', 'source': 'codex'}], 106), {})

    def test_optional_db_and_missing_timing_are_not_zero_rates(self):
        self.assertEqual(CCSwitchRateReader(self.db.parent/'missing.db').samples([], 105), {})
        self.insert(first=None)
        self.assertEqual(CCSwitchRateReader(self.db).samples([{'id': self.parent, 'source': 'codex'}], 105), {})
        self.insert(request_id='dsh-request', session='dsh-id', app='dsh', source='dsh_session', at=102, first=None)
        result = CCSwitchRateReader(self.db).samples([{'id': 'dsh-id', 'source': 'dsh'}], 105)
        self.assertEqual(result['dsh:dsh-id']['value'], 90)


class CollectorRateTests(unittest.TestCase):
    def test_latest_request_average_is_separate_from_legacy_poll_delta(self):
        class Adapter:
            def collect(self, now):
                return [{'id':'a','source':'codex','title':'Test request','model':'test','status':'running','lastAt':now,'outputTokens':1000,
                         '_rateSample':request_sample(300,10000,now-2,'codex-log',estimated=True)},
                        {'id':'b','source':'codex','title':'Latest request','model':'test','status':'running','lastAt':now,'outputTokens':500,
                         '_rateSample':request_sample(400,10000,now-1,'codex-log',estimated=True)}], ''
        class Reader:
            def samples(self, sessions, now):
                return {}
        collector = Collector(lambda:{'codex':True,'dsh':False}, {'codex':Adapter()}, rate_reader=Reader())
        first = collector.poll()
        second = collector.poll()
        self.assertEqual(first['totals']['outputRate'], 0)
        self.assertEqual(second['totals']['outputRate'], 0)
        self.assertEqual(second['totals']['outputRateInfo']['value'], 40, 'Latest average, never a sum of historical per-session speeds')
        self.assertEqual(second['totals']['outputRateInfo']['sessionId'], 'b')
        self.assertEqual(second['totals']['outputRateInfo']['kind'], 'estimated_request_average')
        self.assertNotIn('_rateSample', second['sessions'][0])
        self.assertEqual(second['totals']['outputRateMode'], 'usage_poll_delta')


if __name__ == '__main__':
    unittest.main()
