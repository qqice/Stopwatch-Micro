from pathlib import Path
import unittest
ROOT=Path.cwd()
class BudgetDebug(unittest.TestCase):
 def test_console_bounds_and_no_secret(self):
  src=(ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf8');cmd=src.split('std::strcmp(command, "derp-tx-budget") == 0',1)[1].split('if (std::strcmp(command, "network")',1)[0]
  self.assertIn('value != 500 && value != 5000',cmd);self.assertIn('::strtok_r(nullptr',cmd);self.assertIn('MosaicoOta::healthPending()',cmd);self.assertIn('ram_only=1 next_frame_snapshot=1',cmd);self.assertNotIn('nvs_',cmd);self.assertNotIn('auth_key',cmd)
 def test_separate_numeric_lines(self):
  src=(ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
  for tag in ('DBG TAIL_TX ','DBG TAIL_CTRL '):
   fmt=src.split('debugPrintf("'+tag,1)[1].split('"',1)[0];self.assertNotIn('%s',fmt);self.assertIn('snapshots_independent=1',fmt);self.assertLess(len(fmt)+200,1536)
 def test_wrapper_is_atomic_pointer_and_no_persistence(self):
  src=(ROOT/'main/host/tailscale_transport.cpp').read_text(encoding='utf8');fn=src.split('bool TailnetQuota::setDerpTxRetryBudgetMs',1)[1].split('bool TailnetQuota::configure',1)[0];self.assertIn('_client.load()',fn);self.assertNotIn('nvs_',fn)
if __name__=='__main__':unittest.main()
