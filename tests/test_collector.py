import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import (CodexAdapter, DSHAdapter, Collector, make_handler, session_title,
                    read_json_window, LOG_READ_LIMIT, LOG_RECORD_LIMIT, PROJECTION_READ_LIMIT)


class AdapterTests(unittest.TestCase):
    def test_attachment_wrapper_does_not_replace_the_task_title(self):
        wrapped='# Files mentioned by the user:\n## screenshot.png: C:/temp/screenshot.png\nImage attachment: true\n## My request:\n修复工作台的输出速度\n<image name="screenshot">'
        self.assertEqual(session_title(wrapped),'修复工作台的输出速度')
        self.assertEqual(session_title('正常任务名称'),'正常任务名称')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = time.time()

    def tearDown(self):
        self.temp.cleanup()

    def codex_fixture(self, boundary="task_started", output=42):
        log = self.root / "session.jsonl"
        records = [
            {"timestamp": self.now, "type": "event_msg", "payload": {"type": boundary}},
            {"timestamp": self.now, "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 100, "output_tokens": output, "cached_input_tokens": 80},
                "last_token_usage": {"input_tokens": 50}, "model_context_window": 200}}},
            {"timestamp": self.now, "type": "response_item", "payload": {"type": "function_call", "name": "read_file", "arguments": "PRIVATE_TOOL_ARGUMENTS"}},
        ]
        log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        db = self.root / "state_5.sqlite"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT,rollout_path TEXT,title TEXT,model TEXT,model_provider TEXT,cwd TEXT,agent_role TEXT,updated_at INT,archived INT)")
        c.execute("DELETE FROM threads")
        c.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)", ("test-codex", str(log), "Test task", "test-model", "test", "C:/workspace", None, int(self.now), 0))
        c.commit();c.close()
        return log

    def dsh_fixture(self, open_turn=False):
        folder = self.root / "storages/session_projcache/sessions"
        folder.mkdir(parents=True, exist_ok=True)
        rows = {
            "title": {"val": "DSH task"},
            "turnBoundary": {"val": {"openTurnStartSeq": 8 if open_turn else None}},
            "sessionStats": {"val": {"steps": 2, "pendingCalls": {}}},
            "tokenUsage": {"val": {"totals": {"uncachedInputTokens": 20, "cacheReadTokens": 80, "outputTokens": 5}}},
            "modelSelection": {"val": {"lastUsed": {"model": "deepseek-test"}}},
        }
        (folder / "session-test-dsh.json").write_text(json.dumps({"record": {"identity": {"cwd": "C:/dsh"}, "rows": rows}}), encoding="utf-8")

    def test_codex_lifecycle_and_partial_line(self):
        log = self.codex_fixture()
        adapter = CodexAdapter(self.root)
        items, error = adapter.collect(self.now)
        self.assertFalse(error)
        self.assertEqual(items[0]["status"], "running")
        self.assertEqual(items[0]["outputTokens"], 42)
        self.assertNotIn("PRIVATE_TOOL_ARGUMENTS", json.dumps(items))
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": self.now, "type": "event_msg", "payload": {"type": "task_complete"}}) + "\n{\"partial\"")
        items, _ = adapter.collect(self.now)
        self.assertEqual(items[0]["status"], "idle")

    def test_terminal_lifecycle_wins_over_unmatched_historical_tool(self):
        log = self.codex_fixture()
        with log.open('a', encoding='utf-8') as stream:
            for payload in ({'type': 'function_call', 'name': 'old_tool', 'call_id': 'unmatched'},
                            {'type': 'task_complete'}):
                stream.write(json.dumps({'timestamp': self.now, 'type': 'response_item'
                    if payload['type'] == 'function_call' else 'event_msg', 'payload': payload}) + '\n')
        session = CodexAdapter(self.root).collect(self.now)[0][0]
        self.assertEqual(session['status'], 'idle')
        self.assertEqual(session['tools'][0]['pending'], 1)

    def test_collector_observes_log_write_within_two_seconds(self):
        log = self.codex_fixture()
        probe_release = threading.Event()
        def slow_detector():
            probe_release.wait(5)
            return {'codex': True, 'dsh': False}
        collector = Collector(slow_detector, {'codex': CodexAdapter(self.root)},
            rate_reader=SimpleNamespace(samples=lambda *_: {}), background_process_probe=True)
        # A previously verified online source keeps collecting while its next
        # expensive process check is stalled in the background.
        collector.processes = {'codex': True, 'dsh': False}
        collector.process_checked_at = self.now
        first = threading.Event()
        changed = threading.Event()
        stopped = threading.Event()
        original_poll = collector.poll
        def observe():
            snapshot = original_poll()
            first.set()
            if snapshot['totals']['outputTokens'] == 43:
                changed.set()
            return snapshot
        collector.poll = observe
        thread = threading.Thread(target=collector.run, args=(stopped,), daemon=True)
        thread.start()
        try:
            self.assertTrue(first.wait(2))
            with log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'timestamp': time.time(), 'type': 'token_usage_record',
                    'payload': {'thread_token_usage': {'output_tokens': 43}}}) + '\n')
            self.assertTrue(changed.wait(1.8), 'a flushed source update must reach the next one-second collection')
        finally:
            stopped.set()
            probe_release.set()
            thread.join(timeout=2)
            collector._process_thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_unfinished_stale_task_is_unknown(self):
        self.codex_fixture()
        items, _ = CodexAdapter(self.root).collect(self.now + 400)
        self.assertEqual(items[0]["status"], "unknown")

    def test_codex_appends_read_only_new_bytes_and_complete_partial_line(self):
        log = self.codex_fixture()
        adapter = CodexAdapter(self.root)
        adapter.collect(self.now)
        initial_size = log.stat().st_size
        completion = json.dumps({'timestamp': self.now, 'type': 'event_msg',
                                  'payload': {'type': 'task_complete'}}).encode()
        with log.open('ab') as stream:
            stream.write(completion[:20])
        with mock.patch('server.read_json_window', wraps=read_json_window) as read:
            self.assertEqual(adapter.collect(self.now)[0][0]['status'], 'running')
            self.assertEqual(read.call_args.args[2], initial_size)
        with log.open('ab') as stream:
            stream.write(completion[20:] + b'\n')
        with mock.patch('server.read_json_window', wraps=read_json_window) as read:
            self.assertEqual(adapter.collect(self.now)[0][0]['status'], 'idle')
            self.assertEqual(read.call_args.args[2], initial_size)

    def test_codex_cache_bounds_metadata_and_evicts_unselected_sessions(self):
        log = self.codex_fixture()
        with log.open('a', encoding='utf-8') as stream:
            for index in range(LOG_RECORD_LIMIT + 50):
                stream.write(json.dumps({'timestamp': self.now, 'type': 'response_item',
                    'payload': {'type': 'function_call', 'call_id': str(index),
                                'name': 'read_file', 'arguments': 'PRIVATE_INPUT_' * 10}}) + '\n')
        adapter = CodexAdapter(self.root)
        adapter.cache['expired'] = ((0, 0), {})
        adapter.log_records['expired'] = (0, [])
        adapter.collect(self.now)
        self.assertEqual(set(adapter.cache), {str(log)})
        self.assertEqual(set(adapter.log_records), {str(log)})
        records = adapter.log_records[str(log)][1]
        self.assertEqual(len(records), LOG_RECORD_LIMIT)
        self.assertNotIn('PRIVATE_INPUT', json.dumps(list(records)))
        self.assertEqual(adapter.collect(self.now)[0][0]['outputTokens'], 42)
        conn = sqlite3.connect(self.root / 'state_5.sqlite')
        try:
            conn.execute('UPDATE threads SET archived=1')
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(adapter.collect(self.now)[0], [])
        self.assertEqual(adapter.cache, {})
        self.assertEqual(adapter.log_records, {})

    def test_log_window_never_reads_unbounded_giant_lines(self):
        log = self.root / 'giant.jsonl'
        final = {'type': 'event_msg', 'payload': {'type': 'task_complete'}}
        log.write_bytes(b'x' * 5000 + b'\n' + json.dumps(final).encode() + b'\n')
        actual = log.open('rb')
        class BoundedReader:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                actual.close()
            def fileno(self):
                return actual.fileno()
            def seek(self, offset):
                return actual.seek(offset)
            def tell(self):
                return actual.tell()
            def read(self, size=-1):
                if size < 0 or size > 129:
                    raise AssertionError('read exceeds fixed window')
                return actual.read(size)
        with mock.patch.object(Path, 'open', return_value=BoundedReader()):
            records, offset = read_json_window(log, size=128)
        self.assertEqual(records, [final])
        self.assertEqual(offset, log.stat().st_size)

    def test_codex_same_size_rewrite_and_truncation_reset_history(self):
        log = self.codex_fixture()
        def write_boundary(kind):
            data = json.dumps({'timestamp': self.now, 'type': 'event_msg', 'payload': {'type': kind}})
            log.write_text(data.ljust(256) + '\n', encoding='utf-8')
        write_boundary('task_started')
        adapter = CodexAdapter(self.root)
        self.assertEqual(adapter.collect(self.now)[0][0]['status'], 'running')
        size = log.stat().st_size
        write_boundary('task_complete')
        self.assertEqual(log.stat().st_size, size)
        self.assertEqual(adapter.collect(self.now)[0][0]['status'], 'idle')
        log.write_text(json.dumps({'timestamp': self.now, 'type': 'response_item',
                                  'payload': {'type': 'reasoning'}}) + '\n', encoding='utf-8')
        self.assertEqual(adapter.collect(self.now)[0][0]['status'], 'active')

    def test_codex_growth_above_window_drops_disconnected_call_history(self):
        log = self.codex_fixture()
        with log.open('ab') as stream:
            stream.write(json.dumps({'timestamp': self.now, 'type': 'response_item',
                'payload': {'type': 'function_call', 'call_id': 'old', 'name': 'old_tool'}}).encode() + b'\n')
        adapter = CodexAdapter(self.root)
        self.assertEqual([tool['name'] for tool in adapter.collect(self.now)[0][0]['tools']], ['old_tool'])
        initial_size = log.stat().st_size
        with log.open('r+b') as stream:
            stream.seek(LOG_READ_LIMIT + 1, 2)
            stream.write(b'\n' + json.dumps({'timestamp': self.now, 'type': 'response_item',
                'payload': {'type': 'function_call', 'call_id': 'new', 'name': 'new_tool'}}).encode() + b'\n')
        self.assertGreater(log.stat().st_size - initial_size, LOG_READ_LIMIT)
        session = adapter.collect(self.now)[0][0]
        self.assertEqual([tool['name'] for tool in session['tools']], ['new_tool'])
        self.assertEqual(session['outputTokens'], 42)

    def test_codex_eight_mb_log_reads_only_two_kb_append_after_initial_window(self):
        log = self.codex_fixture()
        initial_records = log.read_bytes()
        with log.open('wb') as stream:
            stream.seek(8 * 1024 * 1024 - 1)
            stream.write(b'\n' + initial_records)
        reads = []
        original_open = Path.open
        class MeasuredReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def __getattr__(self, key):
                return getattr(self.stream, key)
            def read(self, size=-1):
                data = self.stream.read(size)
                reads.append(len(data))
                return data
        def counted_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return MeasuredReader(stream) if path == log and args == ('rb',) else stream
        adapter = CodexAdapter(self.root)
        with mock.patch.object(Path, 'open', counted_open):
            adapter.collect(self.now)
        self.assertEqual(reads, [LOG_READ_LIMIT + 1])
        appended = json.dumps({'timestamp': self.now, 'type': 'response_item',
            'payload': {'type': 'function_call', 'call_id': 'append', 'name': 'append_tool',
                        'arguments': 'x' * 2000}}).encode() + b'\n'
        with log.open('ab') as stream:
            stream.write(appended)
        reads.clear()
        with mock.patch.object(Path, 'open', counted_open):
            session = adapter.collect(self.now)[0][0]
        self.assertEqual(reads, [len(appended) + 1])
        self.assertGreater(LOG_READ_LIMIT / reads[0], 900)
        self.assertIn('append_tool', [tool['name'] for tool in session['tools']])

    def test_spawned_agents_have_recorded_names_and_separate_counts(self):
        parent_log = self.codex_fixture()
        db = sqlite3.connect(self.root / 'state_5.sqlite')
        for column in ('source', 'name', 'agent_path', 'agent_nickname'):
            db.execute(f'ALTER TABLE threads ADD COLUMN {column} TEXT')
        db.execute('UPDATE threads SET name=?,source=?', ('用户命名的主任务', 'vscode'))
        child_source = {'subagent': {'thread_spawn': {'parent_thread_id': 'test-codex',
                        'agent_path': '/root/repair_flow', 'agent_nickname': 'Newton'}}}
        # Match the actual schema: generated children have blank title/name,
        # with identity and parent recorded in source rather than agent_role.
        for child_id, source, nickname in [('source-child', json.dumps(child_source), 'Newton'),
                                          ('legacy-child', None, 'Curie')]:
            child_log = self.root / (child_id + '.jsonl')
            child_log.write_bytes(parent_log.read_bytes())
            db.execute('INSERT INTO threads(id,rollout_path,title,model,model_provider,cwd,agent_role,updated_at,archived,source,agent_nickname) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (child_id, str(child_log), '', 'child-model', 'test', 'C:/workspace',
                        None, int(self.now), 0, source, nickname))
        db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT,status TEXT)')
        db.execute('INSERT INTO thread_spawn_edges VALUES(?,?,?)', ('test-codex', 'legacy-child', 'open'))
        db.commit();db.close()

        collector = Collector(lambda: {'codex': True, 'dsh': False},
                              {'codex': CodexAdapter(self.root), 'dsh': DSHAdapter(self.root)})
        snapshot = collector.poll()
        sessions = {s['id']: s for s in snapshot['sessions']}
        self.assertEqual(sessions['test-codex']['title'], '用户命名的主任务')
        self.assertFalse(sessions['test-codex']['isSubagent'])
        self.assertEqual(sessions['source-child']['title'], '协作子任务 · repair_flow')
        self.assertEqual(sessions['source-child']['agentPath'], '/root/repair_flow')
        self.assertEqual(sessions['legacy-child']['title'], '协作子任务 · Curie')
        for child in ('source-child', 'legacy-child'):
            self.assertEqual(sessions[child]['kind'], 'subagent')
            self.assertTrue(sessions[child]['isSubagent'])
            self.assertEqual(sessions[child]['parentId'], 'test-codex')
            self.assertEqual(sessions[child]['parentTitle'], '用户命名的主任务')
        self.assertEqual(snapshot['totals']['sessions'], 3)
        self.assertEqual(snapshot['totals']['threads'], 1)
        self.assertEqual(snapshot['totals']['subagents'], 2)
        self.assertEqual(snapshot['totals']['outputTokens'], 126)
        self.assertEqual(snapshot['sources'][0]['threads'], 1)
        self.assertEqual(sum(e['kind'] == 'parent-child' for e in snapshot['topology']['edges']), 2)

    def test_rename_updates_even_when_log_is_cached(self):
        self.codex_fixture()
        db = sqlite3.connect(self.root / 'state_5.sqlite')
        db.execute('ALTER TABLE threads ADD COLUMN name TEXT')
        db.commit()
        adapter = CodexAdapter(self.root)
        self.assertEqual(adapter.collect(self.now)[0][0]['title'], 'Test task')
        db.execute('UPDATE threads SET name=?', ('刚修改的会话名',))
        db.commit();db.close()
        self.assertEqual(adapter.collect(self.now)[0][0]['title'], '刚修改的会话名')

    def test_blank_main_thread_uses_identifier_without_claiming_a_task_name(self):
        self.codex_fixture()
        db = sqlite3.connect(self.root / 'state_5.sqlite')
        db.execute('UPDATE threads SET title=?', ('   ',))
        db.commit();db.close()
        session = CodexAdapter(self.root).collect(self.now)[0][0]
        self.assertEqual(session['title'], 'Codex 会话 · st-codex')
        self.assertEqual(session['kind'], 'thread')
        self.assertFalse(session['isSubagent'])

    def test_dsh_projection_lifecycle_and_tokens(self):
        self.dsh_fixture(open_turn=True)
        items, _ = DSHAdapter(self.root).collect(self.now)
        self.assertEqual(items[0]["status"], "running")
        self.assertEqual(items[0]["inputTokens"], 100)
        self.assertEqual(items[0]["outputTokens"], 5)
        self.assertEqual(items[0]["model"], "deepseek-test")
        self.dsh_fixture(open_turn=False)
        items, _ = DSHAdapter(self.root).collect(self.now)
        self.assertEqual(items[0]["status"], "idle")

    def test_dsh_oversized_projection_is_skipped_and_cache_is_compact(self):
        self.dsh_fixture(open_turn=True)
        folder = self.root / 'storages/session_projcache/sessions'
        path = folder / 'session-test-dsh.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record['record']['rows']['privateMessages'] = {'val': 'PRIVATE_CHAT_HISTORY'}
        record['record']['rows']['sessionStats']['val']['pendingCalls'] = {
            'call': {'name': 'read_file', 'arguments': 'PRIVATE_ARGUMENTS'}}
        path.write_text(json.dumps(record), encoding='utf-8')
        adapter = DSHAdapter(self.root)
        adapter.cache['expired'] = (0, {})
        sessions, note = adapter.collect(self.now)
        self.assertFalse(note)
        self.assertEqual(sessions[0]['lastTool'], 'read_file')
        self.assertEqual(set(adapter.cache), {str(path)})
        self.assertNotIn('PRIVATE', json.dumps(adapter.cache))
        with path.open('wb') as stream:
            stream.seek(PROJECTION_READ_LIMIT)
            stream.write(b'x')
        with mock.patch.object(Path, 'open', side_effect=AssertionError('must skip before reading')):
            sessions, note = adapter.collect(self.now)
        self.assertEqual(sessions, [])
        self.assertIn('过大', note)
        self.assertEqual(adapter.cache, {})

    def test_auto_single_dual_and_closed_sources(self):
        self.codex_fixture()
        self.dsh_fixture(open_turn=True)
        processes = {"codex": True, "dsh": False}
        collector = Collector(lambda: dict(processes), {"codex": CodexAdapter(self.root), "dsh": DSHAdapter(self.root)})
        first = collector.poll()
        self.assertEqual(first["totals"]["online"], 1)
        self.assertEqual({s["source"] for s in first["sessions"]}, {"codex"})
        self.assertEqual(first["totals"]["outputRate"], 0)
        processes["dsh"] = True;collector.last_process_at = 0
        both = collector.poll()
        self.assertEqual(both["totals"]["online"], 2)
        self.assertEqual(both["totals"]["running"], 2)
        self.assertEqual({s["source"] for s in both["sessions"]}, {"codex", "dsh"})
        processes["codex"] = False;collector.last_process_at = 0
        dsh_only = collector.poll()
        self.assertEqual({s["source"] for s in dsh_only["sessions"]}, {"dsh"})
        processes["dsh"] = False;collector.last_process_at = 0
        closed = collector.poll()
        self.assertEqual(closed["totals"]["running"], 0)
        self.assertEqual(closed["sessions"], [])
        self.assertEqual(closed["totals"]["outputTokens"], 0)
        self.assertEqual(collector.previous, {})


class CollectorSchedulingTests(unittest.TestCase):
    def make_collector(self, detector, **kwargs):
        adapter = SimpleNamespace(collect=lambda _: ([], ''))
        return Collector(detector, {'codex': adapter, 'dsh': adapter},
                         rate_reader=SimpleNamespace(samples=lambda *_: {}), **kwargs)

    def test_slow_process_probe_does_not_block_collection_or_spawn_duplicates(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        def slow_detector():
            calls.append(1)
            entered.set()
            release.wait(3)
            return {'codex': True, 'dsh': False}
        collector = self.make_collector(slow_detector, background_process_probe=True)
        try:
            started = time.perf_counter()
            initial = collector.poll()
            self.assertLess(time.perf_counter() - started, .5)
            self.assertTrue(initial['discovering'])
            self.assertTrue(entered.wait(1))
            for _ in range(5):
                collector.last_process_at = 0
                collector.poll()
            self.assertEqual(calls, [1])
            self.assertTrue(collector._process_thread.is_alive())
        finally:
            release.set()
            collector._process_thread.join(timeout=2)
        snapshot = collector.poll()
        self.assertFalse(snapshot['discovering'])
        self.assertTrue(snapshot['sources'][0]['online'])

    def test_unchanged_poll_preserves_revision_and_skips_topology_rebuild(self):
        online = {'codex': False, 'dsh': False}
        collector = self.make_collector(lambda: dict(online))
        with mock.patch('server.build_topology', return_value={'nodes': [], 'edges': []}) as build:
            collector.poll()
            etag = collector.state_etag
            revision = collector.revision
            for _ in range(5):
                collector.poll()
            self.assertEqual(collector.state_etag, etag)
            self.assertEqual(collector.revision, revision)
            self.assertEqual(build.call_count, 1)
            online['codex'] = True
            collector.last_process_at = 0
            collector.poll()
            self.assertNotEqual(collector.state_etag, etag)
            self.assertEqual(build.call_count, 2)
        self.assertNotEqual(collector.state_etag, self.make_collector(lambda: online).state_etag)


class ServerTests(unittest.TestCase):
    def setUp(self):
        collector = Collector(lambda: {"codex": False, "dsh": False})
        collector.poll()
        self.collector = collector
        self.published=[]
        self.bridge=SimpleNamespace(publish=lambda payload: self.published.append(payload) or {'active': True},
                                    drain=lambda client_id: [{'key':'control-1','type':'click'}] if client_id=='test-client' else [])
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(collector, wallpaper_token='test-install-token', input_bridge=self.bridge))
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=2)

    def test_state_health_and_static_allowlist(self):
        with urlopen(self.url + "/api/health") as r:
            self.assertEqual(json.load(r)["app"], "agt-command-center")
        with urlopen(self.url + "/api/state") as r:
            self.assertEqual(json.load(r)["totals"]["online"], 0)
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/server.py")
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/audio/sw421k.mp3")
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + "/api/state", headers={"Host": "untrusted.example"}))
        self.assertEqual(error.exception.code, 403)

    def test_wallpaper_requires_install_token(self):
        for headers in ({'Origin': 'null'}, {'Origin': 'null', 'X-AGT-Token': 'wrong'}):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url + '/api/state', headers=headers))
            self.assertEqual(error.exception.code, 403)
        with urlopen(Request(self.url + '/api/state', headers={
                'Origin': 'null', 'X-AGT-Token': 'test-install-token'})) as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'null')
            self.assertEqual(json.load(response)['app'], 'agt-command-center')
        with urlopen(Request(self.url + '/api/state', headers={'Origin': 'https://untrusted.example'})) as response:
            self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))

    def test_wallpaper_preflight_is_limited_to_api(self):
        headers={'Origin': 'null', 'Access-Control-Request-Method': 'GET',
                 'Access-Control-Request-Headers': 'x-agt-token'}
        with urlopen(Request(self.url + '/api/state', method='OPTIONS', headers=headers)) as response:
            self.assertEqual(response.status, 204)
        for route in ('/runtime.js', '/server.py'):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url + route, method='OPTIONS', headers=headers))
            self.assertEqual(error.exception.code, 403)

    def test_conditional_state_uses_fresh_collector_heartbeat_without_serializing(self):
        headers = {'Origin': 'null', 'X-AGT-Token': 'test-install-token'}
        with urlopen(Request(self.url + '/api/state', headers=headers)) as response:
            tag = response.headers['ETag']
            checked_at = float(response.headers['X-AGT-Checked-At'])
            self.assertIn('ETag', response.headers['Access-Control-Expose-Headers'])
            first = json.load(response)
        self.assertEqual(first['revision'], self.collector.revision)
        # A new successful poll changes freshness without changing task data.
        with mock.patch('server.time.time', return_value=checked_at + 1):
            self.collector.poll()
        self.assertIsNone(self.collector.encoded_snapshot)
        with mock.patch('server.json.dumps', side_effect=AssertionError('304 must not serialize state')):
            with self.assertRaises(HTTPError) as result:
                urlopen(Request(self.url + '/api/state', headers={**headers, 'If-None-Match': tag}))
        self.assertEqual(result.exception.code, 304)
        self.assertEqual(result.exception.read(), b'')
        self.assertEqual(result.exception.headers['ETag'], tag)
        self.assertEqual(float(result.exception.headers['X-AGT-Checked-At']), checked_at + 1)
        self.assertIsNone(self.collector.encoded_snapshot)
        # A request alone cannot advance the collector heartbeat.
        with self.assertRaises(HTTPError) as repeated:
            urlopen(Request(self.url + '/api/state', headers={**headers, 'If-None-Match': tag}))
        self.assertEqual(repeated.exception.headers['X-AGT-Checked-At'], result.exception.headers['X-AGT-Checked-At'])
        with self.assertRaises(HTTPError) as denied:
            urlopen(Request(self.url + '/api/state', headers={'Origin': 'null', 'If-None-Match': tag}))
        self.assertEqual(denied.exception.code, 403)

    def test_conditional_state_preflight_stays_limited_to_state(self):
        headers = {'Origin': 'null', 'Access-Control-Request-Method': 'GET',
                   'Access-Control-Request-Headers': 'x-agt-token,if-none-match'}
        with urlopen(Request(self.url + '/api/state', method='OPTIONS', headers=headers)) as response:
            self.assertIn('If-None-Match', response.headers['Access-Control-Allow-Headers'])
        for route in ('/api/health', '/api/ui', '/runtime.js'):
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(self.url + route, method='OPTIONS', headers=headers))
            self.assertEqual(denied.exception.code, 403)

    def test_state_json_is_encoded_once_per_poll_for_all_clients(self):
        with urlopen(self.url + '/api/state') as response:
            first = response.read()
        with mock.patch('server.json.dumps', side_effect=AssertionError('must reuse encoded snapshot')):
            with urlopen(self.url + '/api/state') as response:
                self.assertEqual(response.read(), first)

    def test_desktop_controls_require_install_token_and_file_origin(self):
        body=json.dumps({'clientId':'test-client','regions':[]}).encode()
        for headers in ({'Origin':'null'}, {'Origin':'https://untrusted.example','X-AGT-Token':'test-install-token'}):
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url+'/api/ui', data=body, headers=headers))
            self.assertEqual(error.exception.code,403)
        self.assertEqual(self.published,[])
        headers={'Origin':'null','X-AGT-Token':'test-install-token','Content-Type':'application/json'}
        with urlopen(Request(self.url+'/api/ui',data=body,headers=headers)) as response:
            self.assertTrue(json.load(response)['active'])
        self.assertEqual(self.published,[{'clientId':'test-client','regions':[]}])
        with urlopen(Request(self.url+'/api/ui?clientId=test-client',headers=headers)) as response:
            self.assertEqual(json.load(response)['commands'],[{'key':'control-1','type':'click'}])
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url+'/api/ui?clientId=test-client')
        self.assertEqual(error.exception.code,403)


if __name__ == "__main__":
    unittest.main()
