"""Compare on-device history details with the authenticated local service."""
import argparse,json,re,time,urllib.request
from pathlib import Path
from serial_debug_test import DebugClient

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True);p.add_argument('--config',type=Path,required=True);a=p.parse_args()
    config=json.loads(a.config.read_text(encoding='utf-8-sig'))
    request=urllib.request.Request(f"http://{config['server_host']}:{config.get('server_port',8765)}/v1/history",
        headers={'Authorization':'Bearer '+config['device_token']})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=10) as response: history=json.loads(response.read())
    assert history['available']
    index=next(i for i in range(min(len(history['days'])-1,22),-1,-1) if history['days'][i]['quality'] == "official")
    expected=history['days'][index]
    c=DebugClient(a.port)
    try:
        c.handshake()
        deadline=time.monotonic()+120
        while True:
            status=c.command('debug network','network')
            match=re.search(r'history_accepted=(\d+)',status.details)
            if match and int(match.group(1))>0:break
            if time.monotonic()>deadline:raise RuntimeError('No device history received')
            until=time.monotonic()+10
            while time.monotonic()<until:c.serial.readline()
        assert c.command('debug history-selftest','history-selftest').status=='PASS'
        c.command('debug display-wake','display-wake')
        assert c.command('debug history days','history').status=='PASS'
        detail=c.command(f'debug history select {index}','history')
        assert detail.status=='PASS' and expected['label'] in detail.details
        assert f": {expected['tokens']} tokens (API day / timezone unknown)" in detail.details,detail.details
        assert c.command('debug history hours','history').status=='PASS'
        first=c.command('debug history select 0','history')
        assert first.status=='PASS'
        if history['hours'][0]['tokens'] is None:assert 'tokens' not in first.details
        last=c.command('debug history select 23','history');assert last.status=='PASS'
        # Completed observation hours are stable between host reads and device
        # refresh; never compare the actively accumulating current hour.
        positive = next((i for i in range(22, -1, -1) if history['hours'][i]['tokens'] is not None
                         and history['hours'][i]['tokens'] > 0), None)
        if positive is not None:
            expected_hour = history['hours'][positive]
            detail = c.command(f'debug history select {positive}', 'history')
            assert detail.status == 'PASS' and expected_hour['label'] in detail.details
            assert f": {expected_hour['tokens']} tokens ({expected_hour['quality']})" in detail.details, detail.details
            print('HOST OBSERVED HOUR EXACT PASS', expected_hour['label'], expected_hour['tokens'])

        assert c.command('debug selftest','selftest',8).status=='PASS'
        print('HOST HISTORY PASS daily_exact=1 hourly_selection=1 missing_not_zero=1')
    finally:c.close()

if __name__=='__main__':main()
