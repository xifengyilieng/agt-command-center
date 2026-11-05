import unittest
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from telemetry import summarize_calls,build_topology
from server import stamp,short


class TelemetryTests(unittest.TestCase):
    def test_call_and_result_form_one_observed_relation(self):
        records=[
            {'timestamp':10,'type':'response_item','payload':{'type':'function_call','call_id':'call-1','name':'read_file','arguments':'PRIVATE INPUT'}},
            {'timestamp':12,'type':'response_item','payload':{'type':'function_call_output','call_id':'call-1','output':'PRIVATE RESULT'}},
        ]
        data=summarize_calls(records,'session-1',stamp,short)
        self.assertEqual(len(data['tools']),1)
        self.assertEqual(data['tools'][0]['calls'],1)
        self.assertEqual(data['tools'][0]['status'],'completed')
        self.assertEqual(data['tools'][0]['durationMs'],2000)
        self.assertNotIn('PRIVATE',str(data))

    def test_pending_and_failed_are_not_randomized(self):
        records=[{'timestamp':10,'type':'response_item','payload':{'type':'function_call','call_id':'pending','name':'long_tool'}},
                 {'timestamp':12,'type':'event_msg','payload':{'type':'item_completed','started_at_ms':10000,'completed_at_ms':12000,'item':{'type':'CommandExecution','id':'actual-command','status':'failed','exit_code':1}}}]
        data=summarize_calls(records,'s',stamp,short)
        tools={t['name']:t for t in data['tools']}
        self.assertEqual(tools['long_tool']['pending'],1)
        self.assertEqual(tools['exec_command']['failed'],1)

    def test_topology_contains_only_recorded_parent_and_tool_links(self):
        sources=[{'id':'codex','name':'Codex','online':True,'status':'running'}]
        sessions=[{'id':'parent','source':'codex','title':'主任务','status':'running','tools':[]},
                  {'id':'child','source':'codex','title':'子任务','status':'idle','parentId':'parent','tools':[{'name':'real_tool','calls':3,'lastAt':12,'status':'completed'}]},
                  {'id':'orphan','source':'codex','title':'无父记录','status':'idle','parentId':'missing','tools':[]}]
        data=build_topology(sources,sessions)
        ids={n['id'] for n in data['nodes']}
        self.assertEqual(sum(e['kind']=='parent-child' for e in data['edges']),1)
        self.assertEqual(sum(e['kind']=='tool-call' for e in data['edges']),1)
        self.assertNotIn('session:codex:missing',ids)
        self.assertTrue(all(e['from'] in ids and e['to'] in ids for e in data['edges']))
        self.assertEqual(next(e['calls'] for e in data['edges'] if e['kind']=='tool-call'),3)

    def test_closed_source_removes_its_nodes_and_lines(self):
        sources=[{'id':'codex','name':'Codex','online':False,'status':'offline'}]
        sessions=[{'id':'old','source':'codex','title':'历史记录','status':'running','tools':[]}]
        self.assertEqual(build_topology(sources,sessions),{'nodes':[],'edges':[]})


if __name__=='__main__':unittest.main()
