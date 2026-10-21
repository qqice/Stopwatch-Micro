"""Run actual OTA host observer with mocked device; no ports, network or firmware."""
import importlib.util,io,contextlib,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ota_update_probe_under_test',ROOT/'tools/mosaico_ota_update.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Client:
 def __init__(self,probe_error=False):self.commands=[];self.n=0;self.closed=False;self.probe_error=probe_error
 def handshake(self,*a):pass
 def close(self):self.closed=True
 def command(self,text,expected,*args):
  self.commands.append(text)
  if text=='debug ota-status':
   self.n+=1
   d=['running=ota_1 state=2 busy=0','running=unknown state=-1 busy=1 stage=boot_checking','running=unknown state=-1 busy=1 stage=boot_checking','running=ota_0 state=2 busy=0'][self.n-1]
  elif text=='debug status':
   if self.probe_error:raise TimeoutError('synthetic observer-only timeout')
   d='hal_i2c=on hal_display=on'
  else:d=''
  return SimpleNamespace(status='PASS',details=d)
class ProbeTests(unittest.TestCase):
 def run_case(self,enabled=False,probe_error=False):
  c=Client(probe_error);port=SimpleNamespace(device='COM8',vid=0x1366,pid=0x0105,serial_number='000024060502')
  with tempfile.TemporaryDirectory(prefix='.ota-host-probe-',dir=ROOT) as td:
   log=Path(td)/'capture.log';args=['mosaico_ota_update.py','--port','COM8','--uart','--confirm-external-power','--bypass','--timeout','60','--log',str(log)]+(['--boot-status-probe'] if enabled else [])
   with patch.object(sys,'argv',args),patch.object(m.list_ports,'comports',return_value=[port]),patch.object(m,'open_client',return_value=c),patch.object(m.time,'sleep'),contextlib.redirect_stdout(io.StringIO()) as output:m.main()
   self.assertIn('NETWORK_OTA_ACCEPTED',output.getvalue());saved=log.read_text();self.assertTrue(c.closed)
   self.assertEqual(c.commands.count('debug ota-bypass CONFIRM_EXTERNAL_POWER'),1)
   self.assertFalse(any('gauge' in s or 'reset' in s or 'rollback' in s for s in c.commands))
   return c.commands,saved
 def test_default_no_probe(self):cmd,_=self.run_case();self.assertNotIn('debug status',cmd)
 def test_probe_once_despite_two_health_samples(self):cmd,_=self.run_case(True);self.assertEqual(cmd.count('debug status'),1)
 def test_unavailable_probe_does_not_override_actual_valid(self):cmd,log=self.run_case(True,True);self.assertEqual(cmd.count('debug status'),1);self.assertIn('BOOT_STATUS_PROBE_UNAVAILABLE TimeoutError',log)
if __name__=='__main__':unittest.main()
