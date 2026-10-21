#!/usr/bin/env python3
"""Trigger signed network OTA via runtime CDC and verify the opposite slot is VALID."""
import argparse,contextlib,io,time
from pathlib import Path
from serial.tools import list_ports
from serial_debug_test import DebugClient

def fields(result):return dict(p.split('=',1) for p in result.details.split() if '=' in p)

def transport_matches(port, uart=False, serial_number=None):
 # Explicit UART adapters only; CP210x can be the V1 power meter, not a console.
 identifiers={(0x1366,0x0105),(0x0403,0x6011)} if uart else {(0x303a,0x4001)}
 return (port.vid,port.pid) in identifiers and (serial_number is None or port.serial_number==serial_number)

def open_client(port, uart=False):
 return DebugClient(port,uart=uart,wake_preamble=uart)

def prepare_display(client, keep_locked=False):
 if not keep_locked:
  return client.command('debug display-wake','display-wake').status=='PASS'
 # A locked transfer avoids starting an awake BLE session before OTA admission.
 # Do not fake wake success or alter settings: the caller must prepare the state.
 state=client.command('debug power','power',10)
 return state.status=='PASS' and fields(state).get('locked')=='1'
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',required=True);p.add_argument('--confirm-external-power',action='store_true');p.add_argument('--timeout',type=int,default=900);p.add_argument('--initial-observation-delay',type=int,default=0);p.add_argument('--bypass',action='store_true',help='Use the 0.7+ USB screen-confirmation bypass; safety checks remain enforced')
 p.add_argument('--log',type=Path,help='Optional local serial evidence file; keep private because runtime logs may contain sensitive data')
 p.add_argument('--keep-locked',action='store_true',help='Require an already locked display; do not wake it before requesting OTA')
 p.add_argument('--uart',action='store_true',help='Use CRC-framed JLink/FT4232 UART instead of board CDC; requires explicit --port')
 p.add_argument('--boot-status-probe',action='store_true',help='One read-only status snapshot during boot health verification; never changes health gates')
 args=p.parse_args()
 if not args.confirm_external_power:raise SystemExit('Confirm USB/external power with --confirm-external-power; not inferred from SOC')
 if not 60<=args.timeout<=1800:raise SystemExit('timeout must be60..1800 seconds')
 if not 0<=args.initial_observation_delay<=600 or args.initial_observation_delay>=args.timeout:raise SystemExit('Invalid initial observation delay')
 entries=[x for x in list_ports.comports() if x.device.upper()==args.port.upper() and transport_matches(x,args.uart)]
 if len(entries)!=1:raise SystemExit('Explicit port does not match the selected CDC/UART transport')
 serial_number=entries[0].serial_number
 client=None;captured=io.StringIO();boot_status_probed=False
 try:
  client=open_client(args.port,args.uart)
  with contextlib.redirect_stdout(captured):
   client.handshake(30)
   stat=client.command('debug ota-status','ota-status')
   old=fields(stat)
   if stat.status!='PASS' or old.get('state')!='2' or old.get('running') not in ('ota_0','ota_1') or old.get('busy')!='0':raise SystemExit('OTA runtime is not valid/idle')
   target='ota_1' if old['running']=='ota_0' else 'ota_0'
   if not prepare_display(client,args.keep_locked):raise SystemExit('Display admission refused')
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
    matches=[x for x in list_ports.comports() if transport_matches(x,args.uart,serial_number)]
    if len(matches)==1:
     try:
      client=open_client(matches[0].device,args.uart)
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
   if args.boot_status_probe and not boot_status_probed and d.get('stage')=='boot_checking':
    boot_status_probed=True  # One observation only, never a retry or control command.
    with contextlib.redirect_stdout(captured):
     try:client.command('debug status','status',10)
     except (OSError,TimeoutError) as exc:print('BOOT_STATUS_PROBE_UNAVAILABLE '+type(exc).__name__,flush=True)
   if d.get('busy')=='0':
    if d.get('running')==target:
     if d.get('state')=='2':print('NETWORK_OTA_ACCEPTED running='+target+' state=VALID',flush=True);return
     if d.get('state')=='1':time.sleep(5);continue
    # Staged firmware briefly exposes its selected image before the USB
    # full-pipeline mode restarts. Idle is not necessarily an abort in that
    # bounded ReadyReboot window; only the actual opposite-slot VALID boot wins.
    if d.get('stage')=='ready_reboot' and d.get('selected')=='1':
     time.sleep(2);continue
    raise SystemExit('OTA rejected/aborted or rolled back: running='+d.get('running','unknown')+' reason='+d.get('reason','unknown'))
   print('OTA_PROGRESS received='+d.get('received','0')+' size='+d.get('size','0')+' running='+d.get('running','unknown')+' state='+d.get('state','?')+' stage='+d.get('stage','unknown'),flush=True)
   time.sleep(10)
  raise SystemExit('OTA observation timed out; completion NOT accepted')
 finally:
  if client:client.close()
  if args.log:
   try:
    args.log.parent.mkdir(parents=True,exist_ok=True)
    args.log.write_text(captured.getvalue(),encoding='utf8')
   except OSError as exc:
    print('OTA_LOG_SAVE_FAILED '+type(exc).__name__,flush=True)
if __name__=='__main__':main()
