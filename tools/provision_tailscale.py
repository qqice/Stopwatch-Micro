"""Provision a tailnet registration key over USB without displaying it."""
import argparse,base64,json
from pathlib import Path
from serial_debug_test import DebugClient

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--key-file',type=Path,required=True)
    p.add_argument('--url',required=True,help='http://100.x.y.z:8765/v1/status inside WireGuard')
    p.add_argument('--port',required=True)
    a=p.parse_args()
    key=a.key_file.read_text(encoding='utf-8-sig').strip()
    if not key.startswith('tskey-auth-'):raise SystemExit('Invalid Tailscale auth key file')
    data=base64.b64encode(json.dumps({'auth_key':key,'url':a.url},separators=(',',':')).encode()).decode()
    c=DebugClient(a.port)
    try:
        c.handshake();r=c.command('debug tailscale-config '+data,'tailscale-config',8)
        if r.status!='PASS':raise SystemExit('Tailnet configuration rejected')
    finally:c.close()

if __name__=='__main__':main()
