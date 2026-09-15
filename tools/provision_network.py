"""Write private Wi-Fi/quota settings over USB without printing credentials."""
import argparse
import base64
import json
from pathlib import Path
from serial_debug_test import DebugClient

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--port',required=True)
    args=parser.parse_args()
    data=json.loads(args.config.read_text(encoding='utf-8-sig'))
    payload={key:data[key] for key in ('ssid','password','url','token')}
    encoded=base64.b64encode(json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()).decode()
    if len(encoded)>1300: raise SystemExit('Configuration too large')
    client=DebugClient(args.port)
    try:
        client.handshake()
        result=client.command('debug network-config '+encoded,'network-config',timeout=8)
        if result.status!='PASS': raise SystemExit('Network configuration rejected')
    finally: client.close()

if __name__=='__main__': main()
