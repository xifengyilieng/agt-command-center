import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import threading
import unittest
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import CodexAdapter, DSHAdapter, Collector, make_handler, session_title


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

    def test_unfinished_stale_task_is_unknown(self):
        self.codex_fixture()
        items, _ = CodexAdapter(self.root).collect(self.now + 400)
        self.assertEqual(items[0]["status"], "unknown")

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


class ServerTests(unittest.TestCase):
    def setUp(self):
        collector = Collector(lambda: {"codex": False, "dsh": False})
        collector.poll()
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
