"""Check the real device's minute-rate lock screen without sending host keys."""
import argparse
import re
import time
from serial_debug_test import DebugClient

def fields(result):
    return {key:int(value) for key,value in re.findall(r'(locked|refreshes|frames|brightness)=(\d+)',result.details)}

def drain(client,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        client.serial.readline()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',required=True);a=p.parse_args()
    client=DebugClient(a.port)
    try:
        client.handshake();client.command('debug display-wake','display-wake')
        print('HOST waiting for automatic 60-second idle lock',flush=True)
        drain(client,65)
        before=fields(client.command('debug display','display'))
        assert before['locked']==1 and before['brightness']==8,before
        print('HOST display locked; checking one-minute refresh',flush=True)
        drain(client,65)
        after=fields(client.command('debug display','display'))
        assert after['locked']==1,after
        assert after['refreshes']-before['refreshes']==1,(before,after)
        assert 1<=after['frames']-before['frames']<=3,(before,after)
        client.command('debug display-wake','display-wake')
        awake=fields(client.command('debug display','display'))
        assert awake['locked']==0 and awake['brightness']>8,awake
        print('HOST DISPLAY PASS minute_refresh=1 bounded_physical_frames=1 wake=1')
    finally:client.close()

if __name__=='__main__':main()
