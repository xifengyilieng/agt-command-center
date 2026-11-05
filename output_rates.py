"""Completed-request throughput, never a claim of live token decoding.

CC Switch's reference definitions:
https://github.com/farion1231/cc-switch/blob/main/src/components/usage/format.ts
https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/services/session_usage_codex.rs

Only allowlisted token/timing identifiers are read from its optional local DB.
Session-log timing is an estimate including first-token waiting time.
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import sqlite3
import time


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def request_sample(output, elapsed_ms, at, source, *, estimated, first_token_ms=None, model=None):
    output, elapsed_ms, at = number(output), number(elapsed_ms), number(at)
    if output is None or elapsed_ms is None or at is None or at <= 0:
        return None
    if estimated:
        # Same safeguards as CC Switch: short tool calls are a poor speed sample.
        if output < 200 or elapsed_ms < 1000:
            return None
        window = elapsed_ms
    else:
        first = number(first_token_ms)
        if first is None or first < 0 or output < 100:
            return None
        window = elapsed_ms - first
        if window < 100:
            return None
    return {'value': round(output * 1000 / window, 1),
            'kind': 'estimated_request_average' if estimated else 'request_average',
            'sampleAt': at, 'windowMs': round(window), 'outputTokens': int(output),
            'source': source, 'includesFirstTokenWait': estimated,
            'model': str(model)[:100] if model else None, 'sampleCount': 1}


def codex_request_sample(records, stamp):
    """Infer boundaries from already-loaded records; do not count polling gaps.

    A first cumulative snapshot alone is a baseline, not a request. A request
    must have output usage and an observed preceding request/turn/tool boundary.
    """
    boundary = start = last_output = last_tool_after_output = None
    last_finished = None
    previous_total = None
    latest = None
    model = None
    seen_responses = set()
    for event in records:
        payload = event.get('payload') or {}
        if not isinstance(payload, dict):
            continue
        kind, subkind = event.get('type'), payload.get('type')
        at = stamp(event.get('timestamp'))
        if not at:
            continue
        if kind == 'turn_context':
            boundary = max(boundary or 0, at)
            model = payload.get('model') or model
        if kind == 'event_msg' and subkind in ('task_started', 'turn_started', 'turn_aborted', 'task_interrupted'):
            boundary, start, last_output, last_tool_after_output = at, None, None, None
        if kind == 'event_msg' and subkind == 'thread_settings_applied':
            settings = payload.get('thread_settings') or {}
            model = settings.get('model') or model
        is_tool_output = kind == 'response_item' and isinstance(subkind, str) and subkind.endswith('_output')
        is_message = kind == 'response_item' and subkind == 'message'
        if is_tool_output or (is_message and payload.get('role') != 'assistant'):
            boundary = max(boundary or 0, at)
            if is_tool_output and last_output is not None:
                last_tool_after_output = at
        is_model_output = kind == 'response_item' and (
            subkind == 'reasoning' or (is_message and payload.get('role') == 'assistant')
            or (isinstance(subkind, str) and subkind.endswith('_call')))
        item = payload.get('item') or {}
        if kind == 'event_msg' and subkind == 'item_completed' and isinstance(item, dict):
            is_model_output = item.get('type') in ('Reasoning', 'AgentMessage')
        if is_model_output and not (start is None and last_finished is not None and 0 <= at - last_finished <= .1):
            if start is None:
                start = boundary
            last_output, last_tool_after_output = at, None

        usage = None
        modern = kind == 'token_usage_record'
        if modern:
            response_id = payload.get('response_id')
            if response_id and response_id in seen_responses:
                continue
            if response_id:
                seen_responses.add(response_id)
            usage = payload.get('usage')
            total = number((payload.get('thread_token_usage') or {}).get('output_tokens'))
        elif kind == 'event_msg' and subkind == 'token_count':
            info = payload.get('info') or {}
            total = number((info.get('total_token_usage') or {}).get('output_tokens'))
            if total is not None and previous_total == total:
                continue  # mirrored token_count after token_usage_record/rate-limit refresh
            usage = info.get('last_token_usage')
            if not isinstance(usage, dict) and total is not None and previous_total is not None:
                usage = {'output_tokens': max(0, total - previous_total)}
            if last_finished is not None and 0 <= at - last_finished <= .1:
                previous_total = total if total is not None else previous_total
                continue
        else:
            continue
        previous_total = total if total is not None else previous_total
        if not isinstance(usage, dict):
            # A cumulative-only first event establishes a baseline for the next.
            boundary, start, last_output, last_tool_after_output = at, None, None, None
            continue
        end = at
        if not modern and last_tool_after_output is not None and at - last_tool_after_output <= .1:
            end = last_output or at
        request_start = start if start is not None else boundary
        if request_start is not None:
            sample = request_sample(usage.get('output_tokens'), (end - request_start) * 1000,
                                    end, 'codex-log', estimated=True, model=model)
            if sample:
                latest = sample
        boundary, start, last_output, last_tool_after_output, last_finished = at, None, None, None, at
    return latest


class CCSwitchRateReader:
    """Optional, bounded, read-only lookup of completed request timing.

    The DB may contain credentials in other tables. No SELECT * and no other
    tables are read. The session identifier inside codex_session's request ID
    is the actual thread; its session_id column may refer to a parent instead.
    """
    COLUMNS = ('request_id', 'app_type', 'output_tokens', 'latency_ms', 'first_token_ms',
               'status_code', 'session_id', 'is_streaming', 'created_at', 'data_source')

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path.home() / '.cc-switch' / 'cc-switch.db'
        self.cached = []
        self.checked_at = -math.inf

    def samples(self, sessions, now):
        if now - self.checked_at >= 4:
            self.checked_at = now
            self.cached = self._read(now)
        wanted = {s['source'] + ':' + s['id'] for s in sessions}
        result = {}
        for row in self.cached:
            source = row.get('app_type')
            if source not in ('codex', 'dsh') or row.get('status_code') != 200:
                continue
            session_id = row.get('session_id')
            data_source = row.get('data_source')
            if source == 'codex' and data_source == 'codex_session':
                match = re.match(r'^codex_session:thread-v1:([0-9a-fA-F-]{36}):', str(row.get('request_id', '')))
                if not match:
                    continue  # cannot safely attribute imported/grouped older schemas
                session_id = match.group(1)
            identity = source + ':' + str(session_id)
            if identity not in wanted:
                continue
            estimated = bool(data_source and data_source != 'proxy')
            if estimated and data_source not in ('codex_session', 'dsh_session'):
                continue
            sample = request_sample(row.get('output_tokens'), row.get('latency_ms'), row.get('created_at'),
                                    'cc-switch', estimated=estimated,
                                    first_token_ms=row.get('first_token_ms'))
            if sample and identity not in result:
                result[identity] = sample
        return result

    def _read(self, now):
        if not self.path.is_file():
            return []
        conn = None
        try:
            conn = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.025)
            conn.execute('PRAGMA query_only=ON')
            deadline = time.perf_counter() + .04
            conn.set_progress_handler(lambda: int(time.perf_counter() > deadline), 1000)
            columns = {r[1] for r in conn.execute('PRAGMA table_info(proxy_request_logs)')}
            if not {'app_type', 'output_tokens', 'latency_ms', 'session_id', 'created_at'} <= columns:
                return []
            fields = ','.join('"'+name+'"' if name in columns else 'NULL AS "'+name+'"' for name in self.COLUMNS)
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT '+fields+' FROM proxy_request_logs WHERE created_at>=? '
                                "AND app_type IN ('codex','dsh') ORDER BY created_at DESC LIMIT 512", (now - 3600,))
            return [dict(r) for r in rows]
        except (OSError, sqlite3.Error):
            return []
        finally:
            if conn is not None:
                conn.close()


def best_sample(*samples):
    valid = [s for s in samples if s and number(s.get('value')) is not None]
    if not valid:
        return None
    return max(valid, key=lambda s: (s['sampleAt'], s['kind'] == 'request_average'))


def rate_info(sample, *, active, now, session_id=None, model=None):
    info = dict(sample) if sample else {'value': None, 'kind': 'unavailable', 'sampleAt': None,
        'windowMs': None, 'outputTokens': None, 'source': None, 'includesFirstTokenWait': None, 'sampleCount': 0}
    info['sessionId'] = session_id
    info['model'] = info.get('model') or model
    if not active:
        info.update(status='idle', reason='任务空闲；所示为最近完成请求均速' if sample else '任务空闲，暂无有效请求速度样本')
    elif not sample:
        info.update(status='waiting', reason='等待请求完成后的 usage 与有效耗时；累计用量不是实时解码速度')
    elif now - sample['sampleAt'] > 120:
        info.update(status='waiting', reason='等待新请求 usage；所示为上次完成请求均速')
    else:
        info.update(status='recent', reason=('会话日志估算：输出 / 请求整段耗时，包含首字等待'
                                            if sample['includesFirstTokenWait'] else '已完成代理请求：输出 /（总耗时－首字等待）'))
    return info
