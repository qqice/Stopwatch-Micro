"""First ColdFactory post-exit observer and queue retry tests. No hardware/IDF build."""
from pathlib import Path
import unittest
import test_mosaico_gauge_status as status_tests
from test_mosaico_system_logs_export import page
import mosaico_system_logs as logs
import struct
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()

class PostExitTests(unittest.TestCase):
    compile=status_tests.GaugeStatusTests.compile

    def test_actual_exit_observer(self):
        body=SOURCE.split('    bool exitCfg() {',1)[1].split('    bool reseal()',1)[0]
        self.compile(r'''
#include "mosaico_gauge_status_model.h"
#include "mosaico_gauge_engine_model.h"
#include "mosaico_fcc_limit_repair_model.h"
using Standard=mosaico_gauge_status::Snapshot;
using Engine=mosaico_gauge_engine::Snapshot;
struct Fixture {
 Standard gauge_boot_post_exit{};Engine gauge_engine_post_exit{};
 bool isCold=true,securityOk=true,cfg=true,initialized=true,cal=false,exitOk=true,ioOk=true;
 unsigned stage=0,exitCalls=0,standardCalls=0,engineCalls=0;bool wrong=false;
 constexpr bool observedSecurity(mosaico_fcc_limit::Security& s){s={1,cfg,cal,initialized};return securityOk;}
 constexpr bool cold()const{return isCold;}
 constexpr bool gauge_exit_config(){++exitCalls;stage=1;return exitOk;}
 constexpr void gauge_diagnostic_locked(Standard& s,uint8_t p,uint8_t o){
  if(stage!=1){wrong=true;}stage=2;++standardCalls;s={};s.phase=p;s.outcome=o;s.sampledUs=2000123;s.validMask=ioOk?1:0;
 }
 constexpr void gauge_engine_locked(Engine& s,uint8_t p,uint8_t o){
  if(stage!=2){wrong=true;}stage=3;++engineCalls;s={};s.phase=p;s.outcome=o;s.sampledUs=2020456;s.validMask=ioOk?1:0;
 }
 constexpr bool exitCfg() {'''+body+r'''
};
constexpr bool cases(){
 static_assert(sizeof(Standard)==48);static_assert(sizeof(Engine)==72);
 for(unsigned fault=0;fault<7;++fault){
  Fixture f;
  if(fault==1)f.isCold=false;
  if(fault==2)f.securityOk=false;
  if(fault==3)f.cfg=false;
  if(fault==4)f.exitOk=false;
  if(fault==5)f.ioOk=false;
  if(fault==6){f.cfg=false;f.initialized=false;}
  const bool ok=f.exitCfg();
  const bool capture=fault==0||fault==5;
  if(ok!=(fault!=2&&fault!=4&&fault!=6)||f.wrong)return false;
  if(f.standardCalls!=unsigned(capture)||f.engineCalls!=unsigned(capture))return false;
  if(f.exitCalls!=unsigned(fault!=2&&fault!=3&&fault!=6))return false;
  if(capture){
   if(f.gauge_boot_post_exit.phase!=4||f.gauge_engine_post_exit.phase!=4||
      f.gauge_boot_post_exit.sampledUs!=2000123||f.gauge_engine_post_exit.sampledUs!=2020456||
      f.gauge_boot_post_exit.validMask!=unsigned(f.ioOk)||f.gauge_engine_post_exit.validMask!=unsigned(f.ioOk))return false;
   if(!f.exitCfg()||f.standardCalls!=1||f.engineCalls!=1)return false;
  }
 }
 return true;
}
static_assert(cases());
''')

    def test_production_queue_retry(self):
        model=(ROOT/'main/host/mosaico_system_log_model.h').as_posix()
        self.compile('#include "'+model+'"\n'+r'''
#include "mosaico_gauge_status_model.h"
#include "mosaico_gauge_engine_model.h"
using namespace MosaicoSystemLog::Model;
constexpr bool cases(){
 for(uint8_t result=1;result<=3;++result){
  for(unsigned valid=0;valid<2;++valid){
   PostExitCapture capture;Queue q;mosaico_gauge_status::Snapshot s{};mosaico_gauge_engine::Snapshot e{};
   s.phase=e.phase=4;s.sampledUs=2000123;e.sampledUs=2020456;s.validMask=e.validMask=valid;
   unsigned accepted=0;
   auto emit=[&](const auto& x){Record r;r.type=accepted==0?Type::GaugeBoot:Type::GaugeEngine;r.monotonicMs=x.sampledUs/1000;r.args[0]=x.validMask;r.args[1]=x.phase|(uint32_t(x.outcome)<<8);if(!q.push(r))return false;++accepted;return true;};
   if(capture.update(s,e,0,emit,emit)||q.count||capture.complete)return false;
   Record filler;for(unsigned i=0;i<Capacity;++i)q.push(filler);
   if(capture.update(s,e,result,emit,emit)||capture.standardSent||accepted)return false;
   q.consume(1);
   if(capture.update(s,e,result,emit,emit)||!capture.standardSent||capture.engineSent||accepted!=1)return false;
   q.consume(1);
   if(!capture.update(s,e,result,emit,emit)||!capture.engineSent||accepted!=2)return false;
   if(!capture.update(s,e,result,emit,emit)||accepted!=2)return false;
   const auto a=decode(q.data[(q.head+126)%Capacity]),b=decode(q.data[(q.head+127)%Capacity]);
   if(a.monotonicMs!=2000||b.monotonicMs!=2020||a.epoch||b.epoch||a.flags||b.flags||
      a.args[0]!=valid||b.args[0]!=valid||a.args[1]!=(4U|(uint32_t(result)<<8))||b.args[1]!=a.args[1])return false;
   if(s.outcome||e.outcome||s.sampledUs!=2000123||e.sampledUs!=2020456)return false;
  }
 }
 PostExitCapture absent;mosaico_gauge_status::Snapshot s{};mosaico_gauge_engine::Snapshot e{};
 auto forbidden=[](const auto&){return false;};
 if(absent.update(s,e,0,forbidden,forbidden)||absent.complete)return false;
 if(!absent.update(s,e,1,forbidden,forbidden)||!absent.complete||absent.standardSent||absent.engineSent)return false;
 return true;
}
static_assert(cases());
''')

    def test_source_boundaries(self):
        getter=SOURCE.split('bool Hal::gaugeBootPostExit(',1)[1].split('bool Hal::gaugeModeAudit(',1)[0]
        self.assertIn('transaction(gauge_transaction_mutex)',getter)
        for forbidden in ('gauge_diagnostic_locked(', 'gauge_engine_locked(', 'battery(battery_mutex)', 'nvs_', 'gauge_control('):self.assertNotIn(forbidden,getter)
        backend=SOURCE.split('    bool exitCfg() {',1)[1].split('    bool reseal()',1)[0]
        self.assertEqual(backend.count('gauge_exit_config()'),1)
        for forbidden in ('gauge_delay_ms(', 'gauge_control(', 'gauge_access_word(', 'lock_guard', 'nvs_', 'writeParameter('):self.assertNotIn(forbidden,backend)
        exit_code=SOURCE.split('bool gauge_exit_config()',1)[1].split('bool gauge_write_field(',1)[0]
        self.assertIn('gauge_control(GaugeCommand::ExitReinit)',exit_code)
        self.assertIn('gauge_wait_config(false)',exit_code)
        wait=SOURCE.split('bool gauge_wait_config(',1)[1].split('bool gauge_exit_config()',1)[0]
        self.assertIn('gauge_delay_ms(2000)',wait)
        coordinator=(ROOT/'main/hal/mosaico/mosaico_fcc_limit_repair_model.h').read_text()
        self.assertIn('b.exitCfg()',coordinator)
        cleanup=coordinator.split('constexpr bool cleanup()',1)[1].split('constexpr Result fail()',1)[0]
        self.assertLess(cleanup.index('b.exitCfg()'),cleanup.index('b.reseal()'))
        logger=(ROOT/'main/host/mosaico_system_log.cpp').read_text()
        collector=logger.split('void collect(uint64_t now){',1)[1].split('const auto p=',1)[0]
        post=collector.split('if(!postExitCapture.complete)',1)[1].split('\n if(!bootCapture',1)[0]
        self.assertLess(post.index('gaugeBootReloadInfo()'),post.index('gaugeBootPostExit('))
        self.assertIn('postExitCapture.update(',post)
        self.assertNotIn('gaugeDiagnosticSample(',post)
        self.assertNotIn('gaugeEngineSample(',post)
        self.assertIn('r.epoch=0;r.flags&=~uint16_t(1)',logger)

    def test_actual_type13_phase4_pack(self):
        logger=(ROOT/'main/host/mosaico_system_log.cpp').read_text()
        pack='constexpr bool engine('+logger.split('bool engine(',1)[1].split('void changed(',1)[0]
        model=(ROOT/'main/host/mosaico_system_log_model.h').as_posix()
        self.compile('#include "'+model+'"\n'+r'''
#include "mosaico_gauge_engine_model.h"
#include "mosaico_gauge_status_model.h"
struct Hal{using GaugeEngineSnapshot=mosaico_gauge_engine::Snapshot;};
using namespace MosaicoSystemLog::Model;
struct Harness {
 Record captured{};bool critical=false;
 constexpr Record record(Type type,uint64_t ms){Record r;r.type=type;r.monotonicMs=ms;r.epoch=123;r.flags=1;return r;}
 constexpr bool enqueue(const Record& r,bool urgent){captured=r;critical=urgent;return true;}
'''+pack+r'''
};
constexpr bool cases(){
 Harness h;Hal::GaugeEngineSnapshot s{};s.phase=4;s.outcome=2;s.sampledUs=2020456;s.validMask=0;
 for(size_t i=0;i<23;++i)s.rawCedv[i]=uint8_t(i+1);
 for(size_t i=0;i<17;++i)s.rawSmooth[i]=uint8_t(i+24);
 if(!h.engine(s))return false;
 const auto b=encode(h.captured);
 if(b.size()!=64||h.captured.type!=Type::GaugeEngine||h.captured.monotonicMs!=2020||h.captured.epoch||h.captured.flags||!h.critical)return false;
 if(h.captured.args[0]!=((4U<<16)|(2U<<24)))return false;
 for(size_t i=0;i<40;++i)if(b[24+i]!=i+1)return false;
 return true;
}
static_assert(cases());
''')

    def test_old_and_phase4_decoder(self):
        for phase in (1,2,3,4):
            data=bytearray(page(1))
            struct.pack_into('<HHQQ11I',data,60,3,0,2000,0,0,phase|(2<<8),94,61,65,65,4100,0,0,0,0)
            struct.pack_into('<I',data,2044,logs.crc(data[:2044]))
            row=logs.decode_page(data,1)[0]
            self.assertEqual(row['gauge']['phase'],phase)
            self.assertEqual(row['monotonic_ms'],2000)
            self.assertEqual(row['epoch'],0)
            struct.pack_into('<HHQQ11I',data,60,13,0,2020,0,(phase<<16)|(2<<24),*range(10))
            struct.pack_into('<I',data,2044,logs.crc(data[:2044]))
            row=logs.decode_page(data,1)[0]
            self.assertEqual(row['gauge_engine']['phase'],phase)
            self.assertEqual(row['gauge_engine']['outcome'],2)
            self.assertEqual(row['monotonic_ms'],2020)

if __name__=='__main__':unittest.main()
