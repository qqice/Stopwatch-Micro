#!/usr/bin/env python3
"""Trigger signed network OTA via runtime CDC and verify the opposite slot is VALID."""
import argparse,contextlib,io,time,json,os,re,sys
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
def maintenance_update(args, serial_number):
 """One explicit grant/request; thereafter read-only observation, never renew."""
 client=None;request_ack_unknown=False;requested=False;accepted=False
 started=time.monotonic()
 # Reserve evidence BEFORE opening a port. Existing evidence is never replaced.
 args.log.parent.mkdir(parents=True,exist_ok=True)
 log=args.log.open('xb')
 def record(event, **data):
  payload=dict(event=event,elapsed_s=round(time.monotonic()-started,3),**data)
  log.write((json.dumps(payload,sort_keys=True)+'\n').encode('utf8'))
  log.flush();os.fsync(log.fileno())
 def call(command, expected, timeout=10):
  output=io.StringIO()
  record('command',command=command,timeout_s=timeout)
  try:
   with contextlib.redirect_stdout(output):reply=client.command(command,expected,timeout)
   record('response',command=command,status=reply.status if reply is not None else None,
          details=reply.details if reply is not None else None,raw=output.getvalue())
   return reply,output.getvalue()
  except BaseException as exc:
   record('command_error',command=command,error=repr(exc),raw=output.getvalue())
   raise
 def handshake(timeout):
  output=io.StringIO()
  try:
   with contextlib.redirect_stdout(output):client.handshake(timeout)
  finally:record('handshake',timeout_s=timeout,raw=output.getvalue())
 def diagnostic(command, expected):
  try:return call(command,expected,10)[0]
  except (OSError,TimeoutError) as exc:
   record('diagnostic_unavailable',command=command,error=repr(exc));return None
 def close_current():
  nonlocal client
  old_client=client;client=None
  if old_client is not None:old_client.close()
 try:
  record('maintenance_preflight',port=args.port,uart=args.uart,serial_number=serial_number,
         sha=args.maintenance_sha,ttl_s=args.maintenance_ttl,expected_version=args.expected_version,
         external_power_human_attestation=True,usb_sole_5v_human_attestation=True,
         not_a_usb_flag_or_current_measurement=True)
  client=open_client(args.port,args.uart)
  handshake(30)
  stat,_=call('debug ota-status','ota-status')
  old=fields(stat)
  if stat.status!='PASS' or old.get('state')!='2' or old.get('running') not in ('ota_0','ota_1') or old.get('busy')!='0':
   raise SystemExit('OTA runtime is not valid/idle; maintenance request NOT sent')
  target='ota_1' if old['running']=='ota_0' else 'ota_0'
  # Preserve the established screen admission behavior; no settings mutation.
  output=io.StringIO()
  record('display_admission',keep_locked=args.keep_locked,command='debug power' if args.keep_locked else 'debug display-wake')
  try:
   with contextlib.redirect_stdout(output):display_ready=prepare_display(client,args.keep_locked)
  finally:record('display_admission_result',raw=output.getvalue())
  if not display_ready:raise SystemExit('Display admission refused; maintenance request NOT sent')
  command='debug ota-maintenance update CONFIRM_USB_POWER '+args.maintenance_sha+' '+str(args.maintenance_ttl)
  requested=True  # A write/ACK failure is uncertain, not permission to resend.
  try:
   ready,_=call(command,'ota-maintenance',10)
  except (OSError,TimeoutError) as exc:
   request_ack_unknown=True
   record('request_ack_unknown',error=repr(exc),never_resend=True)
   # Distinct idempotent command drains any late same-command ACK first.
   diagnostic('debug ping','ping')
   diagnostic('debug ota-maintenance status','ota-maintenance')
  else:
   if ready.status!='PASS':
    diagnostic('debug ota-maintenance status','ota-maintenance')
    diagnostic('debug ota-maintenance power','ota-maintenance')
    raise SystemExit('Maintenance OTA request refused; no retry or renewal')
   ack=fields(ready)
   # Queued PASS is authoritative even if cached diagnostics report lock busy.
   # Any immutable data actually echoed must match the one requested image.
   if ack.get('sha') not in (None,args.maintenance_sha):
    raise SystemExit('Maintenance ACK immutable SHA mismatch; no retry')
   if ack.get('mode') not in (None,'maintenance') or ack.get('maintenance') not in (None,'1','busy'):
    raise SystemExit('Maintenance ACK mode mismatch; no retry')
   if ack.get('grant') not in (None,'1') or ack.get('immutable') not in (None,'1'):
    raise SystemExit('Maintenance ACK grant mismatch; no retry')
   record('request_acknowledged',target=target)
  print('MAINTENANCE_NETWORK_OTA_OBSERVING target='+target+' request_ack_unknown='+str(int(request_ack_unknown)),flush=True)
  deadline=time.monotonic()+args.timeout
  observe_until=time.monotonic()+args.initial_observation_delay
  saw_progress=False
  while client is not None and time.monotonic()<observe_until:
   try:
    raw=client.serial.readline()
    if raw:record('unsolicited',raw=raw.decode('utf8',errors='replace'))
   except OSError as exc:
    record('read_disconnected',error=repr(exc));close_current();break
  while time.monotonic()<deadline:
   if client is None:
    matches=[x for x in list_ports.comports() if transport_matches(x,args.uart,serial_number)]
    if len(matches)==1:
     try:
      client=open_client(matches[0].device,args.uart);handshake(10)
      # No request is sent after reconnect. A barrier prevents a stale ACK
      # being mistaken for status on a surviving console transport.
      diagnostic('debug ping','ping')
     except (OSError,TimeoutError) as exc:
      record('reconnect_unavailable',error=repr(exc));close_current()
    if client is None:time.sleep(5);continue
   try:stat,_=call('debug ota-status','ota-status',5)
   except (OSError,TimeoutError) as exc:
    record('observe_disconnected',error=repr(exc));close_current();time.sleep(5);continue
   d=fields(stat)
   if stat.status!='PASS':time.sleep(5);continue
   if d.get('running')==target and d.get('state')=='2' and d.get('busy')=='0':
    version_reply,raw=call('debug status','status',10)
    versions=re.findall(r'DBG STATUS firmware=(\S+)',raw)
    if version_reply.status!='PASS' or versions!=[args.expected_version]:
     raise SystemExit('Opposite slot VALID but firmware version mismatch; completion NOT accepted')
    grant=diagnostic('debug ota-maintenance status','ota-maintenance')
    grant_off_verified=grant is not None and grant.status=='PASS' and fields(grant).get('maintenance')=='0' and fields(grant).get('grant')=='0'
    record('postboot_maintenance_status',available=grant is not None,
           grant_off_verified=grant_off_verified)
    if not grant_off_verified:
     record('not_accepted',accepted=False,running=target,state='VALID',sdk_valid=True,
            firmware=args.expected_version,grant_off_verified=False)
     raise SystemExit('Opposite slot SDK VALID but maintenance/grant OFF unverified; not accepted; no retry or renewal')
    accepted=True
    record('accepted',running=target,state='VALID',firmware=args.expected_version,
           request_ack_unknown=request_ack_unknown)
    print('MAINTENANCE_NETWORK_OTA_ACCEPTED running='+target+' state=VALID firmware='+args.expected_version+
          ' request_ack_unknown='+str(int(request_ack_unknown)),flush=True)
    return
   if d.get('running')==target and d.get('state')=='1':
    time.sleep(5);continue
   if d.get('stage')=='ready_reboot' and d.get('selected')=='1':
    saw_progress=True
    grant=diagnostic('debug ota-maintenance status','ota-maintenance')
    g=fields(grant) if grant is not None and grant.status=='PASS' else {}
    if g.get('grant')=='0' or g.get('ttl_ms')=='0':
     record('needs_operator_grant',sha=args.maintenance_sha,automatic_renewal=False)
     raise SystemExit('ReadyReboot grant expired; needs operator grant for the same immutable hash; no automatic renewal/reboot')
    time.sleep(2);continue
   if d.get('busy')=='0':
    if not saw_progress and d.get('running')==old['running'] and d.get('reason','idle')=='idle':
     record('waiting_for_queued_owner',request_ack_unknown=request_ack_unknown)
     time.sleep(5);continue
    diagnostic('debug ota-maintenance status','ota-maintenance')
    diagnostic('debug ota-maintenance power','ota-maintenance')
    raise SystemExit('Maintenance OTA rejected/aborted or rolled back: running='+d.get('running','unknown')+' reason='+d.get('reason','unknown'))
   saw_progress=True
   record('progress',**d)
   time.sleep(10)
  record('observation_timeout',automatic_renewal=False)
  raise SystemExit('Maintenance observation timed out; completion NOT accepted; inspect same-hash grant manually')
 except BaseException as exc:
  record('failure',error=repr(exc),request_attempted=requested,request_ack_unknown=request_ack_unknown)
  raise
 finally:
  try:
   try:close_current()
   except BaseException as exc:
    accepted=False
    record('close_error',error=repr(exc));raise
  finally:
   # Persist the final record even if client.close() itself raises.
   try:record('final',accepted=accepted,request_attempted=requested,request_ack_unknown=request_ack_unknown)
   finally:
    try:log.flush();os.fsync(log.fileno())
    finally:log.close()


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',required=True);p.add_argument('--confirm-external-power',action='store_true');p.add_argument('--timeout',type=int,default=900);p.add_argument('--initial-observation-delay',type=int,default=0);mode=p.add_mutually_exclusive_group();mode.add_argument('--bypass',action='store_true',help='Use the 0.7+ USB screen-confirmation bypass; safety checks remain enforced');mode.add_argument('--maintenance-sha',help='Explicit maintenance OTA immutable image SHA256; requires stable sole-USB power attestation')
 p.add_argument('--log',type=Path,help='Optional local serial evidence file; keep private because runtime logs may contain sensitive data')
 p.add_argument('--keep-locked',action='store_true',help='Require an already locked display; do not wake it before requesting OTA')
 p.add_argument('--uart',action='store_true',help='Use CRC-framed JLink/FT4232 UART instead of board CDC; requires explicit --port')
 p.add_argument('--boot-status-probe',action='store_true',help='One read-only status snapshot during boot health verification; never changes health gates')
 p.add_argument('--maintenance-ttl',type=int,default=None,help='Legacy maintenance argument (30..600); firmware keeps the confirmed USB transaction live until disconnect/completion')
 p.add_argument('--confirm-usb-power',action='store_true',help='Human attestation: stable USB is sole 5V supply, not inferred from telemetry')
 p.add_argument('--expected-version',help='Exact target firmware version; mandatory for maintenance acceptance')
 args=p.parse_args()
 if not args.confirm_external_power:raise SystemExit('Confirm USB/external power with --confirm-external-power; not inferred from SOC')
 if not 60<=args.timeout<=1800:raise SystemExit('timeout must be60..1800 seconds')
 if not 0<=args.initial_observation_delay<=600 or args.initial_observation_delay>=args.timeout:raise SystemExit('Invalid initial observation delay')
 if args.maintenance_sha is not None:
  if not re.fullmatch('[0-9a-fA-F]{64}',args.maintenance_sha):p.error('maintenance SHA must be exactly 64 hexadecimal digits')
  args.maintenance_sha=args.maintenance_sha.lower()
  if args.maintenance_ttl is None:args.maintenance_ttl=600
  if not 30<=args.maintenance_ttl<=600:p.error('maintenance TTL must be30..600 seconds')
  if args.boot_status_probe or args.initial_observation_delay!=0:p.error('maintenance does not support --boot-status-probe or nonzero --initial-observation-delay')
  if not args.confirm_usb_power:p.error('maintenance requires --confirm-usb-power human attestation')
  if not args.expected_version or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,30}',args.expected_version):p.error('maintenance requires an exact --expected-version')
  if args.log is None:p.error('maintenance requires a new --log evidence file')
  if args.log.exists():p.error('maintenance log already exists; evidence must not be overwritten')
 elif args.confirm_usb_power or args.expected_version is not None or args.maintenance_ttl is not None:
  p.error('maintenance power/version/TTL options require --maintenance-sha')
 entries=[x for x in list_ports.comports() if x.device.upper()==args.port.upper() and transport_matches(x,args.uart)]
 if len(entries)!=1:raise SystemExit('Explicit port does not match the selected CDC/UART transport')
 serial_number=entries[0].serial_number
 if args.maintenance_sha is not None:return maintenance_update(args,serial_number)
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
