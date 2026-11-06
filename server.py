"""AGT command center: read-only local adapters, Python standard library only."""
from __future__ import annotations

import argparse
from collections import deque
import datetime as dt
import heapq
import json
import mimetypes
import os
import re
import secrets
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs
from telemetry import summarize_calls, build_topology
from output_rates import codex_request_sample, CCSwitchRateReader, best_sample, rate_info

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SESSION_LIMIT = 32
LOG_READ_LIMIT = 2 * 1024 * 1024
LOG_RECORD_LIMIT = 1024
PROJECTION_READ_LIMIT = 2 * 1024 * 1024
PROCESS_POLL_SECONDS = 15
COLLECTION_POLL_SECONDS = 1


def stamp(value):
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10**11 else float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def short(value, limit=160):
    return " ".join(str(value or "").split())[:limit]


def session_title(value):
    text = str(value or '')
    if text.lstrip().startswith('# Files mentioned by the user:'):
        match = re.search(r'(?:^|\n)\s*(?:##?\s*)?My request:\s*(.*)', text, re.S)
        if match:
            text = re.split(r'\n\s*<image\b', match.group(1), maxsplit=1)[0]
    return short(text) or '未命名任务'


def codex_session_identity(row):
    """Keep spawned agents distinct from user chats using recorded identities."""
    source = row.get('source')
    if isinstance(source, str):
        try:
            source = json.loads(source) if source.lstrip().startswith('{') else {}
        except (ValueError, TypeError):
            source = {}
    source = source if isinstance(source, dict) else {}
    subagent = source.get('subagent')
    spawn = subagent.get('thread_spawn') if isinstance(subagent, dict) else {}
    spawn = spawn if isinstance(spawn, dict) else {}
    parent = row.get('_parentId') or spawn.get('parent_thread_id')
    is_subagent = bool(subagent is not None or parent)
    agent_path = short(row.get('agent_path') or spawn.get('agent_path'), 300)
    nickname = short(row.get('agent_nickname') or spawn.get('agent_nickname'), 80)
    role = short(row.get('agent_role') or spawn.get('agent_role'), 80)
    name = next((value for value in (row.get('name'), row.get('title'))
                 if str(value or '').strip()), None)
    if name:
        title = session_title(name)
    elif is_subagent:
        # The real spawn task path identifies the job; a nickname or short ID
        # remains a truthful fallback for older clients without that field.
        task = agent_path.rstrip('/').rsplit('/', 1)[-1] if agent_path else ''
        title = '协作子任务 · ' + (task or role or nickname or str(row['id'])[-8:])
    else:
        first_request = str(row.get('first_user_message') or '').strip()
        title = session_title(first_request) if first_request else 'Codex 会话 · ' + str(row['id'])[-8:]
    return {'title': title, 'kind': 'subagent' if is_subagent else 'thread',
            'isSubagent': is_subagent, 'parentId': parent,
            'agentPath': agent_path, 'nickname': nickname, 'role': role or 'Agent'}


def read_json_window(path, size=LOG_READ_LIMIT, offset=None):
    """Read at most one bounded window, retaining incomplete JSON for next poll.

    A tool result can occupy a multi-megabyte line. Never use an unbounded
    readline to skip its prefix, or read through concurrent appends to EOF.
    """
    with path.open("rb") as f:
        total = os.fstat(f.fileno()).st_size
        start = max(0, total - size)
        if offset is not None and start <= offset <= total:
            start = offset
        f.seek(max(0, start - 1))
        data = f.read(total - f.tell())
    if start:
        preceding, data = data[:1], data[1:]
        if preceding != b'\n':
            data = data.partition(b'\n')[2]
    lines = data.splitlines(keepends=True)
    result = []
    next_offset = total
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                result.append(item)
        except (ValueError, UnicodeError):
            if index == len(lines) - 1 and not line.endswith(b'\n'):
                next_offset = total - len(line)
    return result, next_offset


def tail_json(path, size=1024 * 1024):
    return read_json_window(path, size)[0]


def compact_log_record(event):
    """Keep only telemetry metadata; never retain tool outputs or chat text."""
    event_kind = event.get('type')
    if event_kind not in ('event_msg', 'response_item', 'token_usage_record', 'turn_context'):
        return None
    payload = event.get('payload')
    if not isinstance(payload, dict):
        return None
    kept = {key: short(payload[key], 200) for key in
            ('type', 'name', 'call_id', 'id', 'response_id', 'role', 'model')
            if isinstance(payload.get(key), str)}
    for key in ('started_at_ms', 'completed_at_ms'):
        if isinstance(payload.get(key), (int, float)):
            kept[key] = payload[key]
    token_keys = ('input_tokens', 'output_tokens', 'cached_input_tokens',
                  'reasoning_output_tokens', 'total_tokens')
    def usage(value):
        return {key: value[key] for key in token_keys
                if isinstance(value.get(key), (int, float))} if isinstance(value, dict) else {}
    for key in ('usage', 'thread_token_usage'):
        if isinstance(payload.get(key), dict):
            kept[key] = usage(payload[key])
    info = payload.get('info')
    if isinstance(info, dict):
        kept['info'] = {key: usage(info[key]) for key in
                        ('total_token_usage', 'last_token_usage') if isinstance(info.get(key), dict)}
        if isinstance(info.get('model_context_window'), (int, float)):
            kept['info']['model_context_window'] = info['model_context_window']
    item = payload.get('item')
    if isinstance(item, dict):
        kept['item'] = {key: short(item[key], 200) for key in
                        ('type', 'id', 'call_id', 'name', 'tool_name', 'status')
                        if isinstance(item.get(key), str)}
        tool = item.get('tool')
        if isinstance(tool, dict):
            tool = tool.get('name')
        if isinstance(tool, str):
            kept['item']['tool'] = short(tool, 100)
        if isinstance(item.get('exit_code'), int):
            kept['item']['exit_code'] = item['exit_code']
    settings = payload.get('thread_settings')
    if isinstance(settings, dict) and isinstance(settings.get('model'), str):
        kept['thread_settings'] = {'model': short(settings['model'], 100)}
    timestamp = event.get('timestamp')
    return {'type': event_kind,
            'timestamp': short(timestamp, 80) if isinstance(timestamp, str) else timestamp,
            'payload': kept}


def detect_processes():
    """Match application identities, not the existence of old session files."""
    if os.name != "nt":
        raw = subprocess.check_output(["ps", "-eo", "pid=,comm=,args="], text=True)
        low = raw.lower()
        return {"codex": "codex" in low, "dsh": "dsh-desktop" in low or "dsh web" in low}
    script = r"""
    $items = Get-CimInstance Win32_Process | Where-Object {
      $_.Name -match '^(codex|chatgpt|dsh.*|deepseek.*|harness.*|node|electron)(\.exe)?$'
    }; @($items | ForEach-Object {
      $path = [string]$_.ExecutablePath; $cmd = [string]$_.CommandLine
      $name = [string]$_.Name
      if ($name -match 'updater|proxylauncher|crashpad') { return }
      $kind = if ($name -match '^codex\.exe$' -or ($name -match '^chatgpt\.exe$' -and $path -match 'OpenAI\.Codex')) {'codex'}
      elseif ($name -match '^(dsh|deepseek|harness)' -or ($path + ' ' + $cmd) -match '(@deepseek-ai[\\/]dsh|dsh-desktop|deepseek.harness|dsh\.m?[cj]?s\s+(web|desktop)|\bdsh\s+web\b)') {'dsh'}
      if ($kind) { @{kind=$kind;pid=$_.ProcessId} }
    }) | ConvertTo-Json -Compress
    """
    raw = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        creationflags=CREATE_NO_WINDOW, timeout=8, encoding="utf-8", errors="replace",
    ).strip()
    entries = json.loads(raw) if raw else []
    if isinstance(entries, dict):
        entries = [entries]
    return {key: any(p.get("kind") == key for p in entries) for key in ("codex", "dsh")}


class CodexAdapter:
    def __init__(self, home=None):
        self.home = Path(home if home is not None else os.environ.get('CODEX_HOME') or HOME / '.codex').expanduser()
        self.cache = {}
        self.log_records = {}

    def parse_log(self, row, now):
        row = dict(row)
        path = Path(row["rollout_path"])
        stat = path.stat()
        cached = self.cache.get(str(path))
        if cached and cached[0] == (stat.st_size, stat.st_mtime_ns):
            result = dict(cached[1])
        else:
            state = self.log_records.get(str(path))
            append = bool(cached and state and stat.st_size > cached[0][0])
            incoming, offset = read_json_window(path, LOG_READ_LIMIT, state[0] if append else None)
            continuous = append and stat.st_size - state[0] <= LOG_READ_LIMIT
            records = state[1] if continuous else deque(maxlen=LOG_RECORD_LIMIT)
            incoming = [compact for event in incoming if (compact := compact_log_record(event)) is not None]
            # Read every metadata entry in the initial window before trimming
            # history, so early cumulative totals/lifecycle markers are kept.
            history = [*records, *incoming]
            records.extend(incoming)
            self.log_records[str(path)] = (offset, records)
            previous = cached[1] if cached and append else {}
            usage = {'input_tokens': previous.get('inputTokens', 0),
                     'output_tokens': previous.get('outputTokens', 0),
                     'cached_input_tokens': previous.get('cachedTokens', 0)}
            last_usage = {'input_tokens': previous.get('contextTokens', 0)}
            window = previous.get('contextWindow', 0)
            boundary = None
            last_at = stat.st_mtime
            last_tool = previous.get('lastTool', '')
            events = []
            calls = 0
            for event in history:
                payload = event.get("payload") or {}
                kind = payload.get("type")
                at = stamp(event.get("timestamp"))
                last_at = max(last_at, at)
                if event.get("type") == "event_msg":
                    if kind in ("task_started", "turn_started"):
                        boundary = "running"
                    elif kind in ("task_complete", "turn_complete", "turn_aborted", "task_interrupted"):
                        boundary = "idle"
                    if kind == "token_count":
                        info = payload.get("info") or {}
                        usage = info.get("total_token_usage") or usage
                        last_usage = info.get("last_token_usage") or last_usage
                        window = info.get("model_context_window") or window
                    if kind == "item_completed":
                        item = payload.get("item") or {}
                        item_kind = item.get("type", "")
                        if item_kind in ("McpToolCall", "CommandExecution", "WebSearch"):
                            # Tool inputs and outputs stay out of the dashboard.
                            tool = item.get("tool") or item.get("name") or item_kind
                            if isinstance(tool, dict):
                                tool = tool.get("name", item_kind)
                            events.append({"at": at, "source": "codex", "session": row["id"],
                                           "kind": "tool", "message": short(tool, 70)})
                if event.get("type") == "response_item" and kind in ("function_call", "custom_tool_call"):
                    last_tool = short(payload.get("name"), 70)
                    calls += 1
                    events.append({"at": at, "source": "codex", "session": row["id"],
                                   "kind": "tool", "message": last_tool})
                if event.get("type") == "token_usage_record":
                    # New clients carry thread totals even when token_count is absent.
                    token = payload.get("thread_token_usage") or {}
                    if isinstance(token, dict) and token:
                        usage = token
            # If the last lifecycle marker is outside the tail, retain a previously
            # observed boundary instead of resetting a long-running task to idle.
            if boundary is None and previous:
                boundary = previous.get("boundary")
            result = {"boundary": boundary, "lastAt": last_at, "lastTool": last_tool,
                      "inputTokens": int(usage.get("input_tokens") or 0),
                      "outputTokens": int(usage.get("output_tokens") or 0),
                      "cachedTokens": int(usage.get("cached_input_tokens") or 0),
                      "contextTokens": int(last_usage.get("input_tokens") or 0),
                      "contextWindow": int(window or 0), "events": events[-12:],
                      "toolCallsInTail": calls}
            normalized = summarize_calls(records, row['id'], stamp, short)
            result.update(normalized)
            result['_rateSample'] = codex_request_sample(history, stamp) or previous.get('_rateSample')
            self.cache[str(path)] = ((stat.st_size, stat.st_mtime_ns), result)
        active_age = now - result["lastAt"]
        status = result["boundary"] or ("active" if active_age < 90 else "unknown")
        if status in ("running", "active") and active_age > 300:
            status = "unknown"
        return {**result, "id": row["id"], "source": "codex", "status": status,
                "model": row["model"] or row["model_provider"] or "—",
                "cwd": row["cwd"], **codex_session_identity(row)}

    def collect(self, now):
        candidates = sorted(self.home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            self.cache.clear()
            self.log_records.clear()
            return [], "未发现 Codex 会话库"
        conn = sqlite3.connect(candidates[0].as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
            wanted = ["id", "rollout_path", "title", "name", "model", "model_provider", "cwd",
                      "agent_role", "updated_at", "source", "agent_path", "agent_nickname", "first_user_message"]
            fields = ",".join(f'"{key}"' if key in columns else f'NULL AS "{key}"' for key in wanted)
            rows = conn.execute(f"SELECT {fields} FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT {SESSION_LIMIT}").fetchall()
            parents = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='thread_spawn_edges'").fetchone():
                ids = [r['id'] for r in rows]
                if ids:
                    placeholders = ','.join('?' for _ in ids)
                    parents = {r['child_thread_id']: r['parent_thread_id'] for r in conn.execute(
                        f'SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges WHERE child_thread_id IN ({placeholders})', ids)}
        finally:
            conn.close()
        current = {str(Path(row['rollout_path'])) for row in rows if row['rollout_path']}
        self.cache = {path: value for path, value in self.cache.items() if path in current}
        self.log_records = {path: value for path, value in self.log_records.items() if path in current}
        sessions = []
        skipped = 0
        for row in rows:
            try:
                session = self.parse_log({**dict(row), '_parentId': parents.get(row['id'])}, now)
                sessions.append(session)
            except (OSError, ValueError, TypeError):
                skipped += 1
        titles = {session['id']: session['title'] for session in sessions}
        for session in sessions:
            session['parentTitle'] = titles.get(session['parentId'])
        return sessions, f"{skipped} 个会话暂不可读" if skipped else ""


class DSHAdapter:
    def __init__(self, home=None):
        self.home = Path(home if home is not None else os.environ.get('DSH_HOME') or HOME / '.dsh').expanduser()
        self.cache = {}

    @staticmethod
    def compact_record(record):
        rows = record.get('rows') or {}
        def val(key):
            return (rows.get(key) or {}).get('val')
        def fields(value, names):
            return {key: value[key] for key in names if key in value} if isinstance(value, dict) else {}
        stats = fields(val('sessionStats'), ('steps',))
        pending = (val('sessionStats') or {}).get('pendingCalls') or {}
        stats['pendingCalls'] = {}
        if isinstance(pending, dict):
            for call_id in deque(pending, maxlen=12):
                call = pending[call_id]
                name = call.get('name') or call.get('tool') if isinstance(call, dict) else ''
                if isinstance(name, dict):
                    name = name.get('name')
                stats['pendingCalls'][short(call_id, 200)] = {'name': short(name, 70)}
        title = val('title')
        if isinstance(title, dict):
            title = title.get('title') or title.get('text')
        compact = {
            'title': session_title(title) if str(title or '').strip() else '',
            'turnBoundary': fields(val('turnBoundary'), ('openTurnStartSeq',)),
            'sessionStats': stats,
            'tokenUsage': {'totals': fields((val('tokenUsage') or {}).get('totals'),
                ('uncachedInputTokens', 'cacheReadTokens', 'outputTokens'))},
            'contextPressure': fields(val('contextPressure'), ('pressureTokens', 'surfaceTokens', 'contextWindow')),
            'modelSelection': {'lastUsed': {'model': short(((val('modelSelection') or {}).get('lastUsed') or {}).get('model'), 100)}},
        }
        return {'identity': {'cwd': short((record.get('identity') or {}).get('cwd'), 500)},
                'rows': {key: {'val': value} for key, value in compact.items()}}

    def collect(self, now):
        folder = self.home / "storages" / "session_projcache" / "sessions"
        if not folder.exists():
            self.cache.clear()
            return [], "未发现 DSH 会话投影"
        paths = heapq.nlargest(SESSION_LIMIT, folder.glob('*.json'), key=lambda p: p.stat().st_mtime)
        current = {str(path) for path in paths}
        self.cache = {path: value for path, value in self.cache.items() if path in current}
        sessions = []
        skipped = 0
        for path in paths:
            try:
                stat = path.stat()
                modified = stat.st_mtime
                signature = (stat.st_size, stat.st_mtime_ns)
                if stat.st_size > PROJECTION_READ_LIMIT:
                    self.cache.pop(str(path), None)
                    skipped += 1
                    continue
                cached = self.cache.get(str(path))
                if cached and cached[0] == signature:
                    record = cached[1]
                else:
                    with path.open('rb') as stream:
                        raw = stream.read(PROJECTION_READ_LIMIT + 1)
                    if len(raw) > PROJECTION_READ_LIMIT:
                        skipped += 1
                        continue
                    record = self.compact_record(json.loads(raw).get('record', {}))
                    self.cache[str(path)] = (signature, record)
                rows = record.get("rows") or {}
                def val(key):
                    return (rows.get(key) or {}).get("val")
                boundary = val("turnBoundary") or {}
                stats = val("sessionStats") or {}
                usage = val("tokenUsage") or {}
                totals = usage.get("totals") or {}
                pressure = val("contextPressure") or {}
                model = (val("modelSelection") or {}).get("lastUsed") or {}
                identity = record.get("identity") or {}
                pending = stats.get("pendingCalls") or {}
                is_open = boundary.get("openTurnStartSeq") is not None
                status = "running" if is_open else "idle"
                if is_open and now - modified > 300:
                    status = "unknown"
                title = val("title")
                if isinstance(title, dict):
                    title = title.get("title") or title.get("text")
                session_id = path.stem.removeprefix("session-")
                events = []
                if isinstance(pending, dict):
                    for call_id in deque(pending, maxlen=12):
                        call = pending[call_id]
                        name = call.get("name") or call.get("tool") or "工具执行中" if isinstance(call, dict) else "工具执行中"
                        if isinstance(name, dict):
                            name = name.get('name') or '工具执行中'
                        events.append({"at": modified, "source": "dsh", "session": session_id,
                                       "id": str(call_id), "kind": "tool", "status": "running", "message": short(name, 70)})
                sessions.append({"id": session_id, "source": "dsh",
                                 "title": session_title(title) if str(title or '').strip() else 'DSH 会话 · ' + session_id[-8:],
                                 "kind": "thread", "isSubagent": False, "parentId": None,
                                 "model": model.get("model") or "—", "cwd": identity.get("cwd", ""),
                                 "role": "Agent", "status": status, "lastAt": modified,
                                 "lastTool": events[-1]["message"] if events else "",
                                 "inputTokens": int(totals.get("uncachedInputTokens", 0)) + int(totals.get("cacheReadTokens", 0)),
                                 "outputTokens": int(totals.get("outputTokens", 0)),
                                 "cachedTokens": int(totals.get("cacheReadTokens", 0)),
                                 "contextTokens": int(pressure.get("pressureTokens") or pressure.get("surfaceTokens") or 0),
                                 "contextWindow": int(pressure.get("contextWindow") or 0),
                                 "steps": int(stats.get("steps") or 0), "events": events,
                                 "dataMode": "projection"})
                grouped_tools = {}
                for event in events:
                    tool = grouped_tools.setdefault(event['message'], {'name': event['message'], 'calls': 0,
                        'lastAt': event['at'], 'lastCallId': event['id'], 'status': 'running',
                        'pending': 0, 'failed': 0, 'durationMs': None})
                    tool['calls'] += 1
                    tool['pending'] += 1
                sessions[-1]['tools'] = list(grouped_tools.values())
                sessions[-1]['usageSamples'] = []
            except (OSError, ValueError, TypeError, AttributeError):
                skipped += 1
                continue
        return sessions, f'{skipped} 个会话投影过大或暂不可读' if skipped else ''


class Collector:
    def __init__(self, process_detector=detect_processes, adapters=None, rate_reader=None,
                 background_process_probe=None):
        self.detector = process_detector
        # Injected lightweight detectors remain synchronous for embedders/tests.
        # The real Windows CIM/subprocess probe must never stall log collection.
        self.background_process_probe = (process_detector is detect_processes
            if background_process_probe is None else background_process_probe)
        self.adapters = adapters or {"codex": CodexAdapter(), "dsh": DSHAdapter()}
        self.lock = threading.Lock()
        self.previous = {}
        self.started = time.time()
        self.snapshot = {"sources": [], "sessions": [], "events": [], "history": [], "discovering": True}
        self.history = []
        self.last_process_at = 0
        self.processes = {"codex": False, "dsh": False}
        self.process_error = ""
        self.process_checked_at = 0
        self._process_lock = threading.Lock()
        self._process_thread = None
        self._process_result = None
        self.revision = 0
        self._semantic_state = None
        self._etag_epoch = secrets.token_hex(6)
        self.state_etag = f'W/"agt-{self._etag_epoch}-0"'
        self.encoded_snapshot = None
        self.rate_reader = rate_reader if rate_reader is not None else CCSwitchRateReader()

    def _detect_sources(self):
        try:
            processes = self.detector()
            return processes, '', time.time()
        except (OSError, ValueError, subprocess.SubprocessError):
            return None, '进程检测暂不可用', time.time()

    def _probe_sources(self):
        result = self._detect_sources()
        with self._process_lock:
            self._process_result = result

    def _refresh_processes(self, now):
        if self.background_process_probe:
            with self._process_lock:
                if self._process_result is not None:
                    processes, self.process_error, self.process_checked_at = self._process_result
                    if processes is not None:
                        self.processes = processes
                    self._process_result = None
                if self._process_thread is not None and not self._process_thread.is_alive():
                    self._process_thread = None
                if now - self.last_process_at >= PROCESS_POLL_SECONDS and self._process_thread is None:
                    self.last_process_at = now
                    self._process_thread = threading.Thread(target=self._probe_sources,
                        name='agt-source-probe', daemon=True)
                    self._process_thread.start()
            return
        if now - self.last_process_at >= PROCESS_POLL_SECONDS:
            processes, self.process_error, self.process_checked_at = self._detect_sources()
            if processes is not None:
                self.processes = processes
            self.last_process_at = now

    def poll(self):
        now = time.time()
        self._refresh_processes(now)
        sources, sessions, events = [], [], []
        delta_output = 0
        for key, name in (("codex", "Codex"), ("dsh", "DeepSeek Harness")):
            online = self.processes.get(key, False)
            note = self.process_error
            items = []
            if online:
                try:
                    items, adapter_note = self.adapters[key].collect(now)
                    note = ' / '.join(value for value in (note, adapter_note) if value)
                except (OSError, ValueError, sqlite3.Error):
                    note = "会话数据暂不可读，正在重试"
            # A stopped app contributes no live agents, tokens, or running tasks.
            running = sum(item["status"] in ("running", "active") for item in items)
            sources.append({"id": key, "name": name, "online": online, "running": running,
                            "sessions": len(items), "note": note,
                            "threads": sum(not item.get('isSubagent', False) for item in items),
                            "subagents": sum(bool(item.get('isSubagent')) for item in items),
                            "status": "running" if running else "idle" if online else "offline"})
            for item in items:
                identity = key + ":" + item["id"]
                previous = self.previous.get(identity)
                output = item.get("outputTokens", 0)
                speed = 0.0
                if previous:
                    delta = max(0, output - previous[0])
                    elapsed = max(.1, now - previous[1])
                    speed = delta / elapsed
                    delta_output += delta
                self.previous[identity] = (output, now)
                item["outputRate"] = round(speed, 1)
                item["outputRateMode"] = 'usage_poll_delta'
                # Old history is visible as idle, never presented as an active task.
                item["recent"] = now - item.get("lastAt", 0) < 86400
                sessions.append({k: v for k, v in item.items() if k not in ("events", "boundary")})
                events.extend(e for e in item.get("events", []) if now - e.get("at", 0) < 86400)
        current = {session['source'] + ':' + session['id'] for session in sessions}
        self.previous = {identity: value for identity, value in self.previous.items() if identity in current}
        sessions.sort(key=lambda s: (s["status"] in ("running", "active"), s["lastAt"]), reverse=True)
        request_rates = self.rate_reader.samples(sessions, now)
        for session in sessions:
            sample = best_sample(session.pop('_rateSample', None),
                                 request_rates.get(session['source'] + ':' + session['id']))
            session['outputRateInfo'] = rate_info(sample, active=session['status'] in ('running', 'active'),
                                                 now=now, session_id=session['id'], model=session.get('model'))
        # Deduplicate tool completion/call records and poll snapshots.
        dedup = {(e["source"], e["session"], e["at"], e["message"]): e for e in events}
        events = sorted(dedup.values(), key=lambda e: e["at"], reverse=True)[:30]
        active = sum(s["status"] in ("running", "active") for s in sessions)
        # A latest completed request is meaningful. Summing unrelated historical
        # per-request averages would falsely imply simultaneous live throughput.
        sampled_sessions = [s for s in sessions if s['outputRateInfo']['value'] is not None]
        latest_session = max(sampled_sessions, key=lambda s: s['outputRateInfo']['sampleAt'], default=None)
        total_rate = rate_info(latest_session['outputRateInfo'] if latest_session else None,
                              active=active > 0, now=now,
                              session_id=latest_session['id'] if latest_session else None,
                              model=latest_session.get('model') if latest_session else None)
        self.history.append({"at": now, "output": delta_output, "running": active})
        self.history = self.history[-90:]
        snapshot = {"app": "agt-command-center", "version": 3, "at": now, "startedAt": self.started,
                    "discovering": bool(self.background_process_probe and not self.process_checked_at),
                    "sources": sources, "sessions": sessions, "events": events,
                    "history": self.history, "totals": {"online": sum(s["online"] for s in sources),
                    "running": active, "sessions": len(sessions),
                    "threads": sum(not s.get('isSubagent', False) for s in sessions),
                    "subagents": sum(bool(s.get('isSubagent')) for s in sessions),
                    "inputTokens": sum(s.get("inputTokens", 0) for s in sessions),
                    "outputTokens": sum(s.get("outputTokens", 0) for s in sessions),
                    "outputRate": round(sum(s.get("outputRate", 0) for s in sessions), 1),
                    "outputRateMode": 'usage_poll_delta', "outputRateInfo": total_rate}}
        semantic = {key: snapshot[key] for key in ('discovering', 'sources', 'sessions', 'events', 'totals')}
        changed = semantic != self._semantic_state
        if changed:
            self.revision += 1
            self._semantic_state = semantic
            snapshot['topology'] = build_topology(sources, sessions)
        else:
            snapshot['topology'] = self.snapshot.get('topology', {'nodes': [], 'edges': []})
        snapshot['revision'] = self.revision
        with self.lock:
            self.snapshot = snapshot
            self.state_etag = f'W/"agt-{self._etag_epoch}-{self.revision}"'
            self.encoded_snapshot = None
        return snapshot

    def run(self, stop_event=None):
        while stop_event is None or not stop_event.is_set():
            started = time.monotonic()
            try:
                self.poll()
            except Exception:
                # Keep the last snapshot; its timestamp makes stale data explicit.
                pass
            delay = max(.05, COLLECTION_POLL_SECONDS - (time.monotonic() - started))
            if stop_event is None:
                time.sleep(delay)
            else:
                stop_event.wait(delay)


def make_handler(collector, wallpaper_token=None, input_bridge=None, renderer_recovery=None):
    def valid_wallpaper_token(supplied):
        # Browser mode can be running before the optional wallpaper is installed.
        # Read the local token per request so a late install/rotation takes effect.
        current = wallpaper_token
        if current is None:
            try:
                current = (ROOT / '.wallpaper-token').read_text(encoding='ascii').strip()
            except (OSError, UnicodeError):
                current = ''
        return bool(current) and secrets.compare_digest(supplied, current)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_OPTIONS(self):
            # File-based wallpapers have an opaque origin. The subsequent GET
            # must also carry the installation token before any data is exposed.
            allowed_headers = {'x-agt-token', 'content-type'}
            if urlsplit(self.path).path == '/api/state':
                allowed_headers.add('if-none-match')
            if (self.headers.get('Host', '').split(':')[0] not in ('localhost', '127.0.0.1')
                    or self.headers.get('Origin') != 'null'
                    or urlsplit(self.path).path not in ('/api/state', '/api/health', '/api/ui')
                    or self.headers.get('Access-Control-Request-Method') not in
                        (('GET', 'POST') if urlsplit(self.path).path == '/api/ui' else ('GET',))
                    or not {'x-agt-token'} <= set(self.headers.get('Access-Control-Request-Headers', '').lower().replace(' ', '').split(','))
                    or not set(self.headers.get('Access-Control-Request-Headers', '').lower().replace(' ', '').split(',')) <= allowed_headers):
                self.send_error(403)
                return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', 'null')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST' if urlsplit(self.path).path == '/api/ui' else 'GET')
            self.send_header('Access-Control-Allow-Headers', 'X-AGT-Token, Content-Type' +
                             (', If-None-Match' if urlsplit(self.path).path == '/api/state' else ''))
            self.send_header('Access-Control-Max-Age', '600')
            self.send_header('Vary', 'Origin')
            self.end_headers()

        def do_POST(self):
            if (self.headers.get('Host', '').split(':')[0] not in ('localhost', '127.0.0.1')
                    or self.headers.get('Origin') != 'null' or urlsplit(self.path).path != '/api/ui'
                    or not valid_wallpaper_token(self.headers.get('X-AGT-Token', ''))):
                self.send_error(403)
                return
            try:
                length=int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    raise ValueError('invalid body length')
                payload=json.loads(self.rfile.read(length))
                value=input_bridge.publish(payload) if input_bridge else {'active': False}
            except (ValueError, TypeError, KeyError):
                self.send_error(400)
                return
            body=json.dumps(value).encode('utf-8')
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', 'null')
            self.send_header('Vary', 'Origin')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            host = self.headers.get("Host", "").split(":")[0]
            if host not in ("localhost", "127.0.0.1"):
                self.send_error(403)
                return
            route = urlsplit(self.path).path
            state_etag = checked_at = None
            not_modified = False
            wallpaper_request = self.headers.get('Origin') == 'null'
            if wallpaper_request and (route not in ('/api/state', '/api/health', '/api/ui')
                    or not valid_wallpaper_token(self.headers.get('X-AGT-Token', ''))):
                self.send_error(403)
                return
            if route == '/api/ui':
                if not wallpaper_request:
                    self.send_error(403)
                    return
                client_id=parse_qs(urlsplit(self.path).query).get('clientId', [''])[0]
                body=json.dumps({'commands': input_bridge.drain(client_id) if input_bridge else []}).encode('utf-8')
                content_type='application/json; charset=utf-8'
            elif route == '/api/state':
                with collector.lock:
                    state_etag = collector.state_etag
                    checked_at = collector.snapshot.get('at', collector.started)
                    requested = {tag.strip() for tag in self.headers.get('If-None-Match', '').split(',')}
                    not_modified = state_etag in requested or '*' in requested
                    if not_modified:
                        body = b''
                    else:
                        if collector.encoded_snapshot is None:
                            collector.encoded_snapshot = json.dumps(collector.snapshot, ensure_ascii=False).encode('utf-8')
                        body = collector.encoded_snapshot
                content_type = 'application/json; charset=utf-8'
            elif route == '/api/health':
                value = {"app": "agt-command-center", "ok": True, "version": 11}
                if input_bridge and hasattr(input_bridge,'status'):
                    value['desktopControls']=input_bridge.status()
                if renderer_recovery:
                    value['rendererRecovery']=renderer_recovery.status()
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                # Only static app files and the original renderer can be served.
                allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
                           "/runtime.js": "runtime.js", "/command.css": "command.css",
                           "/screen.css": "screen.css",
                           "/reference.html": "vendor/haloflux/lab.html",
                           "/fonts/jetbrains-mono-400.ttf": "vendor/fonts/jetbrains-mono-400.ttf",
                           "/fonts/jetbrains-mono-600.ttf": "vendor/fonts/jetbrains-mono-600.ttf",
                           "/fonts/jetbrains-mono-800.ttf": "vendor/fonts/jetbrains-mono-800.ttf",
                           "/styles.css": "styles.css", "/halo.html": "halo.html", "/LICENSE": "vendor/haloflux/LICENSE"}
                relative = allowed.get(unquote(route))
                if relative is None:
                    self.send_error(404)
                    return
                try:
                    body = (ROOT / relative).read_bytes()
                except OSError:
                    self.send_error(404)
                    return
                content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
                if content_type.startswith("text/") or relative.endswith(".js"):
                    content_type += "; charset=utf-8"
            self.send_response(304 if not_modified else 200)
            self.send_header("Content-Type", content_type)
            if not not_modified:
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if state_etag is not None:
                self.send_header('ETag', state_etag)
                self.send_header('X-AGT-Checked-At', f'{checked_at:.6f}')
            if wallpaper_request:
                self.send_header('Access-Control-Allow-Origin', 'null')
                if state_etag is not None:
                    self.send_header('Access-Control-Expose-Headers', 'ETag, X-AGT-Checked-At')
                self.send_header('Vary', 'Origin')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    collector = Collector()
    from desktop_input import DesktopInputBridge
    input_bridge = DesktopInputBridge()
    renderer_recovery = None
    if os.name == 'nt':
        try:
            from install_wallpaper import find_engine
            from renderer_recovery import RendererRecovery
            engine = find_engine()
            renderer_recovery = RendererRecovery(engine, engine/'projects/myprojects/AGT-Live-Command',
                                                 input_bridge.recovery_state)
        except (OSError, ValueError, ImportError) as error:
            print('AGT renderer recovery unavailable: '+str(error), flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(
        collector, input_bridge=input_bridge, renderer_recovery=renderer_recovery))
    threading.Thread(target=collector.run, daemon=True).start()
    print(f"AGT command center: http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
