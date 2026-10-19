"""Bounded system-log model and source safety regression tests; no firmware/device."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main/host/mosaico_system_log.cpp').read_text()
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class SystemLogTests(unittest.TestCase):
    def test_codec_queue_crc_bounds_power_gate(self):
        self.assertTrue(COMPILERS)
        code=r'''
#include "mosaico_system_log_model.h"
using namespace MosaicoSystemLog::Model;
constexpr bool test(){
 Record r;r.type=Type::Gauge;r.flags=0x1234;r.monotonicMs=0x0102030405060708ULL;r.epoch=0x1020304050607080ULL;for(unsigned i=0;i<11;++i)r.args[i]=0x87654321U+i;
 const auto b=encode(r);if(b.size()!=64||b[0]!=2||b[2]!=0x34||b[4]!=8||b[11]!=1||b[12]!=0x80||b[20]!=0x21)return false;
 const auto d=decode(b);if(d.type!=r.type||d.flags!=r.flags||d.monotonicMs!=r.monotonicMs||d.epoch!=r.epoch||d.args!=r.args)return false;
 Queue q;for(unsigned i=0;i<128;++i){r.args[0]=i;if(!q.push(r))return false;}
 if(q.push(r)||q.count!=128||q.dropped!=1)return false;
 std::array<uint8_t,31*64> page{};if(q.copy(page.data(),31)!=31||page[20]!=0||page[64+20]!=1)return false;
 if(q.consume(129)||q.count!=128||!q.consume(31))return false;
 if(q.copy(page.data(),31)!=31||page[20]!=31)return false;
 for(unsigned i=0;i<31;++i){r.args[0]=128+i;if(!q.push(r))return false;}
 if(!q.consume(128)||q.count||q.consume(1))return false;
 if(q.copy(page.data(),31)!=0)return false;
 const uint8_t check[]={'1','2','3','4','5','6','7','8','9'};
 if(crc(check,9)!=0xcbf43926U||crc(check,0)!=0)return false;
 if(!chunkBounds(0,128)||!chunkBounds(15,128)||chunkBounds(16,128)||chunkBounds(0,127))return false;
 if(!powerGate(true,true,true,true,true,3300,2832)||!powerGate(true,true,true,true,true,4200,3181))return false;
 for(unsigned i=0;i<5;++i){bool a[5]={true,true,true,true,true};a[i]=false;if(powerGate(a[0],a[1],a[2],a[3],a[4],4000,3000))return false;}
 if(powerGate(true,true,true,true,true,3299,3000)||powerGate(true,true,true,true,true,4000,2831)||powerGate(true,true,true,true,true,4000,3182))return false;
 return true;
}
static_assert(test());
struct Snapshot {uint64_t sampledUs=0;uint8_t phase=0,outcome=0;};
constexpr bool bootRetry(){
 for(uint8_t terminal=1;terminal<=3;++terminal){
  BootCapture capture;Queue q;Snapshot before{100,1,0},after{};
  unsigned gaugeAccepted=0,outcomeAccepted=0;
  auto gauge=[&](const Snapshot& snap){Record r;r.type=Type::GaugeBoot;r.monotonicMs=snap.sampledUs;r.args[1]=snap.phase|(uint32_t(snap.outcome)<<8);if(!q.push(r))return false;++gaugeAccepted;return true;};
  auto outcome=[&](uint8_t result,bool attempted){Record r;r.type=Type::Outcome;r.args[0]=result;r.args[1]=attempted;if(!q.push(r))return false;++outcomeAccepted;return true;};
  // Deferred and None never finish or emit repeated boot-before records.
  for(unsigned i=0;i<3;++i){before.sampledUs+=100;if(capture.update(before,after,false,gauge,outcome))return false;}
  after={500,2,0};if(capture.update(before,after,false,gauge,outcome)||q.count)return false;
  before.outcome=terminal;after.outcome=terminal;
  Record filler;for(unsigned i=0;i<128;++i)q.push(filler);
  // Queue rejects before: acknowledgement must not advance.
  if(capture.update(before,after,true,gauge,outcome)||capture.beforeSent)return false;
  q.consume(1);
  // Before accepted; after rejected. Retry must not enqueue before again.
  if(capture.update(before,after,true,gauge,outcome)||!capture.beforeSent||capture.afterSent||gaugeAccepted!=1)return false;
  q.consume(1);
  // After accepted; Outcome rejected. Retry must retain both gauge acks.
  if(capture.update(before,after,true,gauge,outcome)||!capture.afterSent||capture.outcomeSent||gaugeAccepted!=2)return false;
  q.consume(1);
  if(!capture.update(before,after,true,gauge,outcome)||!capture.complete||outcomeAccepted!=1||gaugeAccepted!=2)return false;
  if(!capture.update(before,after,true,gauge,outcome)||gaugeAccepted!=2||outcomeAccepted!=1)return false;
 }
 BootCapture incomplete;Snapshot absent{},terminal{100,2,1};
 auto forbidden=[](const Snapshot&){return false;};auto result=[](uint8_t,bool){return false;};
 return !incomplete.update(absent,terminal,false,forbidden,result)&&!incomplete.complete;
}
static_assert(bootRetry());

'''
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'test.cpp';p.write_text(code)
            result=subprocess.run([str(COMPILERS[0]),'-std=c++20','-Wall','-Wextra','-Werror','-fsyntax-only','-I',str(ROOT/'main/host'),str(p)],capture_output=True,text=True)
            log=ROOT/'artifacts/system-log/model-test.log';log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_secret_and_hook_boundary(self):
        hook=SOURCE.split('int logHook(',1)[1].split('bool otaIdle()',1)[0]
        self.assertIn('esp_ptr_in_drom(fmt)',hook)
        self.assertIn('!xPortInIsrContext()',hook)
        self.assertIn('spi_flash_cache_enabled()',hook)
        self.assertIn('hookLock.test_and_set',hook)
        self.assertEqual(hook.count('va_copy(copy,ap)'),2)
        self.assertEqual(hook.count('va_end(copy)'),2)
        for forbidden in ['va_arg','vsnprintf','malloc','logAppend','logRead','GetHAL','nvs_','taskYIELD']:
            self.assertNotIn(forbidden,hook)
        for forbidden in ['peer_ip','peer_port','endpoint_port','peer_key','ssid','password','token','formatConfirm','NVS_READWRITE','nvs_set_','gaugeModeAudit']:
            self.assertNotIn(forbidden,SOURCE)
        self.assertIn('MosaicoPanicNumeric',SOURCE)

    def test_worker_admission_and_reader_boundary(self):
        begin=SOURCE.split('void begin(uint32_t id)',1)[1].split('void service()',1)[0]
        for forbidden in ['GetHAL','logLoad','logAppend','logRead','nvs_','xTaskCreate']:
            self.assertNotIn(forbidden,begin)
        read=SOURCE.split('void processRead(',1)[1].split('void flush(',1)[0]
        self.assertIn('!admitted()||!admitted()',read)
        self.assertNotIn('logLoad',read)
        self.assertNotIn('logAppend',read)
        self.assertIn('MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)!=pdPASS',SOURCE)
        self.assertIn('ulTaskNotifyTake(pdTRUE,portMAX_DELAY)',SOURCE)
        self.assertIn('nextAttempt=now+60000',SOURCE)
        self.assertIn('60000ULL<<',SOURCE)
        chunk=SOURCE.split('bool readChunk(uint64_t seq',1)[1].split('void status(',1)[0]
        for forbidden in ['GetHAL','logRead','logLoad','logAppend','nvs_']:
            self.assertNotIn(forbidden,chunk)
        self.assertNotIn('batteryTelemetry(',SOURCE)
        self.assertNotIn('taskYIELD()',SOURCE)
        self.assertIn('pendingConsume=count',SOURCE)
        self.assertIn('if(!consumeDurable())continue',SOURCE)
        self.assertIn('result.cleanupError==ESP_OK',SOURCE)
        self.assertEqual(SOURCE.count('StorageLease lease;'),4)
        self.assertIn('enqueue(r,false)',SOURCE)
        service=SOURCE.split('void service(){',1)[1].split('bool requestFlush()',1)[0]
        self.assertLess(service.index('if(!otaIdle())'),service.index('xTaskCreateWithCaps'))
        self.assertIn('if(s.phase!=mosaico_gauge_status::Explicit){r.epoch=0;r.flags&=~uint16_t(1);}',SOURCE)
        for transaction in ['logLoad','logAppend','logRead']:
            prefix=SOURCE.split('MosaicoNandStore::'+transaction+'(',1)[0]
            self.assertGreater(prefix.rfind('!admitted()||!admitted()'),prefix.rfind('StorageLease lease;'))
        import re
        fmt=SOURCE.split('void status(char* out,size_t len)',1)[1].split('snprintf(out,len,"',1)[1].split('"',1)[0]
        worst=re.sub(r'%llu','18446744073709551615',fmt)
        worst=re.sub(r'%ld','-2147483648',worst)
        worst=re.sub(r'%lu','4294967295',worst)
        worst=re.sub(r'%u','4294967295',worst)
        self.assertLessEqual(len(worst),500)
        self.assertIn('chunkBounds(index,capacity)',chunk)
        self.assertIn('readSeq==seq',chunk)

if __name__=='__main__':unittest.main()


