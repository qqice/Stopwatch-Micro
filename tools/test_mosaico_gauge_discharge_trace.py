"""Bounded recorder model/fault assertions; no hardware, SDK build, or flash."""
from pathlib import Path
import json
import re
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
#include "main/host/gauge_trace_storage_codec.h"
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
 Blob bad=saved;bad[4]=3;put(bad,BlobSize-4,crc(bad),4);if(decode(bad,recovered))return false;
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
constexpr bool rawRoundtrip(){
 Model m;m.armed=true;Sample s;s.flags=Valid|RawCoulombValid;s.rawCoulombMah=54321;m.append(s);
 auto b=encode(m);Model out;if(get(b,4,2)!=2||!decode(b,out)||out.at(0).rawCoulombMah!=54321)return false;
 b[4]=1;put(b,BlobSize-4,crc(b),4);if(decode(b,out))return false;
 s.flags=Valid;m={};m.append(s);b=encode(m);return get(b,4,2)==1&&decode(b,out)&&!out.at(0).rawCoulombMah;
}
static_assert(rawRoundtrip(),"rawCC valid flag schema2 exact lossless schema1 compatibility");
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
        self.assertIn('Blob ioBlob{}, verifyBlob{};',BACKEND)
        self.assertNotIn('Blob verifyBlob;',save)
        # Readback is reached only after a successful commit ACK; an ACK-lost error
        # returns storageError and leaves the pending request/dirty evidence intact.
        self.assertIn('if(err==ESP_OK){stage=FailureStage::Commit;err=nvs_commit(h);}',save)
        after_commit=save.split('err=nvs_commit(h);',1)[1]
        self.assertLess(after_commit.index('if(err==ESP_OK){'),after_commit.index('NVS_READONLY'))
        self.assertIn('nvs_get_blob(h,Key,readback.bytes.data(),&size)',save)
        self.assertIn('!verifiedReadback(ioBlob,verifyBlob,BlobSize)',save)
        self.assertIn('storageError=err!=ESP_OK;return !storageError;',save)
        self.assertIn('if(save(next)){model=next;model.dirty=false;pending=Command::None;',BACKEND)
        self.assertIn('if(save(model))model.dirty=false;',BACKEND)
        export=BACKEND.split('void exportRows(',1)[1]
        self.assertIn('Model model, staging, exportSnapshot;',BACKEND)
        self.assertNotIn('Model copy;',export)
        self.assertIn('exportLock(exportMutex,std::try_to_lock)',export)
        self.assertIn('if(!exportLock)',export)
        self.assertIn('auto& copy=storage->exportSnapshot;',export)
        # The model lock scope closes before the first snapshot emission. The
        # independent export lock stays alive through every bounded row callback.
        copy_scope=export.split('storage->exportSnapshot=storage->model;',1)[1].split('emitStatus(',1)[0]
        self.assertIn('}',copy_scope)
        self.assertNotIn('exportLock.unlock',export)
        self.assertIn('char traceLine[640]{};',BACKEND)
        status=extract_function('void status(')
        emit_status=extract_function('void emitStatus(')
        self.assertIn('exportLock(exportMutex,std::try_to_lock)',status)
        self.assertIn('if(!exportLock)',status)
        self.assertIn('storage->exportSnapshot=storage->model;',status)
        status_copy_scope=status.split('storage->exportSnapshot=storage->model;',1)[1].split('emitStatus(',1)[0]
        self.assertIn('}',status_copy_scope)
        self.assertIn('emitStatus(emit,ctx,storage->exportSnapshot,',status)
        for function in (status,export,emit_status):
            self.assertNotIn('char line[',function)
            self.assertNotIn('Model copy;',function)
        self.assertIn('std::snprintf(traceLine,sizeof(traceLine)',emit_status)
        self.assertIn('std::snprintf(traceLine,sizeof(traceLine)',export)

    def test_storage_is_explicit_external_and_lazy(self):
        storage=BACKEND.split('struct Storage {',1)[1].split('\n};',1)[0]
        self.assertIn('Model model, staging, exportSnapshot;',storage)
        self.assertIn('Blob ioBlob{}, verifyBlob{};',storage)
        self.assertIn('char traceLine[640]{};',storage)
        allocation=extract_function('bool ensureStorage(')
        self.assertIn('heap_caps_calloc(1,sizeof(Storage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)',allocation)
        self.assertIn('storage=new(memory) Storage;',allocation)
        self.assertIn('now-lastAllocationAttempt<60000',allocation)
        self.assertIn('if(storage)return true;',allocation)
        self.assertNotIn('nvs_',allocation)
        for signature in ('void status(', 'void exportRows('):
            readonly=extract_function(signature)
            self.assertNotIn('ensureStorage(',readonly)
            self.assertIn('if(!allocated){emitUnavailable(',readonly)
        unavailable=extract_function('void emitUnavailable(')
        self.assertIn('char line[288];',unavailable)
        for key in ('storage_external','storage_bytes','allocation_error','internal_free','internal_largest'):
            self.assertIn(key,unavailable)
            self.assertIn(key,extract_function('void emitStatus('))

    def test_actual_service_and_nvs_faults(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'trace-service.cpp';source.write_text(actual_service_fixture())
            result=subprocess.run([COMPILER,'-std=c++20','-fconstexpr-ops-limit=200000000','-fsyntax-only','-I',str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/gauge-discharge-trace-service.log'
            log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_diagnostic_json_bounds_and_api(self):
        for signature in ('void emitStatus(', 'void emitNvsStatus(', 'void exportRows('):
            function=extract_function(signature)
            match=re.search(r'std::snprintf\(traceLine,sizeof\(traceLine\),"((?:\\.|[^"\\])*)"',function)
            self.assertIsNotNone(match,signature)
            fmt=json.loads('"'+match.group(1)+'"')
            # ESP32 long/unsigned are 32 bits; uint64 monotonic/epoch are 64 bits.
            rendered=re.sub(r'%llu|%lu|%ld|%u|%d',lambda m: {'%llu':'18446744073709551615','%lu':'4294967295','%ld':'-2147483648','%u':'4294967295','%d':'-2147483648'}[m.group()],fmt)
            self.assertLess(len(rendered),640,(signature,len(rendered)))
            json.loads(rendered)
        diagnostic=extract_function('void emitNvsStatus(')
        for key in ('last_nvs_error','failure_stage','stats_error','total','used','free','available','namespaces','encoded_bytes'):
            self.assertIn(key,diagnostic)
        self.assertIn('nvs_get_stats(nullptr,&stats)',diagnostic)
        self.assertIn('if(statsError!=ESP_OK)stats={};',diagnostic)
        self.assertIn('bool checkpointConfirm(){return request(Command::Checkpoint);}',BACKEND)
        self.assertIn('corrupt || model.armed || !model.count',extract_function('bool request('))

    def test_storage_codec_real_fixture_and_malformed(self):
        import struct
        import zlib
        fixture=ROOT/'.artifacts/private/mosaico/gauge-return-20261017T083713Z-001.json'
        private_fixture=fixture.exists()
        if private_fixture:
            original=json.loads(fixture.read_text(encoding='utf-8-sig'))
        else:
            # Deterministic synthetic evidence, no account/device data required.
            rows=[]
            for i in range(64):
                rows.append(dict(boot=3,monotonic_ms=i*10000,epoch=1700000000+i*10,
                    flags=31,soc=max(0,20-i),voltage_mv=3700-i,current_ma=-28,
                    average_current_ma=-28,remaining_mah=max(0,13-i//4),full_mah=65,
                    design_mah=65,operation_status=0xa6,battery_status=0x4029,
                    state_of_health=100,cycle_count=1,temperature_deci_k=2981))
            original={'status':dict(armed=0,low_seen=1,finalized_charge_after_low=1,boot=3),'rows':rows}
        canonical=bytearray(3108)
        status=original['status']
        struct.pack_into('<IHHHBBI',canonical,0,0x31544447,1,48,len(original['rows']),status['armed'],status['low_seen']|status['finalized_charge_after_low']<<1,status['boot'])
        fields=[('boot',0,'I'),('monotonic_ms',4,'Q'),('epoch',12,'Q'),('flags',20,'H'),('soc',22,'H'),('voltage_mv',24,'H'),('current_ma',26,'h'),('average_current_ma',28,'h'),('remaining_mah',30,'H'),('full_mah',32,'H'),('design_mah',34,'H'),('operation_status',36,'H'),('battery_status',38,'H'),('state_of_health',40,'H'),('cycle_count',42,'H'),('temperature_deci_k',44,'H')]
        for i,row in enumerate(original['rows']):
            for name,offset,fmt in fields:struct.pack_into('<'+fmt,canonical,32+i*48+offset,row[name])
        struct.pack_into('<I',canonical,3104,zlib.crc32(canonical[:3104]))
        code=r'''#include "main/host/gauge_trace_storage_codec.h"
using namespace GaugeDischargeTrace;
using namespace GaugeDischargeTrace::StorageCodec;
constexpr Blob evidence={''' + ','.join(map(str,canonical)) + r'''};
constexpr bool actualRoundtrip(){
 Packed packed;pack(evidence,packed);Blob out;
 if(packed.size!=1070 || unpack(packed,out)!=Error::None || !verifiedReadback(evidence,out,BlobSize))return false;
 Model m;if(!decode(out,m)||m.count!=64||m.armed||!m.finalizedChargeAfterLow)return false;
 Packed legacy;legacy.bytes=evidence;legacy.size=BlobSize;
 if(unpack(legacy,out)!=Error::None||!verifiedReadback(evidence,out,BlobSize))return false;
 // Incompressible data must remain bounded and fall back without truncation.
 Blob noise;for(size_t i=0;i<BlobSize;++i)noise[i]=uint8_t((i*73+i/19)%255+1);
 put(noise,8,64,2);Packed fallback;pack(noise,fallback);
 return fallback.size==BlobSize&&fallback.bytes==noise;
}
constexpr bool malformed(){
 Packed good;pack(evidence,good);Blob out;
 Packed bad=good;--bad.size;if(unpack(bad,out)!=Error::Length)return false;
 bad=good;bad.bytes[4]=2;if(unpack(bad,out)!=Error::Format)return false;
 bad=good;bad.bytes[5]=2;if(unpack(bad,out)!=Error::Format)return false;
 bad=good;bad.bytes[10]=1;if(unpack(bad,out)!=Error::Format)return false;
 bad=good;bad.bytes[12]^=1;if(unpack(bad,out)!=Error::Crc)return false;
 bad=good;bad.size=BlobSize+1;if(unpack(bad,out)!=Error::Length)return false;
 bad=good;bad.size=15;if(unpack(bad,out)!=Error::Length)return false;
 bad=good;bad.bytes[bad.size++]=0x80;put(bad.bytes,8,bad.size-16,2);put(bad.bytes,12,frameCrc(bad),4);
 if(unpack(bad,out)!=Error::Length)return false; // Trailing decompressed bytes rejected.
 bad=good;bad.size=17;bad.bytes[16]=0x80;put(bad.bytes,8,1,2);put(bad.bytes,12,frameCrc(bad),4);
 if(unpack(bad,out)!=Error::Length)return false; // Decompression underrun.
 bad.bytes[16]=0x7f;put(bad.bytes,12,frameCrc(bad),4);if(unpack(bad,out)!=Error::Length)return false;
 bad.size=41;for(size_t i=16;i<41;++i)bad.bytes[i]=0xff;put(bad.bytes,8,25,2);put(bad.bytes,12,frameCrc(bad),4);
 if(unpack(bad,out)!=Error::Length)return false; // Expansion overrun before any out-of-bounds write.
 bad.bytes=evidence;bad.size=BlobSize;bad.bytes[42]^=1;
 return unpack(bad,out)==Error::Crc;
}
static_assert(actualRoundtrip(),"actual original64 canonical exact roundtrip 1070 bytes legacy fallback");
static_assert(malformed(),"frame CRC version codec reserved trailing truncated expansion bounds inner CRC");
'''
        if not private_fixture:
            code=code.replace('packed.size!=1070','packed.size>=BlobSize')
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'trace-codec.cpp';source.write_text(code)
            result=subprocess.run([COMPILER,'-std=c++20','-fconstexpr-ops-limit=200000000','-fsyntax-only','-I',str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/gauge-discharge-trace-codec.log'
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
        'void failure(', 'bool ensureStorage(', 'void load()', 'bool save(', 'Sample convert(', 'bool request(', 'void service()'))
    # Only external boundary adapters and constexpr-local definite initialization
    # differ. All production conditionals, calls, assignments, and order remain.
    functions=functions.replace('std::unique_lock<std::mutex>','MockLock')
    functions=functions.replace('std::try_to_lock','0')
    functions=functions.replace('MosaicoClock::snapshot()','clockSnapshot()')
    functions=functions.replace('nvs_handle_t h;','nvs_handle_t h=0;')
    functions=functions.replace('void* memory=heap_caps_calloc(1,sizeof(Storage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);','Fixture* memory=allocateStorage();')
    functions=functions.replace('storage=new(memory) Storage;','storage=memory;')
    for signature in ('void failure(', 'bool ensureStorage(', 'void load()', 'bool save(', 'Sample convert(', 'bool request(', 'void service()'):
        functions=functions.replace(signature,'constexpr '+signature,1)
    return r'''
#include "main/host/gauge_trace_storage_codec.h"
using namespace GaugeDischargeTrace;
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
struct Hal { struct BatteryTelemetry {
 bool valid=false,capacityValid=false,temperatureValid=false,nominalConfigured=false;
 uint8_t reportedSoc=0;uint16_t voltageMv=0;
 int16_t currentMa=0,averageCurrentMa=0;
 uint16_t remainingMah=0,fullMah=0,designMah=0,operationStatus=0,batteryStatus=0,
 stateOfHealth=0,cycleCount=0,temperatureDeciK=0,rawCoulombMah=0;bool rawCoulombValid=false;
};};
struct MockMutex {bool available=true;};
struct MockLock {bool held;constexpr MockLock(MockMutex& m,int):held(m.available){} constexpr explicit operator bool()const{return held;}};
struct Fixture {
 enum class Command {None,Arm,Stop,Clear,Checkpoint,Resume};
 enum class FailureStage : uint8_t {None,ReadOpen,Load,WriteOpen,Set,Commit,Reopen,Read,Length,Crc,Compare};
 esp_err_t lastNvsError=ESP_OK;FailureStage failureStage=FailureStage::None;
 MockMutex mutex;
 Model model,staging;
 Fixture* storage=nullptr;
 bool allocationError=false,allocationAttempted=false,allocationFail=false;
 uint64_t lastAllocationAttempt=0;unsigned allocationCalls=0;
 constexpr Fixture* allocateStorage(){++allocationCalls;return allocationFail?nullptr:this;}
 Blob ioBlob{},verifyBlob{},disk{},candidate{};
 StorageCodec::Packed packedIo{},packedVerify{};
 size_t diskSize=BlobSize,candidateSize=0,encodedBytes=0;
 bool ready=false,corrupt=false,storageError=false,writeAttempted=false;
 uint64_t lastWriteAttempt=0,nowMs=0;
 Command pending=Command::None;
 const char* Namespace="gauge_trace";const char* Key="trace1";
 bool ota=false,admission=true,exists=false,readFail=false,readbackCorrupt=false,ackLost=false;
 unsigned writes=0,commits=0,reads=0;
 int openReadError=0,openWriteError=0,setError=0,reopenError=0;
 bool readbackShort=false,readbackDifferent=false;
 Hal::BatteryTelemetry telemetry{};
 constexpr bool otaBlocked()const{return ota;}
 constexpr Fixture& GetHAL(){return *this;}
 constexpr bool gaugeTraceTrySample(Hal::BatteryTelemetry& t){if(!admission)return false;t=telemetry;return true;}
 constexpr int64_t esp_timer_get_time()const{return int64_t(nowMs*1000);}
 struct Clock {bool valid;int64_t epoch;};
 constexpr Clock clockSnapshot()const{return {false,0};}
 constexpr int nvs_open(const char*,int mode,int* h){
  *h=mode;if(mode==NVS_READWRITE && openWriteError)return openWriteError;
  if(mode==NVS_READONLY && openReadError)return openReadError;
  if(mode==NVS_READONLY && commits && reopenError)return reopenError;
  return exists||mode==NVS_READWRITE?ESP_OK:ESP_ERR_NVS_NOT_FOUND;
 }
 constexpr void nvs_close(int){}

 constexpr int nvs_get_blob(int,const char*,uint8_t* ptr,size_t* size){
  ++reads;if(readFail)return ESP_FAIL;if(!exists)return ESP_ERR_NVS_NOT_FOUND;
  if(!ptr){*size=diskSize;return ESP_OK;}
  if(*size<diskSize)return ESP_FAIL;*size=diskSize;
  for(size_t i=0;i<diskSize;++i)ptr[i]=disk[i];
  if(readbackShort)*size=diskSize-1;
  if(readbackCorrupt)ptr[diskSize-1]^=1;
  if(readbackDifferent){
   Blob other=canonicalDisk();other[10]^=1;put(other,BlobSize-4,crc(other),4);
   StorageCodec::Packed altered;StorageCodec::pack(other,altered);
   *size=altered.size;for(size_t i=0;i<altered.size;++i)ptr[i]=altered.bytes[i];
  }
  return ESP_OK;
 }
 constexpr int nvs_set_blob(int,const char*,const uint8_t* p,size_t size){
  ++writes;if(setError)return setError;if(size>BlobSize)return ESP_FAIL;
  candidate={};candidateSize=size;for(size_t i=0;i<size;++i)candidate[i]=p[i];return ESP_OK;
 }
 constexpr int nvs_commit(int){++commits;disk=candidate;diskSize=candidateSize;exists=true;return ackLost?ESP_FAIL:ESP_OK;}
 constexpr Blob canonicalDisk()const{StorageCodec::Packed stored;stored.bytes=disk;stored.size=diskSize;Blob canonical;StorageCodec::unpack(stored,canonical);return canonical;}
 constexpr void good(int soc=50,int mv=3800,int current=-10){
  telemetry.valid=true;telemetry.capacityValid=true;telemetry.reportedSoc=uint8_t(soc);
  telemetry.voltageMv=uint16_t(mv);telemetry.currentMa=int16_t(current);telemetry.averageCurrentMa=int16_t(current);
 }
 constexpr void frozen(){
  good();ready=true;storage=this;model.armed=true;
  Sample s;s.flags=Valid;s.voltageMv=3500;s.soc=27;s.currentMa=-10;
  for(unsigned i=0;i<62;++i){s.monotonicMs=i*10000;model.append(s);}
  s.currentMa=10;s.monotonicMs=620000;model.append(s);s.monotonicMs=630000;model.append(s);
  disk=encode(model);exists=true;
 }
''' + functions + r'''
};
constexpr bool allocationFailureRecovery(){
 Fixture f;Model persisted;persisted.armed=true;Sample row;row.flags=Valid;row.soc=40;row.voltageMv=3800;persisted.append(row);
 f.disk=encode(persisted);f.exists=true;f.good();f.allocationFail=true;f.service();
 if(f.storage||f.ready||!f.storageError||!f.allocationError||f.corrupt||f.reads||f.writes||f.allocationCalls!=1)return false;
 if(f.request(Fixture::Command::Clear)||f.request(Fixture::Command::Arm))return false;
 f.nowMs=59999;f.service();if(f.allocationCalls!=1||f.reads||f.writes)return false;
 f.allocationFail=false;f.nowMs=60000;f.service();
 if(!f.storage||!f.ready||f.storageError||f.allocationError||f.corrupt||f.allocationCalls!=2||!f.model.armed||f.model.count!=2||f.writes)return false;
 const Blob safe=f.canonicalDisk();f.nowMs=60001;f.service();
 return f.allocationCalls==2&&verifiedReadback(safe,f.canonicalDisk(),BlobSize)&&f.model.at(0).soc==40;
}
static_assert(allocationFailureRecovery(),"PSRAM allocation failure no IO bounded retry recovers prior evidence once");
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
 Model onDisk;if(!decode(f.canonicalDisk(),onDisk)||!onDisk.armed)return false;
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
 const Blob safe=f.canonicalDisk();
 f.good(27,3299,-12);f.nowMs=120001;f.service();
 if(!f.model.dirty||f.commits!=2||!verifiedReadback(safe,f.canonicalDisk(),BlobSize))return false;
 Fixture reboot;reboot.disk=f.disk;reboot.diskSize=f.diskSize;reboot.exists=true;reboot.good(28,3700,12);reboot.service();
 if(!reboot.model.lowSeen||!reboot.model.armed||reboot.model.count!=3)return false;
 reboot.nowMs=60000;reboot.service();
 if(reboot.model.armed||!reboot.model.finalizedChargeAfterLow||reboot.model.count!=4||reboot.model.dirty)return false;
 const Blob frozen=reboot.canonicalDisk();const size_t count=reboot.model.count;
 reboot.nowMs=3600000;reboot.service();
 if(reboot.model.count!=count||!verifiedReadback(frozen,reboot.canonicalDisk(),BlobSize))return false;
 Fixture end;end.exists=true;end.disk=frozen;end.good(80,4000,30);end.service();
 if(end.model.armed||!end.model.finalizedChargeAfterLow||end.model.count!=count||end.writes)return false;
 return end.model.at(1).soc==27&&end.model.at(1).voltageMv==3500;
}
constexpr bool diagnosticStages(){
 for(int kind=1;kind<=8;++kind){
  Fixture f;f.frozen();const Blob before=encode(f.model);const auto count=f.model.count;
  if(!f.request(Fixture::Command::Checkpoint))return false;
  if(kind==1)f.openWriteError=0x1105;
  if(kind==2)f.setError=0x1106;
  if(kind==3)f.ackLost=true;
  if(kind==4)f.reopenError=0x1107;
  if(kind==5)f.readFail=true;
  if(kind==6)f.readbackShort=true;
  if(kind==7)f.readbackCorrupt=true;
  if(kind==8)f.readbackDifferent=true;
  f.service();
  const int codes[]={0,0x1105,0x1106,ESP_FAIL,0x1107,ESP_FAIL,ESP_FAIL,ESP_FAIL,ESP_FAIL};
  if(!f.storageError||f.lastNvsError!=codes[kind]||int(f.failureStage)!=kind+2||f.pending!=Fixture::Command::Checkpoint)return false;
  if(f.model.count!=count||f.model.armed||!f.model.finalizedChargeAfterLow||!verifiedReadback(before,encode(f.model),BlobSize))return false;
  f.openWriteError=0;f.setError=0;f.ackLost=false;f.reopenError=0;f.readFail=false;
  f.readbackShort=false;f.readbackCorrupt=false;f.readbackDifferent=false;
  f.nowMs=59999;const unsigned attempts=f.writes;f.service();if(f.writes!=attempts)return false;
  f.nowMs=60000;f.service();
  if(f.storageError||f.pending!=Fixture::Command::None||f.model.dirty||f.lastNvsError!=codes[kind]||int(f.failureStage)!=kind+2)return false;
  if(!verifiedReadback(before,f.canonicalDisk(),BlobSize)||!verifiedReadback(before,encode(f.model),BlobSize))return false;
 }
 Fixture absent;if(absent.request(Fixture::Command::Checkpoint))return false;
 absent.good();absent.service();if(absent.request(Fixture::Command::Checkpoint))return false;
 Fixture busy;busy.frozen();busy.pending=Fixture::Command::Stop;if(busy.request(Fixture::Command::Checkpoint))return false;
 Fixture armed;armed.frozen();armed.model.armed=true;if(armed.request(Fixture::Command::Checkpoint))return false;
 Fixture bad;bad.frozen();bad.corrupt=true;if(bad.request(Fixture::Command::Checkpoint))return false;
 Fixture unsafe;unsafe.frozen();unsafe.good(50,3299);unsafe.request(Fixture::Command::Checkpoint);unsafe.service();
 if(unsafe.writes||unsafe.pending!=Fixture::Command::Checkpoint)return false;
 unsafe.good();unsafe.ota=true;unsafe.service();if(unsafe.writes)return false;
 Fixture readOpen;readOpen.openReadError=0x1108;readOpen.good();readOpen.service();
 if(readOpen.lastNvsError!=0x1108||int(readOpen.failureStage)!=1||readOpen.writes)return false;
 Fixture load;load.exists=true;load.readFail=true;load.good();load.service();
 if(load.lastNvsError!=ESP_FAIL||int(load.failureStage)!=2||load.writes)return false;
 Fixture corrupt;corrupt.exists=true;corrupt.good();corrupt.service();
 if(!corrupt.corrupt||int(corrupt.failureStage)!=2||corrupt.writes)return false;
 Fixture shortLoad;shortLoad.frozen();shortLoad.ready=false;shortLoad.readbackShort=true;shortLoad.service();
 // A truncated legacy blob is no longer legacy-sized and fails packed magic
 // validation (Format -> Load), not the packed frame's declared-length check.
 if(!shortLoad.corrupt||shortLoad.lastNvsError!=ESP_FAIL||shortLoad.failureStage!=Fixture::FailureStage::Load||shortLoad.writes)return false;
 Fixture packedShort;packedShort.frozen();StorageCodec::pack(packedShort.disk,packedShort.packedIo);
 packedShort.disk=packedShort.packedIo.bytes;packedShort.diskSize=packedShort.packedIo.size;
 packedShort.ready=false;packedShort.readbackShort=true;packedShort.service();
 if(!packedShort.corrupt||packedShort.lastNvsError!=ESP_FAIL||packedShort.failureStage!=Fixture::FailureStage::Length||packedShort.writes)return false;
 Fixture schema;schema.frozen();schema.ready=false;schema.disk[4]=3;put(schema.disk,BlobSize-4,crc(schema.disk),4);schema.service();
 return schema.corrupt&&int(schema.failureStage)==2&&!schema.writes;
}
constexpr bool compressedLegacyRewrite(){
 Fixture f;f.frozen();const Blob original=f.disk;
 if(f.diskSize!=BlobSize||!f.request(Fixture::Command::Checkpoint))return false;
 f.service();if(f.storageError||f.diskSize>=BlobSize||f.diskSize!=f.encodedBytes||f.commits!=1)return false;
 if(!verifiedReadback(original,f.canonicalDisk(),BlobSize))return false;
 Fixture boot;boot.disk=f.disk;boot.diskSize=f.diskSize;boot.exists=true;boot.good();boot.service();
 if(boot.corrupt||boot.writes||boot.model.armed||!boot.model.finalizedChargeAfterLow||boot.model.count!=64)return false;
 --boot.model.boot;return verifiedReadback(original,encode(boot.model),BlobSize);
}
static_assert(compressedLegacyRewrite(),"same-key legacy to packed rewrite reload preserves all64 frozen rows");
static_assert(diagnosticStages(),"exact NVS errors and all failure stages frozen checkpoint unchanged gated retry no erase");
static_assert(pendingAndFailures(),"actual service: queued arm invalid blocked low ACK lost readback corruption retry");
static_assert(loadAndClear(),"actual load/save: corrupt record explicit clear readback failure pending preservation");
static_assert(checkpointAndRecovery(),"actual service: safe checkpoint lowV no write reboot charge freeze evidence");
'''

if __name__=='__main__':
    unittest.main()
