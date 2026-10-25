"""Offline tests of actual OTA leases and radio-start block; no device/SDK build."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from test_microlink_peer_probe import function
ROOT=Path(__file__).resolve().parents[1]
OTA=(ROOT/'main/ota/mosaico_ota.cpp').read_text(encoding='utf-8')
NETWORK=(ROOT/'main/host/network_quota.cpp').read_text(encoding='utf-8')

class NetworkStartLeaseTests(unittest.TestCase):
 def test_actual_busy_leases_owner_exclusion_and_radio_guard(self):
  signatures=('bool busy(', 'bool tryStorageLease(', 'bool tryNetworkStartLease(', 'bool currentTaskOwnsStorageLease(', 'void releaseStorageLease(')
  methods='\n'.join(function(OTA,sig) for sig in signatures)
  for sig in signatures:methods=methods.replace(sig,'constexpr '+sig)
  block=function(NETWORK,'        if (!_wifi_running) {')
  old='struct RadioStartLease {bool held=MosaicoOta::tryNetworkStartLease();~RadioStartLease(){if(held)MosaicoOta::releaseStorageLease();}} radioLease;'
  self.assertIn(old,block)
  # Inject only the parent instance into the production local RAII guard.
  block=block.replace(old,'struct RadioStartLease {Fixture& owner;bool held;constexpr RadioStartLease(Fixture& f):owner(f),held(f.tryNetworkStartLease()){} constexpr ~RadioStartLease(){if(held)owner.releaseStorageLease();}} radioLease{*this};')
  block=block.replace('continue;','return false;')
  code=r'''
#include <atomic>
#include <cstdint>
#include <initializer_list>
using TaskHandle_t=const int*;
constexpr int ESP_OK=0;
template<class T>struct Atomic {T value;constexpr void store(T v,std::memory_order){value=v;}constexpr T load(std::memory_order=std::memory_order_seq_cst)const{return value;}};
struct Fixture {
 int network=1,gauge=2;TaskHandle_t current=&network;
 struct Mutex {TaskHandle_t* current;TaskHandle_t owner=nullptr;unsigned depth=0,attempts=0,unlocks=0;
  constexpr bool try_lock(){++attempts;if(depth&&owner!=*current)return false;owner=*current;++depth;return true;}
  constexpr void unlock(){++unlocks;if(--depth==0)owner=nullptr;}
 }storageLeaseMutex{&current};
 Atomic<TaskHandle_t> storageLeaseOwner{nullptr};uint32_t storageLeaseDepth=0;
 Atomic<bool> active{false},approvedRequest{false},checkQueued{false},checking{false},installQueued{false},rebootQueued{false},bootPending{false};
 bool _wifi_running=false,gaugePending=false;unsigned starts=0,waits=0;
 constexpr TaskHandle_t xTaskGetCurrentTaskHandle(){return current;}
 constexpr Fixture& GetHAL(){return *this;}
 constexpr bool gaugeScaleTrialActive(){return gaugePending;}
 constexpr int esp_wifi_start(){++starts;return ESP_OK;}
 constexpr void wait(unsigned){++waits;}
 constexpr void setPhase(unsigned){}
 constexpr void recordWifiRunning(bool v){_wifi_running=v;}
'''+methods+r'''
 constexpr bool startRadio(){
'''+block+r'''
 return _wifi_running;
 }
};
constexpr bool cases(){
 for(int queued:{0,1}){
  Fixture f;if(queued)f.approvedRequest.value=true;else f.checkQueued.value=true;
  // OTA busy still refuses ordinary gauge/NAND admission.
  if(!f.busy()||f.tryStorageLease()||f.storageLeaseDepth)return false;
  if(!f.startRadio()||f.starts!=1||f.storageLeaseDepth||f.storageLeaseMutex.depth)return false;
  if(!f.startRadio()||f.starts!=1)return false; // already-running WiFi untouched
 }
 {Fixture f;f.current=&f.gauge;if(!f.tryStorageLease())return false;
  f.current=&f.network;f.checkQueued.value=true;
  if(f.tryNetworkStartLease()||f.startRadio()||f.starts||f.storageLeaseDepth!=1)return false;
  f.releaseStorageLease();if(f.storageLeaseDepth!=1)return false;
  f.current=&f.gauge;f.releaseStorageLease();if(f.storageLeaseDepth||f.storageLeaseMutex.depth)return false;}
 {Fixture f;f.checkQueued.value=true;f.gaugePending=true;
  if(f.startRadio()||f.starts||f.storageLeaseDepth||f.storageLeaseMutex.depth||!f.waits)return false;}
 {Fixture f;f.checkQueued.value=true;if(!f.tryNetworkStartLease()||!f.tryNetworkStartLease()||f.storageLeaseDepth!=2)return false;
  if(f.tryStorageLease()||f.storageLeaseDepth!=2)return false;
  f.releaseStorageLease();if(!f.currentTaskOwnsStorageLease())return false;f.releaseStorageLease();if(f.currentTaskOwnsStorageLease())return false;}
 {Fixture f;f.current=nullptr;if(f.tryNetworkStartLease()||f.storageLeaseMutex.depth)return false;}
 {Fixture f;f.storageLeaseDepth=UINT32_MAX;if(f.tryNetworkStartLease()||f.storageLeaseMutex.depth)return false;}
 return true;
}
static_assert(cases(),"queued OTA can start WiFi; ordinary busy guard, gauge owner and gauge Pending remain effective");
'''
  found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
  compiler=os.environ.get('CXX') or (str(found[-1]) if found else None)
  if not compiler:self.skipTest('set CXX or use installed target compiler')
  logdir=ROOT/'artifacts/network-start-lease-tests';logdir.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(dir=logdir) as directory:
   source=Path(directory)/'network.cpp';source.write_text(code,encoding='utf-8')
   result=subprocess.run([compiler,'-std=c++20','-DMOSAICO_BOARD=1','-fsyntax-only',str(source)],capture_output=True,text=True)
   log=logdir/'constexpr.log';log.write_text(result.stdout+result.stderr,encoding='utf-8')
   self.assertEqual(result.returncode,0,str(log)+'\n'+result.stderr)

 def test_request_check_caches_real_partition_before_queue(self):
  request=function(OTA,'bool requestCheck(')
  self.assertLess(request.index('cachedRunning.store(esp_ota_get_running_partition())'),request.index('checkQueued.store(true)'))
  self.assertLess(request.index('!guard.owns_lock() || busy()'),request.index('cachedRunning.store('))
  self.assertIn('StorageAdmission storageAdmission;',request)
  self.assertIn('TouchSleep::OtaAdmission touchGuard;',request)

 def test_only_radio_uses_narrow_admission(self):
  self.assertEqual(NETWORK.count('MosaicoOta::tryNetworkStartLease()'),1)
  self.assertIn('!task||busy()||storageLeaseDepth==UINT32_MAX',function(OTA,'bool tryStorageLease('))
  narrow=function(OTA,'bool tryNetworkStartLease(')
  self.assertIn('storageLeaseMutex.try_lock()',narrow)
  self.assertNotIn('busy()',narrow)
  self.assertNotIn('new ',narrow)

if __name__=='__main__':unittest.main()
