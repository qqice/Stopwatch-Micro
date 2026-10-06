"""Official quota trend storage/projection/API; no login, device or network outside loopback."""
from pathlib import Path
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history_store import HistoryStore
import history_store
from quota_dashboard import normalize_quota_dashboard
from quota_trend import project, weekly_snapshot
import quota_service

NOW = 1780001234

def dashboard(epoch=NOW, bp=6000, reset=NOW+604800, ident="codex"):
    return normalize_quota_dashboard({"rateLimitsByLimitId": {ident: {"primary": {
        "usedPercent": 100-bp/100, "windowDurationMins": 10080, "resetsAt": reset}}}}, epoch)

class QuotaTrendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.now=NOW; self.mono=10
        self.path=Path(self.temp.name)/'history.sqlite3'
        self.store=HistoryStore(self.path, now=lambda:self.now, monotonic_now=lambda:self.mono)
    def tearDown(self):
        self.store.close(); self.temp.cleanup()
    def test_official_first_bucket_weekly_only(self):
        raw={"rateLimitsByLimitId":{"other":{"primary":{"usedPercent":1,"windowDurationMins":10080}},
             "codex":{"primary":{"usedPercent":2,"windowDurationMins":300},
                       "secondary":{"usedPercent":12.34,"windowDurationMins":10080,"resetsAt":NOW+4}}}}
        self.assertEqual(weekly_snapshot(normalize_quota_dashboard(raw,NOW)),(NOW,'codex',8766,NOW+4))
        del raw['rateLimitsByLimitId']['codex']['secondary']
        self.assertIsNone(weekly_snapshot(normalize_quota_dashboard(raw,NOW)))
        del raw['rateLimitsByLimitId']['codex']
        self.assertIsNone(weekly_snapshot(normalize_quota_dashboard(raw,NOW)))
        ambiguous={'rateLimitsByLimitId':{'one':{'limitId':'codex','primary':{'usedPercent':1,'windowDurationMins':10080}},
                                        'two':{'limitId':'codex','primary':{'usedPercent':2,'windowDurationMins':10080}}}}
        self.assertIsNone(weekly_snapshot(normalize_quota_dashboard(ambiguous,NOW)))
        # Bool official percentages/durations are invalid, not 0/1 aliases.
        raw['rateLimitsByLimitId']['codex']={'primary':{'usedPercent':True,'windowDurationMins':10080}}
        self.assertIsNone(weekly_snapshot(normalize_quota_dashboard(raw,NOW)))
    def test_empty_schema_cold_one_zero_and_full(self):
        empty=self.store.response_v2()
        self.assertFalse(empty['available']); self.assertFalse(empty['token_available'])
        trend=empty['quota_trend']
        self.assertEqual((len(trend['hours']),len(trend['days'])),(25,8))
        self.assertTrue(all(p['remaining_bp'] is None for p in trend['hours']))
        self.assertTrue(self.store.record_quota(dashboard(bp=0)))
        trend=self.store.response_v2()['quota_trend']
        self.assertTrue(trend['available']); self.assertEqual(trend['captured_epoch'],NOW)
        self.assertEqual([p['remaining_bp'] for p in trend['hours'] if p['remaining_bp'] is not None],[0])
        self.now+=60; self.assertTrue(self.store.record_quota(dashboard(self.now,bp=10000)))
        self.assertEqual(self.store.quota_response()['hours'][-1]['remaining_bp'],10000)
    def test_validation_idempotence_outoforder_and_retention(self):
        first=dashboard(); self.assertTrue(self.store.record_quota(first))
        self.mono+=20; self.assertTrue(self.store.record_quota(first))
        self.assertEqual(self.store.quota_response()['age_seconds'],20)
        self.assertFalse(self.store.record_quota(dashboard(bp=5000)))
        self.assertFalse(self.store.record_quota(dashboard(NOW-1)))
        self.assertFalse(self.store.record_quota(dashboard(NOW+1)))
        bad=dashboard(); bad['captured_epoch']=True; self.assertFalse(self.store.record_quota(bad))
        bad=dashboard(); bad['buckets'][0]['windows'][0]['remaining_bp']=True; self.assertFalse(self.store.record_quota(bad))
        self.now+=60; self.assertTrue(self.store.record_quota(dashboard(self.now)))
        self.assertTrue(self.store.record_quota(first))
        self.now+=91*86400; self.assertTrue(self.store.record_quota(dashboard(self.now)))
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM quota_remaining').fetchone()[0],1)
    def test_restart_historical_age_monotonic_and_no_backfill(self):
        self.store.record_quota(dashboard()); self.store.close()
        self.now+=400; self.store=HistoryStore(self.path,now=lambda:self.now,monotonic_now=lambda:self.mono)
        trend=self.store.quota_response(); self.assertEqual(trend['age_seconds'],400)
        self.now-=100; self.mono+=30
        trend=self.store.quota_response(); self.assertEqual(trend['age_seconds'],430)
        self.assertTrue(trend['available']); self.assertEqual(sum(p['remaining_bp'] is not None for p in trend['days']),1)
    def test_retained_but_outside_rolling_week_is_not_available(self):
        self.store.record_quota(dashboard())
        self.now+=8*86400; self.mono+=8*86400
        trend=self.store.quota_response()
        self.assertFalse(trend['available']); self.assertEqual(trend['captured_epoch'],NOW)
        self.assertEqual(trend['age_seconds'],8*86400); self.assertEqual(trend['limit_id'],'codex')
        self.assertTrue(all(p['remaining_bp'] is None for p in trend['days']))
        self.assertFalse(self.store.response_v2()['available'])
    def test_rolling_edges_latest_snapshot_and_no_future(self):
        start=NOW-86400
        rows=[(start-1,'codex',1,10),(start,'codex',2,10),(start+3,'codex',3,10),
              (NOW-10,'codex',4,10),(NOW,'codex',5,10),(NOW+1,'codex',6,10)]
        result=project(rows,NOW)
        hours=result['hours']; self.assertEqual(hours[0]['epoch'],start+3)
        self.assertEqual(hours[0]['remaining_bp'],3); self.assertEqual(hours[-1]['remaining_bp'],5)
        for key,lower in [('hours',start),('days',NOW-604800)]:
            epochs=[p['epoch'] for p in result[key]]
            self.assertEqual(epochs,sorted(set(epochs)))
            self.assertTrue(all(lower<=e<=NOW for e in epochs))
        self.assertEqual(result['end_epoch'],NOW)
    def test_utc8_midnight_partial_boundaries(self):
        midnight=((NOW+480*60)//86400)*86400-480*60
        result=project([(midnight-1,'codex',7000,1),(midnight+1,'codex',6000,1)],midnight+60)
        self.assertEqual(result['days'][-2]['epoch'],midnight-1)
        self.assertEqual(result['days'][-1]['epoch'],midnight+1)
        self.assertEqual(result['start_7d_epoch'],midnight+60-604800)
        self.assertEqual(result['days'][0]['epoch'],result['start_7d_epoch'])
    def test_gap_reset_hidden_bucket_transition_and_limit_changes(self):
        rows=[(NOW-500,'codex',8000,1),(NOW-319,'codex',7000,1),
              (NOW-250,'codex',9000,2),(NOW-200,'other',4000,2),
              (NOW-100,'codex',6000,2),(NOW,'codex',5900,2)]
        result=project(rows,NOW); last=result['hours'][-1]
        self.assertTrue(last['break_before']); self.assertTrue(last['reset_before'])
        self.assertEqual(last['remaining_bp'],5900)
        rows.append((NOW+1,'other',3000,3))
        result=project(rows,NOW+1)
        self.assertEqual(result['limit_id'],'other')
        self.assertTrue(all(p['remaining_bp'] not in (5900,6000,9000,7000,8000) for p in result['days']))
    def test_regular_sampling_not_false_hourly_gap(self):
        rows=[(NOW-7200+i*60,'codex',10000-i,1) for i in range(121)]
        points=[p for p in project(rows,NOW)['hours'] if p['remaining_bp'] is not None]
        self.assertFalse(points[-1]['break_before'])
    def test_additive_schema_preserves_v1_tokens_and_sql_error(self):
        self.store.record_usage({'summary':{'lifetimeTokens':42}},NOW)
        v1=self.store.response(); self.store.record_quota(dashboard())
        v2=self.store.response_v2()
        for key,value in v1.items():
            if key not in ('version','available'): self.assertEqual(v2[key],value)
        self.assertEqual(self.store.response(),v1)
        self.assertTrue(v2['token_available'])
        self.store._db.execute('DROP TABLE quota_remaining')
        with contextlib.redirect_stderr(io.StringIO()): v2=self.store.response_v2()
        self.assertTrue(v2['token_available']); self.assertFalse(v2['quota_trend']['available'])
    def test_collector_usage_failure_and_sql_error_keep_rate_cache(self):
        class Client:
            def start(self): pass
            def close(self): pass
            def _request(self,method):
                if method=='account/usage/read': raise quota_service.BridgeError('usage unavailable')
                return {'rateLimits':{'primary':{'usedPercent':40,'windowDurationMins':10080,'resetsAt':NOW+100}}}
        rate=quota_service.SnapshotStore(wall_now=lambda:self.now)
        collector=quota_service.QuotaCollector(Client,rate,self.store)
        with patch.object(quota_service.time,'time',return_value=NOW), contextlib.redirect_stderr(io.StringIO()): collector.poll_once()
        self.assertTrue(self.store.quota_response()['available']); self.assertTrue(rate.status()[1])
        with patch.object(self.store,'record_quota',side_effect=sqlite3.OperationalError('secret should not print')), patch.object(quota_service.time,'time',return_value=NOW), contextlib.redirect_stderr(io.StringIO()) as output:
            collector.poll_once()
        self.assertTrue(rate.status()[1]); self.assertNotIn('secret',output.getvalue())
    def test_optional_trend_ddl_failure_preserves_token_store_and_collector(self):
        self.store.record_usage({'summary':{'lifetimeTokens':42}},NOW)
        self.store.close()
        connect=sqlite3.connect
        class DdlFailure:
            def __init__(self,*args,**kwargs): self.db=connect(*args,**kwargs)
            def execute(self,sql,*args):
                if sql.startswith('CREATE TABLE IF NOT EXISTS quota_remaining'):
                    raise sqlite3.OperationalError('private diagnostics must not be printed')
                return self.db.execute(sql,*args)
            def __getattr__(self,key): return getattr(self.db,key)
            def __enter__(self): self.db.__enter__(); return self
            def __exit__(self,*args): return self.db.__exit__(*args)
        with patch.object(history_store.sqlite3,'connect',side_effect=DdlFailure), contextlib.redirect_stderr(io.StringIO()) as output:
            self.store=HistoryStore(self.path,now=lambda:self.now,monotonic_now=lambda:self.mono)
        self.assertNotIn('private diagnostics',output.getvalue())
        self.assertTrue(self.store.response()['available'])
        self.assertFalse(self.store.record_quota(dashboard()))
        self.assertFalse(self.store.quota_response()['available'])
        self.assertTrue(self.store.response_v2()['token_available'])
        class Client:
            def start(self): pass
            def close(self): pass
            def _request(self,method):
                if method=='account/usage/read': return {'summary':{'lifetimeTokens':43}}
                return {'rateLimits':{'primary':{'usedPercent':40,'windowDurationMins':10080,'resetsAt':NOW+100}}}
        rate=quota_service.SnapshotStore(wall_now=lambda:self.now)
        collector=quota_service.QuotaCollector(Client,rate,self.store)
        with patch.object(quota_service.time,'time',return_value=NOW): collector.poll_once()
        self.assertTrue(rate.status()[1]); self.assertTrue(self.store.response()['available'])
    def test_authenticated_v2_history_empty_token_legacy_and_payload(self):
        token='0123456789abcdef'
        server=quota_service.ThreadingHTTPServer(('127.0.0.1',0),quota_service.make_handler(quota_service.SnapshotStore(),token,self.store))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def get(path,auth=True):
            request=urllib.request.Request(base+path,headers={'Authorization':'Bearer '+token} if auth else {})
            try:
                with urllib.request.urlopen(request,timeout=2) as response: return response.status,response.read()
            except urllib.error.HTTPError as error: return error.code,error.read()
        try:
            self.assertEqual(get('/v2/history',False)[0],401)
            self.assertEqual(get('/v2/history?x=1')[0],404)
            status,payload=get('/v2/history'); self.assertEqual(status,200)
            body=json.loads(payload); self.assertFalse(body['available']); self.assertEqual(body['version'],2)
            self.store.record_quota(dashboard())
            status,payload=get('/v2/history'); body=json.loads(payload)
            self.assertTrue(body['available']); self.assertFalse(body['token_available']); self.assertLessEqual(len(payload),32768)
            self.assertEqual(json.loads(get('/v1/history')[1])['version'],1)
            self.assertNotIn('quota_trend',json.loads(get('/v1/history')[1]))
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

if __name__=='__main__': unittest.main()
