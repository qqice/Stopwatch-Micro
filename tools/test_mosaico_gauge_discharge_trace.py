"""Bounded recorder model/fault assertions; no hardware, SDK build, or flash."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = (ROOT/'main/host/gauge_discharge_trace.cpp').read_text(encoding='utf-8-sig')
COMPILER = os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++') or 'C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'

class TraceTests(unittest.TestCase):
    def test_cpp_model_faults_and_recovery(self):
        code = r'''
#include "main/host/gauge_discharge_trace_model.h"
using namespace GaugeDischargeTrace;
constexpr bool timing() {
 Model m;if(m.due(0)||m.armed||m.count)return false;
 m.armed=true;if(!m.due(0))return false;
 Sample s;s.flags=Valid|CapacityValid;s.soc=70;s.voltageMv=3800;s.currentMa=-12;
 m.append(s);if(m.due(59999)||!m.due(60000)||m.checkpointDue(60000))return false;
 s.monotonicMs=60000;s.soc=15;s.voltageMv=3500;m.append(s);
 if(m.due(69999)||!m.due(70000)||!m.checkpointDue(60000))return false;
 m.attempted(60000);if(m.checkpointDue(119999)||!m.checkpointDue(120000))return false;
 s.monotonicMs=70000;s.flags=0;m.append(s);if(m.due(79999)||!m.due(80000))return false;
 s.flags=Valid;s.soc=0;s.voltageMv=3299;s.monotonicMs=80000;m.append(s);
 if(!low(s)||writable(s)||!m.due(90000))return false;
 s.voltageMv=3300;if(!writable(s))return false;
 s.soc=50;s.voltageMv=3499;if(!low(s))return false;
 s.flags=0;if(writable(s)||low(s))return false;
 return true;
}
constexpr bool roundtrip() {
 Model m;m.armed=true;m.boot=4;
 for(unsigned i=0;i<70;++i){Sample s;s.monotonicMs=i*10000;s.flags=31;s.soc=i%101;s.voltageMv=3400;s.currentMa=-25;s.averageCurrentMa=7;s.temperatureDeciK=2981;s.epoch=1800000000+i;m.append(s);}
 if(m.count!=64||m.at(0).monotonicMs!=60000)return false;
 const Blob saved=encode(m);Model restored;
 if(!decode(saved,restored)||!restored.armed||restored.at(0).currentMa!=-25||restored.at(63).averageCurrentMa!=7||restored.at(63).temperatureDeciK!=2981)return false;
 // Recovery starts another segment; monotonic time may restart only on a new boot.
 ++restored.boot;Sample s;s.flags=Valid;s.voltageMv=3300;s.monotonicMs=0;restored.append(s);
 Model recovered;if(!decode(encode(restored),recovered)||recovered.at(63).boot!=5)return false;
 // Torn/corrupt payload never replaces caller state, including armed flag.
 for(unsigned i=0;i<8;++i){Blob bad=saved;bad[i*400]^=0x80;Model out;out.boot=99;
  if(decode(bad,out)||out.boot!=99||out.armed)return false;}
 Blob bad=saved;bad[4]=2;put(bad,BlobSize-4,crc(bad),4);if(decode(bad,recovered))return false;
 bad=saved;bad[11]=4;put(bad,BlobSize-4,crc(bad),4);if(decode(bad,recovered))return false;
 bad=saved;put(bad,8,65,2);put(bad,BlobSize-4,crc(bad),4);if(decode(bad,recovered))return false;
 bad=saved;put(bad,32+RecordSize+4,0,8);put(bad,BlobSize-4,crc(bad),4);if(decode(bad,recovered))return false;
 return BlobSize<=4096;
}
constexpr bool finalize() {
 Model m;m.armed=true;Sample s;s.flags=Valid;s.soc=0;s.voltageMv=3200;s.currentMa=-10;
 m.append(s);if(!m.armed||!m.lowSeen||m.finalizedChargeAfterLow)return false;
 s.monotonicMs=10000;m.append(s);if(!m.armed)return false;
 s.monotonicMs=20000;s.currentMa=9;m.append(s);if(!m.armed)return false;
 s.monotonicMs=30000;s.flags=0;m.append(s);if(!m.armed)return false;
 s.monotonicMs=40000;s.flags=Valid;m.append(s);if(!m.armed)return false;
 s.monotonicMs=50000;m.append(s);if(m.armed||!m.finalizedChargeAfterLow||m.due(90000))return false;
 Model loaded;if(!decode(encode(m),loaded)||loaded.armed||!loaded.lowSeen||!loaded.finalizedChargeAfterLow)return false;
 return true;
}
constexpr bool physicalLowRecovery() {
 Model m;m.armed=true;Sample s;s.flags=Valid;s.soc=27;s.voltageMv=3500;
 s.currentMa=-11;s.averageCurrentMa=-9;m.append(s);
 if(!m.lowSeen || !m.armed || !writable(s))return false;
 // A physical-low latch survives reboot even when the gauge never reports SOC<=5.
 Model reboot;if(!decode(encode(m),reboot)||!reboot.lowSeen)return false;
 ++reboot.boot;
 s.soc=30;s.voltageMv=3700;s.currentMa=12;s.averageCurrentMa=-1;s.monotonicMs=0;
 reboot.append(s);if(!reboot.armed || reboot.finalizedChargeAfterLow)return false;
 s.monotonicMs=60000;reboot.append(s);
 if(reboot.armed || !reboot.finalizedChargeAfterLow || reboot.count!=3)return false;
 Model frozen;if(!decode(encode(reboot),frozen))return false;
 const Blob evidence=encode(frozen);
 if(frozen.due(999999999) || frozen.armed || frozen.at(0).soc!=27 || frozen.at(0).voltageMv!=3500 || frozen.at(0).currentMa!=-11)return false;
 if(!verifiedReadback(evidence,encode(frozen),BlobSize))return false;
 // Positive current at low voltage alone must not create the discharge latch.
 Model charging;charging.armed=true;s.voltageMv=3400;s.soc=50;s.averageCurrentMa=10;
 charging.append(s);if(charging.lowSeen)return false;
 return true;
}
static_assert(physicalLowRecovery(),"bad SOC physical-low reboot then charge freezes evidence");
constexpr bool readbackFaults() {
 Model m;m.armed=true;const Blob expected=encode(m);
 if(!verifiedReadback(expected,expected,BlobSize))return false;
 if(verifiedReadback(expected,expected,BlobSize-1))return false;
 Model old;const Blob previous=encode(old);
 if(verifiedReadback(expected,previous,BlobSize))return false;
 Blob damaged=expected;damaged[40]^=1;
 if(verifiedReadback(expected,damaged,BlobSize))return false;
 // Even a self-consistent CRC is insufficient if bytes differ from the requested record.
 put(damaged,BlobSize-4,crc(damaged),4);
 if(verifiedReadback(expected,damaged,BlobSize))return false;
 return true;
}
static_assert(readbackFaults(),"readback exact length CRC and intended bytes");
static_assert(finalize(),"zero continues recording until two valid charging samples");
static_assert(timing(),"cadence invalid low zero threshold");
static_assert(roundtrip(),"ring crc power loss canonical schema recovery");
'''
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'trace.cpp'; source.write_text(code)
            result=subprocess.run([COMPILER,'-std=c++17','-fconstexpr-ops-limit=200000000','-fsyntax-only','-I',str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/gauge-discharge-trace-model.log'
            log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_commit_readback_and_export_ownership(self):
        save=BACKEND.split('bool save(',1)[1].split('Sample convert(',1)[0]
        self.assertIn('Blob verifyBlob;',BACKEND)
        self.assertNotIn('Blob verifyBlob;',save)
        # Readback is reached only after a successful commit ACK; an ACK-lost error
        # returns storageError and leaves the pending request/dirty evidence intact.
        self.assertIn('if(err==ESP_OK)err=nvs_commit(h);',save)
        after_commit=save.split('err=nvs_commit(h);',1)[1]
        self.assertLess(after_commit.index('if(err==ESP_OK){'),after_commit.index('NVS_READONLY'))
        self.assertIn('nvs_get_blob(h,Key,verifyBlob.data(),&size)',save)
        self.assertIn('!verifiedReadback(ioBlob,verifyBlob,size)',save)
        self.assertIn('storageError=err!=ESP_OK;return !storageError;',save)
        self.assertIn('if(save(next)){model=next;model.dirty=false;pending=Command::None;',BACKEND)
        self.assertIn('if(save(model))model.dirty=false;',BACKEND)
        export=BACKEND.split('void exportRows(',1)[1]
        self.assertIn('Model exportSnapshot;',BACKEND)
        self.assertNotIn('Model copy;',export)
        self.assertIn('exportLock(exportMutex,std::try_to_lock)',export)
        self.assertIn('if(!exportLock)',export)
        self.assertIn('auto& copy=exportSnapshot;',export)
        # The model lock scope closes before the first snapshot emission. The
        # independent export lock stays alive through every bounded row callback.
        copy_scope=export.split('copy=model;',1)[1].split('emitStatus(',1)[0]
        self.assertIn('}',copy_scope)
        self.assertNotIn('exportLock.unlock',export)

    def test_actual_service_and_nvs_faults(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'trace-service.cpp';source.write_text(actual_service_fixture())
            result=subprocess.run([COMPILER,'-std=c++20','-fconstexpr-ops-limit=200000000','-fsyntax-only','-I',str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/gauge-discharge-trace-service.log'
            log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_passive_and_bounded_source(self):
        for forbidden in ('esp_restart','esp_timer_create','xTaskCreate','vTaskDelay','nvs_erase','gaugeSet','OCV','lightSleep','esp_pm_','esp_wifi_'):
            self.assertNotIn(forbidden,BACKEND)
        self.assertIn('gaugeTraceTrySample(t)',BACKEND)
        self.assertIn('if(!writable(s) || otaBlocked())return;',BACKEND)
        self.assertIn('now-lastWriteAttempt>=60000',BACKEND)
        self.assertIn('MosaicoOta::busy() || MosaicoOta::healthPending()',BACKEND)
        self.assertEqual(BACKEND.count('nvs_set_blob('),1)
        self.assertEqual(BACKEND.count('nvs_commit('),1)
        load=BACKEND.split('void load()',1)[1].split('bool save(',1)[0]
        self.assertIn('NVS_READONLY',load)
        self.assertNotIn('nvs_set',load)
        export=BACKEND.split('void exportRows(',1)[1]
        self.assertNotIn('nvs_',export)
        self.assertNotIn('pending=',export)
        self.assertIn('copy.count',export)
        self.assertIn('corrupt || model.count || model.armed',BACKEND)



# Extract complete production function bodies rather than reproducing service logic.
def extract_function(signature):
    start=BACKEND.index(signature)
    opening=BACKEND.index('{',start)
    depth=1
    end=opening+1
    while depth:
        depth += (BACKEND[end]=='{')-(BACKEND[end]=='}')
        end += 1
    return BACKEND[start:end]


def actual_service_fixture():
    functions='\n'.join(extract_function(s) for s in (
        'void load()', 'bool save(', 'Sample convert(', 'bool request(', 'void service()'))
    # Only external boundary adapters and constexpr-local definite initialization
    # differ. All production conditionals, calls, assignments, and order remain.
    functions=functions.replace('std::unique_lock<std::mutex>','MockLock')
    functions=functions.replace('std::try_to_lock','0')
    functions=functions.replace('MosaicoClock::snapshot()','clockSnapshot()')
    functions=functions.replace('nvs_handle_t h;','nvs_handle_t h=0;')
    for signature in ('void load()', 'bool save(', 'Sample convert(', 'bool request(', 'void service()'):
        functions=functions.replace(signature,'constexpr '+signature,1)
    return r'''
#include "main/host/gauge_discharge_trace_model.h"
using namespace GaugeDischargeTrace;
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
struct Hal { struct BatteryTelemetry {
 bool valid=false,capacityValid=false,temperatureValid=false,nominalConfigured=false;
 uint8_t reportedSoc=0;uint16_t voltageMv=0;
 int16_t currentMa=0,averageCurrentMa=0;
 uint16_t remainingMah=0,fullMah=0,designMah=0,operationStatus=0,batteryStatus=0,
 stateOfHealth=0,cycleCount=0,temperatureDeciK=0;
};};
struct MockMutex {bool available=true;};
struct MockLock {bool held;constexpr MockLock(MockMutex& m,int):held(m.available){} constexpr explicit operator bool()const{return held;}};
struct Fixture {
 enum class Command {None,Arm,Stop,Clear};
 MockMutex mutex;
 Model model,staging;
 Blob ioBlob{},verifyBlob{},disk{},candidate{};
 bool ready=false,corrupt=false,storageError=false,writeAttempted=false;
 uint64_t lastWriteAttempt=0,nowMs=0;
 Command pending=Command::None;
 const char* Namespace="gauge_trace";const char* Key="trace1";
 bool ota=false,admission=true,exists=false,readFail=false,readbackCorrupt=false,ackLost=false;
 unsigned writes=0,commits=0,reads=0;
 Hal::BatteryTelemetry telemetry{};
 constexpr bool otaBlocked()const{return ota;}
 constexpr Fixture& GetHAL(){return *this;}
 constexpr bool gaugeTraceTrySample(Hal::BatteryTelemetry& t){if(!admission)return false;t=telemetry;return true;}
 constexpr int64_t esp_timer_get_time()const{return int64_t(nowMs*1000);}
 struct Clock {bool valid;int64_t epoch;};
 constexpr Clock clockSnapshot()const{return {false,0};}
 constexpr int nvs_open(const char*,int mode,int* h){*h=mode;return exists||mode==NVS_READWRITE?ESP_OK:ESP_ERR_NVS_NOT_FOUND;}
 constexpr void nvs_close(int){}
 constexpr int nvs_get_blob(int,const char*,void* ptr,size_t*)=delete;
 constexpr int nvs_get_blob(int,const char*,uint8_t* ptr,size_t* size){
  ++reads;if(readFail)return ESP_FAIL;if(!exists)return ESP_ERR_NVS_NOT_FOUND;
  if(*size<BlobSize)return ESP_FAIL;*size=BlobSize;
  for(size_t i=0;i<BlobSize;++i)ptr[i]=disk[i];
  if(readbackCorrupt)ptr[10]^=1;return ESP_OK;
 }
 constexpr int nvs_set_blob(int,const char*,const uint8_t* p,size_t size){
  ++writes;if(size!=BlobSize)return ESP_FAIL;
  for(size_t i=0;i<BlobSize;++i)candidate[i]=p[i];return ESP_OK;
 }
 constexpr int nvs_commit(int){++commits;disk=candidate;exists=true;return ackLost?ESP_FAIL:ESP_OK;}
 constexpr void good(int soc=50,int mv=3800,int current=-10){
  telemetry.valid=true;telemetry.capacityValid=true;telemetry.reportedSoc=uint8_t(soc);
  telemetry.voltageMv=uint16_t(mv);telemetry.currentMa=int16_t(current);telemetry.averageCurrentMa=int16_t(current);
 }
''' + functions + r'''
};
constexpr bool pendingAndFailures(){
 Fixture f;f.good();f.service();if(!f.ready||f.model.armed||f.exists)return false;
 if(!f.request(Fixture::Command::Arm)||f.pending!=Fixture::Command::Arm||f.model.armed)return false;
 f.admission=false;f.service();if(f.writes||f.pending==Fixture::Command::None)return false;
 f.admission=true;f.telemetry.valid=false;f.service();if(f.writes)return false;
 f.good(50,3299);f.service();if(f.writes)return false;
 f.good();f.ota=true;f.service();if(f.writes)return false;
 f.ota=false;f.ackLost=true;f.service();
 if(f.model.armed||!f.storageError||f.pending!=Fixture::Command::Arm||f.commits!=1||f.reads)return false;
 // Disk may have committed despite lost acknowledgement; RAM remains pending.
 Model onDisk;if(!decode(f.disk,onDisk)||!onDisk.armed)return false;
 f.ackLost=false;f.nowMs=59999;f.service();if(f.commits!=1)return false;
 f.nowMs=60000;f.readbackCorrupt=true;f.service();
 if(f.model.armed||f.pending!=Fixture::Command::Arm||f.commits!=2||!f.storageError)return false;
 f.readbackCorrupt=false;f.nowMs=120000;f.service();
 if(!f.model.armed||f.pending!=Fixture::Command::None||f.storageError||f.commits!=3)return false;
 return true;
}
constexpr bool loadAndClear(){
 Fixture f;f.exists=true;f.disk[0]=1;f.good();f.service();
 if(!f.corrupt||f.model.armed||f.writes||f.request(Fixture::Command::Arm))return false;
 if(!f.request(Fixture::Command::Clear))return false;
 f.readFail=true;f.service(); // Commit ACK succeeds but readback fails: clear remains pending.
 if(!f.corrupt||f.pending!=Fixture::Command::Clear||!f.storageError)return false;
 f.readFail=false;f.nowMs=60000;f.service();
 if(f.corrupt||f.pending!=Fixture::Command::None||f.model.armed||f.model.count)return false;
 return true;
}
constexpr bool checkpointAndRecovery(){
 Fixture f;f.good();f.service();f.request(Fixture::Command::Arm);f.service();
 f.nowMs=1;f.service();if(f.model.count!=1||!f.model.armed)return false;
 f.good(27,3500,-12);f.nowMs=60001;f.service();
 if(!f.model.lowSeen||f.model.count!=2||f.model.dirty||f.commits!=2)return false;
 const Blob safe=f.disk;
 f.good(27,3299,-12);f.nowMs=120001;f.service();
 if(!f.model.dirty||f.commits!=2||!verifiedReadback(safe,f.disk,BlobSize))return false;
 Fixture reboot;reboot.disk=f.disk;reboot.exists=true;reboot.good(28,3700,12);reboot.service();
 if(!reboot.model.lowSeen||!reboot.model.armed||reboot.model.count!=3)return false;
 reboot.nowMs=60000;reboot.service();
 if(reboot.model.armed||!reboot.model.finalizedChargeAfterLow||reboot.model.count!=4||reboot.model.dirty)return false;
 const Blob frozen=reboot.disk;const size_t count=reboot.model.count;
 reboot.nowMs=3600000;reboot.service();
 if(reboot.model.count!=count||!verifiedReadback(frozen,reboot.disk,BlobSize))return false;
 Fixture end;end.exists=true;end.disk=frozen;end.good(80,4000,30);end.service();
 if(end.model.armed||!end.model.finalizedChargeAfterLow||end.model.count!=count||end.writes)return false;
 return end.model.at(1).soc==27&&end.model.at(1).voltageMv==3500;
}
static_assert(pendingAndFailures(),"actual service: queued arm invalid blocked low ACK lost readback corruption retry");
static_assert(loadAndClear(),"actual load/save: corrupt record explicit clear readback failure pending preservation");
static_assert(checkpointAndRecovery(),"actual service: safe checkpoint lowV no write reboot charge freeze evidence");
'''

if __name__=='__main__':
    unittest.main()
