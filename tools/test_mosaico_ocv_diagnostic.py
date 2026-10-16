"""Actual manual OCV helper fault tests plus extracted HAL wire/API guards."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class OcvTests(unittest.TestCase):
    def compile(self,code,extra=()):
        if not COMPILERS:self.skipTest('configured cross compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ocv.cpp';p.write_text(code)
            r=subprocess.run([str(COMPILERS[0]),'-std=c++17','-Wall','-Wextra','-Werror','-fsyntax-only','-I',str(ROOT/'main/hal/mosaico'),str(p)]+list(extra),capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_actual_model_qualification_one_attempt_and_edge(self):
        self.compile(r'''
#include <initializer_list>
#include "mosaico_ocv_diagnostic_model.h"
using namespace mosaico_ocv;
constexpr Sample normal(){return {2982,4194,0x5028,50,65,65,77,0x00a6,0,0};}
struct Fake {
 int mode=0,reads=0,keys=0,identities=0,waits=0;bool latched=false,wrong=false;
 int64_t now=0,sentAt=0;
 constexpr bool wasAttempted(){return latched;}
 constexpr bool identity(){++identities;return mode!=1;}
 constexpr bool snapshot(Sample& s){
  ++reads;s=normal();
  if(!keys&&now>=60000000){
   if(mode==2)return false;
   if(mode==3)s.currentMa=4;
   if(mode==4)s.voltageMv=4205; // valid absolute V, spread 11mV
   if(mode==5)s.temperatureDeciK=2988; // spread 6dK
   if(mode==6)s.averageMa=3; // valid absolute I, spread 3mA
   if(mode==7)s.operation=0x00a4;
   if(mode==8)s.batteryStatus|=0x100;
  }
  if(mode==21&&!keys)s.batteryStatus|=0x2000;
  if(mode==20&&!keys)s.batteryStatus&=~0x4000; // pre-clear alone is insufficient
  if(keys){
   const int64_t elapsed=now-sentAt;
   if(mode!=18&&mode!=19&&mode!=20)s.batteryStatus=elapsed?0x5028:0x1028;
   if(mode==14&&elapsed>=1000000)s.voltageMv=4205;
   if(mode==15&&elapsed>=1000000)return false;
   if(mode==16&&elapsed>=1000000)s.operation=0xa4;
   if(mode==17&&elapsed>=1000000)s.currentMa=3;
   if(mode==22&&elapsed>=1000000)s.batteryStatus|=0x2000;
   if(mode==23)s.batteryStatus&=~0x20;
   if(mode==19){s.soc=100;s.remainingMah=65;} // observation, never completion proof
  }
  return true;
 }
 constexpr int64_t nowUs(){return now;}
 constexpr void waitMs(uint32_t ms){
  ++waits;now+=int64_t(ms)*1000;
  if(waits==60){if(mode==9)now-=2000000;if(mode==10)now+=2000000;}
 }
 constexpr bool latchAttempt(){if(latched)return false;latched=true;return true;}
 constexpr SendReport sendOcv(Qualification& q){
  if(!latched||keys)wrong=true;
  SendReport r{};if(mode==24)return r;
  r.sample=normal();r.sampleValid=true;
  if(mode==25)r.sample.currentMa=3;
  if(mode==26)r.sample.batteryStatus|=0x2000;
  if(!q.sample(r.sample,now)||!q.ready(now))return r;
  ++keys;sentAt=now;r.result=mode==13?SendResult::AckFailed:SendResult::Acked;return r;
 }
};
constexpr Report run(Fake& f){Coordinator<Fake> c(f);return c.run();}
constexpr bool qualification(){
 Sample s=normal();Qualification q;
 for(int64_t t=0;t<=119000000;t+=1000000)if(!q.sample(s,t))return false;
 if(!q.sample(s,119999000)||q.ready(119999000)||!q.sample(s,120000000)||!q.ready(120000000)||q.ready(122500001))return false;
 for(int fault=0;fault<21;++fault){
  Qualification f;Sample bad=s;if(!f.sample(s,1000000))return false;int64_t t=2000000;
  if(fault==0)t=999999;
  if(fault==1)t=3500001;
  if(fault==2)bad.voltageMv=4149;
  if(fault==3)bad.voltageMv=4351;
  if(fault==4)bad.temperatureDeciK=2831;
  if(fault==5)bad.temperatureDeciK=3182;
  if(fault==6)bad.currentMa=4;
  if(fault==7)bad.averageMa=-4;
  if(fault==8)bad.dc=100;
  if(fault==9)bad.fcc=100;
  if(fault==10)bad.operation|=0x400;
  if(fault==11)bad.operation|=1;
  if(fault==12)bad.operation&=~0x20;
  if(fault==13)bad.operation=0xa4;
  if(fault==14)bad.batteryStatus&=~8;
  if(fault==15)bad.batteryStatus|=0x100;
  if(fault==16)bad.batteryStatus|=0x800;
  if(fault==17)bad.batteryStatus|=0x400;
  if(fault==18)bad.batteryStatus|=2;
  if(fault==19)bad.soc=101;
  if(fault==20)bad.batteryStatus|=0x2000;
  if(f.sample(bad,t)||!f.failed||f.sample(s,2000000)||f.ready(121000000))return false;
 }
 Qualification bounds;if(!bounds.sample(s,0))return false;
 s.voltageMv+=10;s.temperatureDeciK+=5;s.currentMa=s.averageMa=2;
 if(!bounds.sample(s,1000000))return false;
 return true;
}
constexpr bool cases(){
 Fake good;auto r=run(good);
 if(r.result!=Result::Confirmed||!r.sent||!r.ack||!r.edge||!r.conditions||good.keys!=1||good.wrong||good.identities!=2||good.now!=125000000||
  r.before.soc!=77||r.after.soc!=77||r.before.remainingMah!=50||r.after.remainingMah!=50||r.before.fcc!=65||r.after.fcc!=65)return false;
 for(int mode=1;mode<=10;++mode){Fake f;f.mode=mode;auto no=run(f);
  if(no.result!=Result::Rejected||f.keys||f.latched||no.sent||no.attempted||f.now>62000000)return false;
 }
 Fake ack;ack.mode=13;auto a=run(ack);
 if(a.result!=Result::SentAckFailed||!a.sent||a.ack||!a.edge||!a.conditions||ack.keys!=1||!ack.latched)return false;
 const int reads=ack.reads;auto retry=run(ack);
 if(retry.result!=Result::AlreadyAttempted||retry.sent||ack.keys!=1||ack.reads!=reads)return false;
 for(int mode=14;mode<=17;++mode){Fake f;f.mode=mode;auto disturbed=run(f);
  if(disturbed.result!=Result::PostDisturbed||!disturbed.sent||disturbed.conditions||f.keys!=1||!f.latched)return false;
 }
 for(int mode:{18,19,20}){Fake f;f.mode=mode;auto unconfirmed=run(f);
  if(unconfirmed.result!=Result::SentUnconfirmed||!unconfirmed.sent||unconfirmed.edge||!unconfirmed.conditions||f.keys!=1)return false;
  if(mode==19&&(unconfirmed.before.soc!=77||unconfirmed.after.soc!=100))return false;
 }
 Fake preFail;preFail.mode=21;auto pre=run(preFail);
 if(pre.result!=Result::Rejected||pre.sent||preFail.keys||preFail.latched)return false;
 Fake postFail;postFail.mode=22;auto post=run(postFail);
 if(post.result!=Result::PostDisturbed||!post.edge||post.conditions||postFail.keys!=1||!postFail.latched)return false;
 auto again=run(postFail);if(again.result!=Result::AlreadyAttempted||postFail.keys!=1)return false;
 Fake notGood;notGood.mode=23;auto ng=run(notGood);
 if(ng.result!=Result::SentUnconfirmed||!ng.edge||!ng.conditions||notGood.keys!=1)return false;
 for(int mode:{24,25,26}){Fake f;f.mode=mode;auto refused=run(f);
  if(refused.result!=Result::GuardRefused||!refused.attempted||refused.sent||refused.ack||f.keys||!f.latched)return false;
  if(run(f).result!=Result::AlreadyAttempted||f.keys)return false;
 }
 return true;
}
static_assert(qualification(),"120s boundary, stale/backward/gap, exact voltage/temp/current stability and all gates");
static_assert(cases(),"manual one attempt, ACK ambiguity, post disturbance, post-only edge, SOC never forced/proof");
''')

    def test_actual_closed_snapshot_all_failure_positions(self):
        self.compile(r'''
#include "mosaico_ocv_diagnostic_model.h"
using namespace mosaico_ocv;
constexpr bool cases(){
 constexpr uint8_t expected[]={0x06,0x08,0x0a,0x0c,0x10,0x12,0x14,0x2c,0x3a,0x3c};
 for(int failure=-1;failure<10;++failure){Sample s{};s.soc=99;int calls=0;bool wrong=false;
  bool ok=readSnapshot([&](uint8_t reg,uint16_t& v) constexpr {int i=calls++;if(i>=10||reg!=expected[i])wrong=true;v=0;return i!=failure;},s);
  if(wrong||calls!=10||ok!=(failure==-1)||s.soc!=(ok?0:99))return false;
 }
 return true;
}
static_assert(cases(),"closed standard snapshot, complete or no publication");
''')

    def test_actual_backend_rate_wire_guard_and_ack_failure(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        methods='    bool waitFullSnapshotSlot() {'+source.split('    bool waitFullSnapshotSlot() {',1)[1].split('\n};\n\nbool Hal::gaugeOcvMeasure(',1)[0]
        methods=methods.replace('    bool ', '    constexpr bool ').replace('    int64_t ', '    constexpr int64_t ').replace('    void ', '    constexpr void ').replace('    mosaico_ocv::SendReport ', '    constexpr mosaico_ocv::SendReport ')
        methods=methods.replace('std::lock_guard<std::mutex> battery(battery_mutex);','TestLock battery(battery_mutex);')
        methods=methods.replace('mosaico_ocv::readSnapshot(gauge_word,s)','fakeReadSnapshot(s)')
        code=r'''
#include <initializer_list>
#include <cstddef>
#include "mosaico_ocv_diagnostic_model.h"
using namespace mosaico_ocv;
constexpr int ESP_OK=0;
struct Mutex{int* held;};struct TestLock{int* held;constexpr TestLock(Mutex& m):held(m.held){++*held;}constexpr ~TestLock(){--*held;}};
struct Fake {
 int fault=0,held=0,wires=0,feeds=0,reads=0,waits=0;bool gauge=true,gauge_ocv_attempted=false,wrong=false,fullReadSeen=false;
 int64_t now=120000000,lastFullReadUs=0,previousCompletion=-1;Mutex battery_mutex{&held};
 constexpr bool runtimeGate(){return fault!=1;}
 constexpr int64_t esp_timer_get_time()const{return now;}
 constexpr void gauge_delay_ms(uint32_t ms){if(held)wrong=true;++waits;now+=int64_t(ms)*1000;}
 constexpr void gauge_feed(){++feeds;}
 constexpr bool fakeReadSnapshot(Sample& s){
  if(held!=1||(previousCompletion>=0&&now-previousCompletion<1000000))wrong=true;
  ++reads;s={2982,4194,0x5028,50,65,65,77,0xa6,0,0};now+=100000;previousCompletion=now;
  if(fault==2)s.currentMa=3; // absolute quiet but outside whole-window spread
  if(fault==3)s.batteryStatus|=0x2000;
  if(fault==4)s.fcc=100;
  return fault!=5;
 }
 constexpr int i2c_master_transmit(bool,const uint8_t* p,size_t n,int timeout){
  ++wires;if(!gauge_ocv_attempted||held!=1||n!=3||p[0]!=0||p[1]!=0x0c||p[2]!=0||timeout!=20)wrong=true;
  return fault==6?2:0;
 }
'''+methods+r'''
};
constexpr Qualification proof(){
 Qualification q;Sample s{2982,4194,0x5028,50,65,65,77,0xa6,0,0};
 for(int64_t now=0;now<=120000000;now+=1000000)if(!q.sample(s,now))q.failed=true;
 return q;
}
constexpr bool cases(){
 for(int fault=0;fault<=6;++fault){Fake f;f.fault=fault;f.fullReadSeen=true;f.lastFullReadUs=f.now;
  auto q=proof();if(!f.latchAttempt())return false;
  auto r=f.sendOcv(q);
  if(!f.gauge_ocv_attempted||f.latchAttempt()||f.wrong||f.held)return false;
  if(fault>=1&&fault<=5){if(r.result!=SendResult::GuardRefused||f.wires)return false;}
  else {if(r.result!=(fault==6?SendResult::AckFailed:SendResult::Acked)||f.wires!=1||f.feeds!=2||f.reads!=1||f.waits!=1)return false;}
 }
 Fake rates;Sample s{};
 for(int i=0;i<4;++i)if(!rates.snapshot(s))return false;
 auto q=proof(); // align proof to the recent samples for the wire-fresh gate
 q.firstUs=rates.now-120000000;q.lastUs=rates.now;
 if(!rates.latchAttempt()||rates.sendOcv(q).result!=SendResult::Acked||!rates.snapshot(s)||rates.wrong||rates.held||rates.reads!=6)return false;
 Fake backward;backward.fullReadSeen=true;backward.lastFullReadUs=backward.now+1;
 if(backward.snapshot(s)||backward.reads||backward.waits)return false;
 return true;
}
static_assert(cases(),"actual backend: no locked waits, >=1s ALL batches, wire-fresh guards/spread, latch ACK failure");
'''
        self.compile('#include <cstdint>\n'+code,extra=('-std=c++20',))

    def test_actual_hal_backend_compile_and_no_hidden_writes(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        backend='struct GaugeOcvBackend {'+source.split('struct GaugeOcvBackend {',1)[1].split('\nbool Hal::gaugeOcvMeasure(',1)[0]
        method='bool Hal::gaugeOcvMeasure('+source.split('bool Hal::gaugeOcvMeasure(',1)[1].split('\nbool Hal::gaugeChargeAudit(',1)[0]
        self.compile(r'''
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include "mosaico_ocv_diagnostic_model.h"
namespace std{struct mutex{};struct recursive_mutex{};template<class T>struct lock_guard{lock_guard(T&){}};}
std::mutex battery_mutex;std::recursive_mutex gauge_transaction_mutex;
bool gauge,gauge_ocv_attempted;bool gauge_word(uint8_t,uint16_t&);bool gauge_identity();
struct Power{bool locked,wifiRunning;int phase;};struct Network{Power powerStats();};Network& GetNetworkQuota();
struct BleStats{bool advertising,connected;};struct Ble{BleStats diagnostics();};Ble& GetCodexMicroBle();
namespace MosaicoOta{bool busy();bool healthPending();}
void gauge_feed();void gauge_delay_ms(uint32_t);int64_t esp_timer_get_time();
constexpr int ESP_OK=0;int i2c_master_transmit(bool,const uint8_t*,size_t,int);
struct Hal{bool gaugeOcvMeasure(char*,size_t);};
'''+backend+method)
        for forbidden in ('nvs_', 'AccessWord', 'gauge_control(', 'gauge_mac', 'gauge_write', 'gaugeSetNominal', 'GaugeFccBackend'):
            self.assertNotIn(forbidden,backend+method)
        self.assertIn('const uint8_t packet[]={0x00,0x0C,0x00};',backend)
        self.assertEqual(backend.count('i2c_master_transmit('),1)
        self.assertIn('gauge_ocv_attempted=true;',backend)
        self.assertIn('std::lock_guard<std::mutex> battery(battery_mutex)',backend)
        self.assertNotIn('std::lock_guard<std::mutex>',method)
        self.assertIn('return r.result==mosaico_ocv::Result::Confirmed;',method)
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
        cmd=serial.split('if (std::strcmp(command, "gauge-ocv") == 0)',1)[1].split('if (std::strcmp(command, "gauge-charge") == 0)',1)[0]
        for token in ('_reply_uart','"measure"','"CONFIRM"','::strtok_r(nullptr','GetHAL().gaugeOcvMeasure'):
            self.assertIn(token,cmd)
        self.assertNotIn('"gauge-ocv"',(ROOT/'main/debug/serial_debug_transport.h').read_text())
        self.assertEqual(source.count('Hal::gaugeOcvMeasure('),1)

if __name__=='__main__':unittest.main()
