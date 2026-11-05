"""Normalize recorded tool calls into evidence-backed topology. No inferred links."""
from collections import defaultdict


def summarize_calls(records, session_id, stamp, short):
    calls = {}
    samples = []
    for event in records:
        payload = event.get('payload') or {}
        at = stamp(event.get('timestamp'))
        kind = payload.get('type')
        if event.get('type') == 'response_item':
            call_id = payload.get('call_id') or payload.get('id')
            if kind in ('function_call', 'custom_tool_call') and call_id:
                name = short(payload.get('name'), 100)
                if name:
                    calls[call_id] = {'id': str(call_id), 'tool': name, 'at': at,
                                      'startedAt': at, 'status': 'running', 'durationMs': None}
            elif kind in ('function_call_output', 'custom_tool_call_output') and call_id in calls:
                calls[call_id]['status'] = 'completed'
                calls[call_id]['at'] = at
                calls[call_id]['durationMs'] = max(0, round((at-calls[call_id]['startedAt'])*1000))
        if event.get('type') == 'event_msg' and kind == 'item_completed':
            item = payload.get('item') or {}
            item_kind = item.get('type')
            if item_kind not in ('McpToolCall', 'CommandExecution', 'DynamicToolCall', 'WebSearch'):
                continue
            name = item.get('tool') or item.get('tool_name') or item.get('name')
            if isinstance(name, dict):
                name = name.get('name')
            if not name:
                name = {'CommandExecution': 'exec_command', 'WebSearch': 'web_search',
                        'McpToolCall': 'mcp_tool', 'DynamicToolCall': 'dynamic_tool'}[item_kind]
            call_id = str(item.get('call_id') or item.get('id') or f'{session_id}:{at}:{name}')
            started = stamp(payload.get('started_at_ms'))
            ended = stamp(payload.get('completed_at_ms'))
            calls[call_id] = {'id': call_id, 'tool': short(name, 100), 'at': at,
                              'startedAt': started or at,
                              'status': 'failed' if item.get('status') == 'failed' or item.get('exit_code') not in (None, 0) else item.get('status', 'completed'),
                              'durationMs': max(0, round((ended-started)*1000)) if started and ended else None}
        if event.get('type') == 'event_msg' and kind == 'token_count':
            info = payload.get('info') or {}
            total = info.get('total_token_usage') or {}
            last = info.get('last_token_usage') or {}
            samples.append({'at': at, 'output': int(total.get('output_tokens') or 0),
                            'input': int(total.get('input_tokens') or 0),
                            'context': int(last.get('input_tokens') or 0)})
    grouped = defaultdict(list)
    for call in calls.values():
        grouped[call['tool']].append(call)
    tools = []
    for name, records_for_tool in grouped.items():
        records_for_tool.sort(key=lambda c: c['at'])
        last = records_for_tool[-1]
        durations = [c['durationMs'] for c in records_for_tool if c['durationMs'] is not None]
        tools.append({'name': name, 'calls': len(records_for_tool), 'lastAt': last['at'],
                      'lastCallId': last['id'], 'status': last['status'],
                      'pending': sum(c['status'] in ('running', 'in_progress', 'pending') for c in records_for_tool),
                      'failed': sum(c['status'] == 'failed' for c in records_for_tool),
                      'durationMs': durations[-1] if durations else None})
    tools.sort(key=lambda t: t['lastAt'], reverse=True)
    events = [{'id': c['id'], 'at': c['at'], 'source': 'codex', 'session': session_id,
               'kind': 'tool', 'message': c['tool'], 'status': c['status'], 'durationMs': c['durationMs']}
              for c in sorted(calls.values(), key=lambda c: c['at'], reverse=True)[:30]]
    return {'tools': tools[:24], 'usageSamples': samples[-40:], 'events': events}


def build_topology(sources, sessions):
    nodes, edges = [], []
    online_sources = {source['id'] for source in sources if source['online']}
    sessions = [session for session in sessions if session['source'] in online_sources]
    session_ids = {f"session:{s['source']}:{s['id']}" for s in sessions}
    for source in sources:
        if source['online']:
            nodes.append({'id': 'source:'+source['id'], 'kind': 'source', 'source': source['id'],
                          'label': source['name'], 'status': source['status']})
    for session in sessions:
        sid = f"session:{session['source']}:{session['id']}"
        nodes.append({'id': sid, 'kind': 'session', 'source': session['source'],
                      'sessionId': session['id'], 'label': session['title'], 'status': session['status']})
        edges.append({'id': 'owner:'+sid, 'from': 'source:'+session['source'], 'to': sid,
                      'kind': 'source-session', 'sessionId': session['id'], 'source': session['source'],
                      'status': session['status'], 'evidence': 'session index'})
        parent = session.get('parentId')
        parent_id = f"session:{session['source']}:{parent}"
        if parent and parent_id in session_ids:
            edges.append({'id': 'spawn:'+sid, 'from': parent_id, 'to': sid, 'kind': 'parent-child',
                          'sessionId': session['id'], 'source': session['source'],
                          'status': session['status'], 'evidence': 'thread_spawn_edges'})
        for tool in session.get('tools', [])[:12]:
            tid = f"tool:{session['source']}:{session['id']}:{tool['name']}"
            nodes.append({'id': tid, 'kind': 'tool', 'source': session['source'], 'sessionId': session['id'],
                          'label': tool['name'], 'status': tool['status']})
            edges.append({'id': 'call:'+tid, 'from': sid, 'to': tid, 'kind': 'tool-call',
                          'source': session['source'], 'sessionId': session['id'], 'status': tool['status'],
                          'calls': tool['calls'], 'lastAt': tool['lastAt'],
                          'durationMs': tool.get('durationMs'), 'evidence': 'recorded tool call'})
    return {'nodes': nodes, 'edges': edges}
