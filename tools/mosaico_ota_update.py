#!/usr/bin/env python3
"""Trigger signed network OTA via runtime CDC and verify the opposite slot is VALID."""
import argparse,contextlib,io,time
from serial.tools import list_ports
from serial_debug_test import DebugClient

def fields(result):return dict(p.split('=',1) for p in result.details.split() if '=' in p)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',required=True);p.add_argument('--confirm-external-power',action='store_true');p.add_argument('--timeout',type=int,default=900);p.add_argument('--initial-observation-delay',type=int,default=0);p.add_argument('--bypass',action='store_true',help='Use the 0.7+ USB screen-confirmation bypass; safety checks remain enforced')
 args=p.parse_args()
 if not args.confirm_external_power:raise SystemExit('Confirm USB/external power with --confirm-external-power; not inferred from SOC')
 if not 60<=args.timeout<=1800:raise SystemExit('timeout must be60..1800 seconds')
 if not 0<=args.initial_observation_delay<=600 or args.initial_observation_delay>=args.timeout:raise SystemExit('Invalid initial observation delay')
 entries=[x for x in list_ports.comports() if x.device.upper()==args.port.upper() and (x.vid,x.pid)==(0x303a,0x4001)]
 if len(entries)!=1:raise SystemExit('Explicit port is not the Mosaico runtime CDC')
 serial_number=entries[0].serial_number
 client=None;captured=io.StringIO()
 try:
  client=DebugClient(args.port)
  with contextlib.redirect_stdout(captured):
   client.handshake(30)
   stat=client.command('debug ota-status','ota-status')
   old=fields(stat)
   if stat.status!='PASS' or old.get('state')!='2' or old.get('running') not in ('ota_0','ota_1') or old.get('busy')!='0':raise SystemExit('OTA runtime is not valid/idle')
   target='ota_1' if old['running']=='ota_0' else 'ota_0'
   if client.command('debug display-wake','display-wake').status!='PASS':raise SystemExit('Wake refused')
   command='ota-bypass' if args.bypass else 'ota-update'
   ready=client.command('debug '+command+' CONFIRM_EXTERNAL_POWER',command,10)
   if ready.status!='PASS':raise SystemExit('OTA request refused; inspect ota-status')
  print('SIGNED_NETWORK_OTA_REQUESTED target='+target+' no_ROM_touch=1',flush=True)
  deadline=time.monotonic()+args.timeout
  observe_until=time.monotonic()+args.initial_observation_delay
  # Drain unsolicited logs without querying OTA locks in older firmware.
  while client is not None and time.monotonic()<observe_until:
   try:
    raw=client.serial.readline()
    if raw:captured.write(raw.decode('utf-8',errors='replace'))
   except OSError:
    client.close();client=None;break
  while time.monotonic()<deadline:
   if client is None:
    matches=[x for x in list_ports.comports() if (x.vid,x.pid)==(0x303a,0x4001) and (not serial_number or x.serial_number==serial_number)]
    if len(matches)==1:
     try:
      client=DebugClient(matches[0].device)
      with contextlib.redirect_stdout(captured):client.handshake(10)
     except (OSError,TimeoutError):
      if client:client.close()
      client=None
    if client is None:time.sleep(5);continue
   try:
    with contextlib.redirect_stdout(captured):stat=client.command('debug ota-status','ota-status',5)
   except (OSError,TimeoutError):
    client.close();client=None;time.sleep(5);continue
   d=fields(stat)
   if d.get('busy')=='0':
    if d.get('running')==target:
     if d.get('state')=='2':print('NETWORK_OTA_ACCEPTED running='+target+' state=VALID',flush=True);return
     if d.get('state')=='1':time.sleep(5);continue
    raise SystemExit('OTA rejected/aborted or rolled back: running='+d.get('running','unknown')+' reason='+d.get('reason','unknown'))
   print('OTA_PROGRESS received='+d.get('received','0')+' size='+d.get('size','0'),flush=True)
   time.sleep(10)
  raise SystemExit('OTA observation timed out; completion NOT accepted')
 finally:
  if client:client.close()
if __name__=='__main__':main()
