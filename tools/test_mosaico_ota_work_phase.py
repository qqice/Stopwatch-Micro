"""OTA presentation wiring checks; runtime state/progress tested by maintenance harness."""
from pathlib import Path
import unittest
R=Path(__file__).resolve().parents[1]
OTA=(R/'main/ota/mosaico_ota.cpp').read_text()
NET=(R/'main/host/network_quota.cpp').read_text()
DELTA=(R/'main/ota/mosaico_delta.cpp').read_text()
def body(source,start,end):return source.split(start,1)[1].split(end,1)[0]
class WorkPhaseTests(unittest.TestCase):
 def test_preparation_and_negotiation_order(self):
  request=body(OTA,'bool requestLocked(', 'bool validateDescriptor(')
  self.assertLess(request.index('active.store(true)'),request.index('recordWorkPhase(WorkPhase::Preparing)'))
  network=body(NET,'void NetworkQuota::updateFirmware()', 'void NetworkQuota::wakeForFirmwareUpdate()')
  self.assertLess(network.index('beginManifest(body)'),network.index('WorkPhase::Negotiating'))
  self.assertLess(network.index('runningImageDescriptor(baseImage)'),network.index('WorkPhase::Negotiating'))
  self.assertLess(network.index('WorkPhase::Negotiating'),network.index('requestJson(path,body,BodyCapacity,used)'))
  self.assertIn('if(!deltaCompleted) MosaicoOta::recordTransferProgress(false,0,size);',network)
 def test_verification_is_separate_from_merge(self):
  reconstruct=body(DELTA,'bool reconstruct(', '\n}\n}')
  self.assertLess(reconstruct.index('d.flush()'),reconstruct.index('WorkPhase::Verifying'))
  self.assertLess(reconstruct.index('WorkPhase::Verifying'),reconstruct.index('psa_hash_finish'))
  finish=body(OTA,'bool finishDownload()', 'bool installVerified()')
  self.assertLess(finish.index('WorkPhase::Verifying'),finish.index('psa_hash_finish'))
  self.assertLess(finish.index('WorkPhase::Verifying'),finish.index('esp_ota_end'))
 def test_new_check_and_candidate_clear_mode(self):
  check=body(OTA,'bool requestCheck()', 'bool takeCheckRequest()')
  self.assertIn('ui.modeKnown=false; ui.workPhase=WorkPhase::None;',check)
  self.assertLess(check.index('ui.modeKnown=false'),check.index('checkQueued.store(true)'))
  candidate=body(OTA,'bool discoverManifest(', 'bool requestAutomatic(')
  self.assertIn('ui.modeKnown=false; ui.workPhase=WorkPhase::None;',candidate)
if __name__=='__main__':unittest.main()
