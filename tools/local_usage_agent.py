"""SSH-only, idempotent numeric usage uploader. No HTTP write endpoint."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from history_store import HistoryStore
from local_usage import LocalUsageScanner

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--ingest', action='store_true')
    p.add_argument('--db',type=Path,required=True)
    p.add_argument('--codex-home',type=Path,default=Path.home()/'.codex')
    p.add_argument('--ssh-host')
    p.add_argument('--once',action='store_true')
    p.add_argument('--log-file',type=Path)
    a=p.parse_args()
    if a.log_file:
        sys.stdout = a.log_file.open("a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    if a.ingest:
        raw=sys.stdin.buffer.read(262145)
        if len(raw)>262144:raise ValueError('batch too large')
        data=json.loads(raw)
        store=HistoryStore(a.db)
        try: store.record_local_events(data['events'],data['source']);print(json.dumps({'ok':True,'count':len(data['events'])}))
        finally:store.close()
        return
    if not a.ssh_host:p.error('--ssh-host required')
    scanner=LocalUsageScanner(a.db,a.codex_home)
    try:
        while True:
            try:
                scanner.scan()
                while True:
                    events = scanner.pending()
                    body=json.dumps({'source':'windows','events':events},separators=(',',':'))
                    command='/opt/homebrew/bin/python3 /Users/qqice/Stopwatch-Micro/tools/local_usage_agent.py --ingest --db /Users/qqice/Stopwatch-Micro/private/history.sqlite3'
                    r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12',a.ssh_host,command],input=body,text=True,capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if r.returncode or json.loads(r.stdout)!= {'ok':True,'count':len(events)}:raise RuntimeError('SSH ingest failed')
                    if not events: break  # heartbeat, even when no completions
                    scanner.acknowledge(events)
                    print('LOCAL_USAGE delivered',len(events),flush=True)
            except Exception as e:
                print('LOCAL_USAGE error',type(e).__name__,flush=True)
                if a.once:raise
            if a.once:break
            time.sleep(60)
    finally:scanner.close()

if __name__=='__main__': main()
