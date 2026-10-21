"""Readiness runtime isolated compilation/source checks; no device or firmware build."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import tempfile
import unittest
import zlib
ROOT=Path(__file__).resolve().parents[1]
RUN_DIR=ROOT/'artifacts'/'gauge-scale-runtime-tests'/datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')
INC=ROOT/'main/hal/mosaico/mosaico_gauge_scale_runtime.inc'
CXX=os.environ.get('CXX','C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
FIXTURE=r'''
#define ESP_PLATFORM
#define MOSAICO_SCALE_TRIAL_MODEL_ONLY
#include <atomic>
#include <mutex>
#include "mosaico_gauge_engine_model.h"
#include "mosaico_gauge_status_model.h"
#include <array>
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include "mosaico_gauge_scale_journal.h"
#include "mosaico_gauge_physical_model.h"

struct Hal {
 enum class GaugeBootReloadStatus {Deferred,Skipped,Applied,Critical};
 struct GaugeBootReloadInfo {GaugeBootReloadStatus status=GaugeBootReloadStatus::Deferred;bool attempted=false;char reason[128]{};};
 using GaugeEngineSnapshot=mosaico_gauge_engine::Snapshot;
 std::array<uint8_t,6> getFactoryMac();
 bool gaugeThresholdAudit(bool,char*,size_t);
 bool gaugeScalePrepare(char*,size_t);bool gaugeScaleStatus(char*,size_t);bool gaugeScaleRestorePlan(char*,size_t);
};
Hal& GetHAL();
struct Power {bool locked,wifiRunning;int phase;};
struct Ble {bool advertising,connected;};
struct Network {Power powerStats();};struct Bluetooth {Ble diagnostics();};
Network& GetNetworkQuota();Bluetooth& GetCodexMicroBle();
namespace MosaicoOta {bool busy();bool healthPending();bool tryStorageLease();void releaseStorageLease();}
void* heap_caps_malloc(size_t,unsigned);
constexpr unsigned MALLOC_CAP_SPIRAM=1,MALLOC_CAP_8BIT=2;
void gauge_feed();int64_t esp_timer_get_time();
bool mockGaugeWord(uint8_t,uint16_t&);bool mockGaugeIdentity();
void mockEngine(Hal::GaugeEngineSnapshot&,uint8_t,uint8_t);
namespace {
void gauge_engine_locked(Hal::GaugeEngineSnapshot& s,uint8_t p,uint8_t o){mockEngine(s,p,o);}
Hal::GaugeBootReloadInfo gauge_boot_info{};
std::recursive_mutex gauge_transaction_mutex;std::mutex battery_mutex;
bool gauge;bool gauge_word(uint8_t r,uint16_t& v){return mockGaugeWord(r,v);}bool gauge_identity(){return mockGaugeIdentity();}
mosaico_threshold::Values gauge_threshold_cached{};uint32_t gauge_threshold_cached_generation;
mosaico_calibration::Values gauge_calibration_cached{};
mosaico_public_extra::Values gauge_public_extra_cached{};uint32_t gauge_public_extra_generation;
}
void* escaped_cache_addresses[]={&gauge,&gauge_threshold_cached,&gauge_calibration_cached,&gauge_public_extra_cached,&gauge_threshold_cached_generation,&gauge_public_extra_generation};
#include "mosaico_gauge_scale_runtime.inc"
namespace {void native_recovery_revoke_locked(){}bool scale_trial_boot_restore_locked(){return false;}bool scale_append_trial_diagnostics(char*,size_t){return true;}}
// Exercise root-owned entry points at compile time for signature checking.
void root_hooks(){std::lock_guard<std::recursive_mutex> t(gauge_transaction_mutex);
 (void)scale_boot_fence_locked();(void)scale_unit_context_locked();(void)scale_journal_missing_locked();scale_invalidate_context_locked();}
'''
class RuntimeTests(unittest.TestCase):
 def test_runtime_isolated_compiles_and_small_stack(self):
  logdir=RUN_DIR;logdir.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.gauge-scale-runtime-',dir=ROOT) as td:
   source=Path(td)/'runtime.cpp';source.write_text(FIXTURE)
   from test_mosaico_gauge_scale_trial import STUBS
   (Path(td)/'nvs.h').write_text(STUBS['nvs.h'])
   obj=logdir/'runtime.o'
   result=subprocess.run([CXX,'-std=c++20','-Wall','-Wextra','-Werror','-Wno-unused-function','-Os','-fstack-usage','-c','-I'+str(td),'-I'+str(INC.parent),str(source),'-o',str(obj)],capture_output=True,text=True)
   log=logdir/'runtime.log';log.write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(log))
   for line in obj.with_suffix('.su').read_text().splitlines():
    self.assertLessEqual(int(line.split('\t')[1]),512,line)
 def test_no_parameter_writer_keys_or_implicit_unit_context(self):
  src=INC.read_text()
  for token in ['gauge_control(', 'gauge_access_word(', 'i2c_master_transmit(', 'nvs_erase', 'EnterConfig', 'ExitReinit']:
   self.assertNotIn(token,src)
  self.assertIn('bool scale_both_images_verified=false;',src)
  self.assertIn('originalVerifiedThisBoot=false',src)
  self.assertIn('if(!p){scale_load_status=scale_journal::Status::IoError;',src)
  self.assertIn('if(!gaugeThresholdAudit(true,nullptr,0))',src)
  self.assertIn('gauge_public_extra_generation!=gauge_threshold_cached_generation',src)
  self.assertIn('uint8_t(f.width==1?v:v>>8)',src)
 def test_reviewed_private_candidate_crc_matches_embedded_17_changes(self):
  fixture=ROOT/'.artifacts/private/mosaico/scale2-reviewed-target-001.json'
  if not fixture.is_file():self.skipTest('private local candidate not available')
  candidate=json.loads(fixture.read_text())
  src=INC.read_text();chunk=src.split('static constexpr Change changes[]={',1)[1].split('};',1)[0]
  changes={int(a,0):(int(w),int(o,0),int(t,0)) for a,w,o,t in re.findall(r'\{(0x[0-9a-f]+),(\d+),(0x[0-9a-f]+|\d+),(0x[0-9a-f]+|\d+)\}',chunk)}
  self.assertEqual(len(changes),17)
  streams=[b'',b''];changed=0
  for field in candidate['all_fields']:
   address=int(field['address'],16);width=field['width'];original=bytes.fromhex(field['originalBE'])
   target=bytes.fromhex(field['targetBE'])
   # Models use three different cache representations. Reconstruct raw exactly.
   if width==1:
    threshold_word=original[0]<<8;extra_word=original[0]
    self.assertEqual(bytes([threshold_word>>8]),original)
    self.assertEqual(bytes([extra_word]),original)
   if original!=target:
    changed+=1;self.assertEqual(changes[address],(width,int.from_bytes(original,'big'),int.from_bytes(target,'big')))
   for i,raw in enumerate([original,target]):streams[i]+=address.to_bytes(2,'little')+bytes([width])+raw.ljust(4,b'\x00')
  self.assertEqual(changed,17)
  self.assertEqual(zlib.crc32(streams[0]),0x91336fc0)
  self.assertEqual(zlib.crc32(streams[1]),0x43de4704)
if __name__=='__main__':unittest.main()
