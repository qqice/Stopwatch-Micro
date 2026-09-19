import unittest,tempfile,json,datetime
from pathlib import Path
from local_usage import LocalUsageScanner,event_from_record
from history_store import HistoryStore

NOW=1789822800

def record(total,last,stamp='2026-09-19T12:30:00Z'):
 return {'timestamp':stamp,'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':total},'last_token_usage':{'total_tokens':last}}}}

class LocalUsageTests(unittest.TestCase):
 def test_repeated_notification_and_partial_baseline(self):
  event,previous=event_from_record(record(1000,50),None)
  self.assertEqual(event['tokens'],50)
  self.assertIsNone(event_from_record(record(1000,50,'2026-09-19T12:31:00Z'),previous)[0])
  self.assertIsNotNone(event_from_record(record(1050,50),previous)[0])
  self.assertIsNotNone(event_from_record(record(30,30),previous)[0])
 def test_copied_logs_and_restart_do_not_duplicate(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder=root/'sessions';folder.mkdir()
   data=json.dumps(record(50,50))+'\n'
   (folder/'a.jsonl').write_text(data)
   (folder/'b.jsonl').write_text(data)
   s=LocalUsageScanner(root/'cursor.db',root,now=lambda:NOW);s.scan()
   self.assertEqual(len(s.pending()),1);s.close()
   s=LocalUsageScanner(root/'cursor.db',root,now=lambda:NOW);s.scan();self.assertEqual(len(s.pending()),1)
   s.acknowledge(s.pending());s.scan();self.assertEqual(s.pending(),[]);s.close()
 def test_partial_line_waits_and_truncation_rescans(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder=root/'sessions';folder.mkdir();p=folder/'a.jsonl'
   data=json.dumps(record(50,50));p.write_text(data)
   s=LocalUsageScanner(root/'cursor.db',root,now=lambda:NOW);s.scan();self.assertEqual(s.pending(),[])
   p.write_text(data+'\n');s.scan();self.assertEqual(len(s.pending()),1);s.close()
 def test_source_dedupe_hour_boundary_and_delayed_counter(self):
  with tempfile.TemporaryDirectory() as t:
   s=HistoryStore(Path(t)/'h.db',now=lambda:NOW)
   a,_=event_from_record(record(50,50,'2026-09-19T12:59:59Z'),None)
   b,_=event_from_record(record(100,50,'2026-09-19T13:00:00Z'),50)
   s.record_local_events([a,b],'windows');s.record_local_events([a,b],'mac')
   s.record_usage({'summary':{'lifetimeTokens':1000}},NOW-60)
   s.record_usage({'summary':{'lifetimeTokens':9000}},NOW)
   r=s.response();h={x['label']:x for x in r['hours']}
   self.assertEqual(h['2026-09-19 20:00']['tokens'],50)
   self.assertEqual(h['2026-09-19 21:00']['tokens'],50)
   self.assertEqual(h['2026-09-19 21:00']['quality'],'local')
   self.assertEqual(r['days'][-1]['tokens'],100)
   self.assertEqual(r['days'][-1]['quality'],'local');s.close()
 def test_invalid_batch_is_atomic(self):
  with tempfile.TemporaryDirectory() as t:
   s=HistoryStore(Path(t)/'h.db',now=lambda:NOW);a,_=event_from_record(record(50,50),None)
   with self.assertRaises(ValueError):s.record_local_events([a,{'id':'x','epoch':NOW,'tokens':-1}],'windows')
   self.assertEqual(s._db.execute('select count(*) from local_events').fetchone()[0],0);s.close()

if __name__=='__main__':unittest.main()
