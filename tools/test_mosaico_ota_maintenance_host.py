"""Offline actual-main maintenance OTA tests: no serial opens or network."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('maintenance_host_under_test',ROOT/'tools/mosaico_ota_update.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SHA='abcdef0123456789'*4
VERSION='0.13.93-mosaico-ota'
REQUEST='debug ota-maintenance update CONFIRM_USB_POWER '+SHA+' 600'


class Clock:
 def __init__(self):self.now=0
 def monotonic(self):self.now+=0.01;return self.now
 def sleep(self,seconds):self.now+=seconds


class Client:
 def __init__(self,mode='success'):
  self.mode=mode;self.commands=[];self.observations=0;self.closed=False;self.handshakes=[]
 def handshake(self,timeout):self.handshakes.append(timeout)
 def close(self):
  self.closed=True
  if self.mode=='close_fail':raise OSError('synthetic close failure')
 def command(self,text,expected,*args):
  self.commands.append(text);status='PASS'
  if text=='debug ota-status':
   self.observations+=1
   if self.observations==1:
    d='running=ota_0 state=2 busy=0 reason=idle'
    if self.mode=='invalid_preflight':d='running=ota_0 state=1 busy=0'
   elif self.mode=='expired':d='running=ota_0 state=2 busy=0 stage=ready_reboot selected=1'
   elif self.mode=='idle_unknown' and self.observations==2:d='running=ota_0 state=2 busy=0 reason=idle'
   elif self.observations<4:d='running=ota_0 state=2 busy=1 stage=downloading received=1 size=10'
   else:d='running=ota_1 state=2 busy=0 stage=complete'
  elif text==REQUEST:
   if self.mode in ('lost_ack','idle_unknown'):raise TimeoutError('synthetic lost ACK')
   if self.mode=='oserror_ack':raise OSError('synthetic write outcome unknown')
   if self.mode=='reject':status='FAIL';d='maintenance=0 grant=0 reason=maintenance_unsafe_power'
   elif self.mode=='busy_ack':d='maintenance=busy'
   elif self.mode=='wrong_hash':d='maintenance=1 grant=1 sha='+'0'*64
   else:d='maintenance=1 grant=1 sha='+SHA
  elif text=='debug ota-maintenance status':
   if self.mode=='expired':d='maintenance=1 grant=0 selected=1 ttl_ms=0 sha='+SHA
   elif self.observations>=4:
    if self.mode=='postboot_none':return None
    if self.mode=='postboot_timeout':raise TimeoutError('synthetic postboot status timeout')
    if self.mode=='postboot_oserror':raise OSError('synthetic postboot status unavailable')
    d={'postboot_fail':'maintenance=0 grant=0',
       'postboot_missing_maintenance':'grant=0',
       'postboot_missing_grant':'maintenance=0',
       'postboot_missing_both':'',
       'postboot_maintenance_on':'maintenance=1 grant=0',
       'postboot_grant_on':'maintenance=0 grant=1',
       'postboot_both_on':'maintenance=1 grant=1'}.get(self.mode,'maintenance=0 grant=0 selected=0 ttl_ms=0 sha=-')
    if self.mode=='postboot_fail':status='FAIL'
   else:d='maintenance=1 grant=1 selected=0 ttl_ms=590000 sha='+SHA
  elif text=='debug ota-maintenance power':d='reason=maintenance_unsafe_power cached_only=1 no_i2c=1 no_power_proof=1'
  elif text=='debug status':
   print('DBG STATUS firmware='+('0.13.91-mosaico-ota' if self.mode=='wrong_fw' else VERSION)+' battery=100')
   d=''
  else:d=''
  return SimpleNamespace(status=status,details=d)


class MaintenanceTests(unittest.TestCase):
 def argv(self,log):
  return ['mosaico_ota_update.py','--port','COM8','--uart','--confirm-external-power',
          '--confirm-usb-power','--maintenance-sha',SHA.upper(),'--expected-version',VERSION,
          '--timeout','60','--log',str(log)]
 def run_case(self,mode='success',uart=True):
  c=Client(mode);clock=Clock()
  port=SimpleNamespace(device='COM8' if uart else 'COM12',vid=0x1366 if uart else 0x303a,
                       pid=0x0105 if uart else 0x4001,serial_number='exact-test-identity')
  with tempfile.TemporaryDirectory(prefix='.ota-maintenance-test-',dir=ROOT) as td:
   log=Path(td)/'evidence.jsonl';argv=self.argv(log)
   if not uart:argv.remove('--uart');argv[argv.index('--port')+1]='COM12'
   failure=None
   with patch.object(sys,'argv',argv),patch.object(m.list_ports,'comports',return_value=[port]), \
        patch.object(m,'open_client',return_value=c) as opener,patch.object(m.time,'monotonic',clock.monotonic), \
        patch.object(m.time,'sleep',clock.sleep),patch.object(m.os,'fsync',wraps=m.os.fsync) as sync, \
        contextlib.redirect_stdout(io.StringIO()) as output:
    try:m.main()
    except (SystemExit,OSError) as exc:failure=exc
   self.assertTrue(c.closed);self.assertGreater(sync.call_count,2)
   events=[json.loads(row) for row in log.read_text().splitlines()]
   self.assertEqual(events[-1]['event'],'final')
   self.assertEqual(c.commands.count(REQUEST),0 if mode=='invalid_preflight' else 1)
   self.assertEqual(c.handshakes,[30])
   opener.assert_called_once_with(port.device,uart)
   self.assertFalse(any('reboot' in s or 'gauge' in s or 'reset' in s or 'rollback' in s for s in c.commands))
   allowed={'debug ota-status','debug display-wake','debug status','debug ping',
            'debug ota-maintenance status','debug ota-maintenance power',REQUEST}
   self.assertTrue(set(c.commands)<=allowed)
   if mode in ('reject','wrong_fw','expired','close_fail','wrong_hash','invalid_preflight') or mode.startswith('postboot_'):
    self.assertIsNotNone(failure);self.assertFalse(events[-1]['accepted'])
    if mode.startswith('postboot_'):
     self.assertIn('not accepted',str(failure))
   else:
    self.assertIsNone(failure);self.assertTrue(events[-1]['accepted'])
    self.assertIn('MAINTENANCE_NETWORK_OTA_ACCEPTED',output.getvalue())
   return c.commands,events,output.getvalue()
 def test_uart_exact_request_once(self):
  commands,_,_=self.run_case()
  self.assertEqual(commands,['debug ota-status','debug display-wake',REQUEST,
                             'debug ota-status','debug ota-status','debug ota-status',
                             'debug status','debug ota-maintenance status'])
 def test_cdc_identity_and_mode(self):self.run_case(uart=False)
 def test_lost_ack_barrier_readonly_eventual_valid(self):
  commands,events,_=self.run_case('lost_ack')
  i=commands.index(REQUEST)
  self.assertEqual(commands[i+1:i+3],['debug ping','debug ota-maintenance status'])
  self.assertTrue(events[-1]['request_ack_unknown'])
 def test_oserror_ack_never_resent(self):
  _,events,_=self.run_case('oserror_ack');self.assertTrue(events[-1]['request_ack_unknown'])
 def test_lost_ack_queued_owner_initial_idle(self):self.run_case('idle_unknown')
 def test_busy_diagnostic_pass_is_not_denied(self):self.run_case('busy_ack')
 def test_wrong_firmware_not_accepted(self):self.run_case('wrong_fw')
 def test_wrong_hash_ack_not_accepted(self):self.run_case('wrong_hash')
 def test_reject_cached_status_and_power(self):
  commands,events,_=self.run_case('reject')
  self.assertEqual(commands[-2:],['debug ota-maintenance status','debug ota-maintenance power'])
  self.assertIn('cached_only=1',next(e['details'] for e in events if e['event']=='response' and e['command']=='debug ota-maintenance power'))
 def test_expired_no_renewal(self):
  _,events,_=self.run_case('expired');self.assertTrue(any(e['event']=='needs_operator_grant' for e in events))
 def test_close_failure_final_log_still_fsynced(self):self.run_case('close_fail')
 def test_preflight_idle_valid_before_request(self):self.run_case('invalid_preflight')
 def assert_postboot_rejected(self,modes):
  for mode in modes:
   with self.subTest(mode=mode):
    commands,events,output=self.run_case(mode)
    rejected=next(e for e in events if e['event']=='not_accepted')
    self.assertEqual((rejected['accepted'],rejected['running'],rejected['state'],rejected['sdk_valid'],rejected['firmware']),
                     (False,'ota_1','VALID',True,VERSION))
    self.assertFalse(next(e for e in events if e['event']=='postboot_maintenance_status')['grant_off_verified'])
    self.assertFalse(any(e['event']=='accepted' for e in events))
    self.assertNotIn('MAINTENANCE_NETWORK_OTA_ACCEPTED',output)
    self.assertEqual(commands.count(REQUEST),1)
    self.assertEqual(commands[-2:],['debug status','debug ota-maintenance status'])
    self.assertFalse(any('reboot' in s or 'grant ' in s or ' clear' in s for s in commands))
 def test_postboot_unavailable_not_accepted(self):
  self.assert_postboot_rejected(('postboot_none','postboot_timeout','postboot_oserror'))
 def test_postboot_failed_or_incomplete_off_not_accepted(self):
  self.assert_postboot_rejected(('postboot_fail','postboot_missing_maintenance','postboot_missing_grant','postboot_missing_both'))
 def test_postboot_maintenance_or_grant_on_not_accepted(self):
  self.assert_postboot_rejected(('postboot_maintenance_on','postboot_grant_on','postboot_both_on'))
 def test_flags_validated_before_inventory_or_open(self):
  with tempfile.TemporaryDirectory(prefix='.ota-maintenance-args-',dir=ROOT) as td:
   log=Path(td)/'new.jsonl'
   invalid=[['--maintenance-sha','x'*64],['--maintenance-ttl','29'],['--maintenance-ttl','601'],
            ['--maintenance-ttl','abc'],['--bypass'],['--expected-version','bad version'],
            ['--expected-version','x'*32],['--boot-status-probe'],['--initial-observation-delay','1']]
   for replacement in invalid:
    args=self.argv(log)
    key=replacement[0]
    if key in args:
     i=args.index(key);args[i:i+2]=replacement
    else:args+=replacement
    with self.subTest(replacement=replacement),patch.object(sys,'argv',args), \
         patch.object(m,'open_client') as opener,patch.object(m.list_ports,'comports') as inventory, \
         contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
     m.main()
    opener.assert_not_called();inventory.assert_not_called()
   for missing in ('--confirm-external-power','--confirm-usb-power','--expected-version','--log'):
    args=self.argv(log);i=args.index(missing);del args[i:i+(2 if missing in ('--expected-version','--log') else 1)]
    with self.subTest(missing=missing),patch.object(sys,'argv',args),patch.object(m,'open_client') as opener, \
         contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):m.main()
    opener.assert_not_called()
 def test_maintenance_only_options_rejected_in_ordinary_mode_before_open(self):
  for extra in (['--prepare-link'],['--prepare-link','--bypass'],['--confirm-usb-power'],['--expected-version',VERSION],['--maintenance-ttl','600'],['--maintenance-ttl','30']):
   args=['mosaico_ota_update.py','--port','COM8','--uart','--confirm-external-power']+extra
   with self.subTest(extra=extra),patch.object(sys,'argv',args),patch.object(m,'open_client') as opener, \
        patch.object(m.list_ports,'comports') as inventory,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
    m.main()
   opener.assert_not_called();inventory.assert_not_called()
 def test_existing_evidence_never_overwritten(self):
  with tempfile.TemporaryDirectory(prefix='.ota-maintenance-existing-',dir=ROOT) as td:
   log=Path(td)/'existing.log';log.write_bytes(b'immutable')
   with patch.object(sys,'argv',self.argv(log)),patch.object(m,'open_client') as opener, \
        contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):m.main()
   opener.assert_not_called();self.assertEqual(log.read_bytes(),b'immutable')
 def test_no_automatic_power_meter_route(self):
  self.assertFalse(m.transport_matches(SimpleNamespace(vid=0x10c4,pid=0xea60,serial_number='meter'),True))
  self.assertTrue(m.transport_matches(SimpleNamespace(vid=0x1366,pid=0x0105,serial_number='pinned'),True,'pinned'))
  self.assertFalse(m.transport_matches(SimpleNamespace(vid=0x1366,pid=0x0105,serial_number='other'),True,'pinned'))


class PrepareClient(Client):
 def __init__(self,mode):
  super().__init__();self.prepare_mode=mode;self.samples=0
 def command(self,text,expected,*args):
  if text!='debug network':return super().command(text,expected,*args)
  self.commands.append(text);self.samples+=1
  if self.prepare_mode=='disconnect':raise OSError('synthetic disconnected')
  if self.prepare_mode=='missing':return SimpleNamespace(status='PASS',details='connected=1')
  fresh=self.samples>1 and self.prepare_mode!='stale'
  accepted=4+(fresh and self.prepare_mode=='accepted')
  failures=2+(fresh and self.prepare_mode!='accepted')
  if self.prepare_mode!='accepted':
   print('DBG TAIL_FETCH path=0 stage=12 http=200 result=1')
  print('private-token-must-not-be-retained')
  return SimpleNamespace(status='PASS',details=f'connected=1 accepted={accepted} failures={failures} available=0 remaining=0')


class PrepareLinkTests(unittest.TestCase):
 argv=MaintenanceTests.argv
 # Exercise opt-in actual main; the default/unknown-ACK tests above remain intact.
 def prepare_case(self,mode,uart=True):
  prep=PrepareClient(mode);ota=Client();clock=Clock();opened=[]
  port=SimpleNamespace(device='COM8',vid=0x1366 if uart else 0x303a,
                       pid=0x0105 if uart else 0x4001,serial_number='board')
  def opener(device,use_uart):
   opened.append((device,use_uart,clock.now))
   if len(opened)==1:return prep
   self.assertTrue(prep.closed)
   return ota
  with tempfile.TemporaryDirectory(prefix='.ota-prepare-test-',dir=ROOT) as td:
   log=Path(td)/'evidence.jsonl';argv=self.argv(log)+['--prepare-link']
   if not uart:argv.remove('--uart')
   with patch.object(sys,'argv',argv),patch.object(m.list_ports,'comports',return_value=[port]), \
        patch.object(m,'open_client',side_effect=opener),patch.object(m.time,'monotonic',clock.monotonic), \
        patch.object(m.time,'sleep',clock.sleep),contextlib.redirect_stdout(io.StringIO()):
    m.main()
   contents=log.read_text();events=[json.loads(row) for row in contents.splitlines()]
  self.assertEqual([(d,u) for d,u,_ in opened],[('COM8',uart),('COM8',uart)])
  self.assertTrue(prep.closed and ota.closed)
  self.assertEqual(ota.commands.count(REQUEST),1)
  self.assertEqual(ota.commands[:3],['debug ota-status','debug display-wake',REQUEST])
  self.assertEqual(set(prep.commands)-{'debug display-wake','debug network'},set())
  self.assertEqual(prep.handshakes,[30])
  self.assertNotIn('private-token',contents)
  self.assertTrue(events[-1]['accepted'])
  result=next(e for e in events if e['event']=='prepare_link_result')
  self.assertFalse(result['admission_gate'])
  self.assertLess(events.index(result),next(i for i,e in enumerate(events) if e['event']=='maintenance_preflight'))
  self.assertLess(opened[1][2]-result['prepare_elapsed_s'],0.2)
  return prep,result,events,opened
 def test_http200_without_quota_still_requests_same_hash(self):
  prep,result,_,_=self.prepare_case('http')
  self.assertTrue(result['fresh_http_confirmed']);self.assertTrue(result['temporary_display_wake'])
  self.assertEqual(prep.samples,2)
 def test_accepted_delta_alone_is_positive_evidence(self):
  _,result,_,_=self.prepare_case('accepted');self.assertTrue(result['fresh_http_confirmed'])
 def test_stale_cached_http_is_not_new_gate(self):
  _,result,_,opened=self.prepare_case('stale')
  self.assertFalse(result['fresh_http_confirmed']);self.assertGreaterEqual(opened[1][2],120)
  self.assertLess(opened[1][2],121)
 def test_disconnected_handle_closed_then_original_attempt(self):
  _,result,events,_=self.prepare_case('disconnect')
  self.assertFalse(result['fresh_http_confirmed'])
  self.assertTrue(any(e['event']=='prepare_link_unavailable' for e in events))
 def test_cdc_transport_reused(self):self.prepare_case('http',uart=False)
 def test_missing_diagnostics_do_not_block_original_request(self):
  _,result,_,_=self.prepare_case('missing');self.assertFalse(result['fresh_http_confirmed'])
 def test_keep_locked_conflict_rejected_before_any_device_action(self):
  with tempfile.TemporaryDirectory(prefix='.ota-prepare-args-',dir=ROOT) as td:
   argv=self.argv(Path(td)/'evidence.jsonl')+['--prepare-link','--keep-locked']
   with patch.object(sys,'argv',argv),patch.object(m,'open_client') as opener, \
        patch.object(m.list_ports,'comports') as inventory,contextlib.redirect_stderr(io.StringIO()), \
        self.assertRaises(SystemExit):m.main()
   opener.assert_not_called();inventory.assert_not_called()
 def test_log_creation_failure_or_race_does_not_prepare_or_open_device(self):
  original_open=Path.open
  port=SimpleNamespace(device='COM8',vid=0x1366,pid=0x0105,serial_number='board')
  for failure in ('permission','race'):
   with self.subTest(failure=failure),tempfile.TemporaryDirectory(prefix='.ota-prepare-log-',dir=ROOT) as td:
    log=Path(td)/'evidence.jsonl';argv=self.argv(log)+['--prepare-link']
    def fail_open(path,mode='r',*args,**kwargs):
     if path==log and mode=='xb':
      if failure=='permission':raise PermissionError('synthetic unwritable log')
      with original_open(path,'wb') as competing:competing.write(b'immutable competing evidence')
     return original_open(path,mode,*args,**kwargs)
    with patch.object(sys,'argv',argv),patch.object(m.list_ports,'comports',return_value=[port]), \
         patch.object(Path,'open',fail_open),patch.object(m,'open_client') as opener, \
         patch.object(m,'prepare_link') as prepare,self.assertRaises(OSError):m.main()
    opener.assert_not_called();prepare.assert_not_called()
    if failure=='race':self.assertEqual(log.read_bytes(),b'immutable competing evidence')
 def test_single_reserved_log_handle_closed_after_prepare_and_request(self):
  original_open=Path.open;handles=[]
  def track_open(path,mode='r',*args,**kwargs):
   handle=original_open(path,mode,*args,**kwargs)
   if mode=='xb':handles.append(handle)
   return handle
  with patch.object(Path,'open',track_open):self.prepare_case('http')
  self.assertEqual(len(handles),1);self.assertTrue(handles[0].closed)


if __name__=='__main__':unittest.main(verbosity=2)
