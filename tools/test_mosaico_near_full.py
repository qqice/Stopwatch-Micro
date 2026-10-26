"""Offline NearFull codec and extracted production fault tests; no device/private data.

Run with ESP-IDF Python. C++ static_assert fixtures are syntax-compiled only;
no firmware image is built and no target executable is run.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
COMPILERS=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
COMPILER=str(COMPILERS[-1]) if COMPILERS else shutil.which('g++') or shutil.which('clang++')

def compile_fixture(test, code, name, extra=()):
    if not COMPILER:
        test.skipTest('C++ compiler unavailable')
    with tempfile.TemporaryDirectory(prefix='near-full-') as directory:
        path=Path(directory)/name
        path.write_text(code, encoding='utf-8')
        result=subprocess.run([COMPILER,'-std=c++20','-Wall','-Werror','-fsyntax-only',*extra,'-I',str(ROOT),str(path)],capture_output=True,text=True)
        test.assertEqual(result.returncode,0,result.stdout+result.stderr)
def body(name):
    begin=SOURCE.index(name); start=SOURCE.index('{',begin); depth=1; end=start+1
    while depth:
        depth += (SOURCE[end]=='{')-(SOURCE[end]=='}'); end+=1
    return SOURCE[start:end]
class NearTests(unittest.TestCase):
 def test_actual_codec_and_cold_faults(self):
  code=r'''
#include "main/hal/mosaico/mosaico_near_full.h"
using namespace mosaico_near;
constexpr uint8_t mac[]={1,2,3,4,5,6};
constexpr bool codec(){
 for(unsigned v=0;v<65536;++v)if(allowed(v)!=(v==5||v==200))return false;
 auto r=make(mac,5,Pending);if(!valid(r,mac)||sizeof(r)!=24)return false;
 for(unsigned i=0;i<24;++i)for(unsigned b=0;b<8;++b){auto changed=r;changed[i]^=1<<b;if(valid(changed,mac))return false;}
 uint8_t other[]={1,2,3,4,5,7};if(valid(r,other))return false;
 auto bad=r;bad[10]=5;seal(bad);if(valid(bad,mac))return false;
 for(auto phase:{Pending,Active,Restored,Failed})if(!valid(make(mac,200,phase),mac))return false;
 Snapshot before{},after{};after[index]=5;if(!unchanged(before,after))return false;
 for(unsigned i=0;i<count;++i)if(i!=index){after[i]=1;if(unchanged(before,after))return false;after[i]=0;}
 for(unsigned i=0;i<count;++i)if(field(i).width<1||field(i).width>4)return false;
 return field(index).address==address&&field(index).width==2;
}
static_assert(codec());
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1;
constexpr const char* Tag="test";
template<class... T> constexpr void log(T... args){((void)args,...);}
#define ESP_LOGW(...) log(__VA_ARGS__)
struct Sim {
 Record durable=make(mac,5,Active);int loaded=0,reads=0,writes=0,saves=0;
 uint16_t observed=200,lastWrite=0,near_boot_value=0;bool near_boot_known=false,near_boot_pending=false;
 int badRead=0,badWrite=0,badSave=0;
 constexpr int near_load(Record& out){out=durable;return loaded;}
 constexpr bool near_save(Record& r){++saves;if(saves==badSave)return false;seal(r);durable=r;return true;}
 constexpr bool near_read(uint16_t& out){++reads;out=observed;return reads!=badRead;}
 constexpr bool near_write(uint16_t value){++writes;lastWrite=value;if(writes==badWrite)return false;observed=value;return true;}
 constexpr bool gauge_identity(){return true;}
 constexpr bool gauge_word(int,uint16_t& op){op=0x422;return true;}
 constexpr bool near_rollbackOwn(uint16_t before,uint16_t desired) ROLLBODY
 constexpr void finish(bool safelySealedNative65) FINISHBODY
 constexpr void run() BODY
};
constexpr bool faults(){
 for(auto target:{200,5}){Sim s;s.durable=make(mac,target,Active);s.run();if(s.writes!=1||s.observed!=target||s.durable[3]!=Pending)return false;s.finish(true);if(!s.near_boot_known||s.near_boot_value!=target||s.durable[3]!=Active)return false;}
 for(int phase:{Pending,Restored,Failed}){Sim s;s.durable[3]=phase;s.run();if(s.reads||s.writes||s.saves)return false;}
 for(int absent:{ESP_ERR_NVS_NOT_FOUND,2}){Sim s;s.loaded=absent;s.run();if(s.reads||s.writes||s.saves)return false;}
 for(int fault:{1,2}){Sim s;s.badRead=fault;s.run();s.finish(true);if(s.writes||s.durable[3]!=Failed)return false;s.run();if(s.writes)return false;}
 {Sim s;s.observed=99;s.run();s.finish(true);if(s.writes||s.durable[3]!=Failed)return false;}
 {Sim s;s.badWrite=1;s.run();s.finish(true);if(s.writes!=1||s.observed!=200||s.durable[3]!=Failed||s.near_boot_known)return false;s.run();if(s.writes!=1)return false;}
 {Sim s;s.badSave=1;s.run();if(s.writes||s.reads)return false;}
 {Sim s;s.badSave=2;s.run();s.finish(true);if(s.durable[3]!=Pending||s.writes!=1)return false;s.run();if(s.writes!=1)return false;}
 {Sim s;s.run();s.finish(false);if(s.durable[3]!=Failed||s.near_boot_known)return false;}
 return true;
}
static_assert(faults());
'''.replace('ROLLBODY',body('bool near_rollbackOwn(')).replace('FINISHBODY',body('void near_boot_finish(')).replace('BODY',body('void near_boot_apply()'))
  compile_fixture(self,code,'near_full_fixture.cpp',('-fconstexpr-ops-limit=100000000',))
 def test_actual_writer_wire_and_faults(self):
  code=r'''
#include "main/hal/mosaico/mosaico_near_full.h"
constexpr int ESP_OK=0;
namespace mosaico_gauge {constexpr uint8_t macChecksum(const uint8_t* p,unsigned n){unsigned s=0;for(unsigned i=0;i<n;++i)s+=p[i];return 255-(s&255);}}
struct Sim {
 int gauge=1,tx=0,reads=0,failTx=0,failRead=0;uint16_t operation=0x422,observed=5;bool identity=true,wire=true;
 constexpr bool gauge_identity(){return identity;}
 constexpr bool gauge_word(int,uint16_t& v){v=operation;return true;}
 constexpr void gauge_feed(){}
 constexpr void gauge_delay_ms(int){}
 constexpr bool near_read(uint16_t& v){++reads;v=observed;return reads!=failRead;}
 constexpr int i2c_master_transmit(int,const uint8_t* p,unsigned n,int){
  ++tx;if(tx==1){wire&=n==5&&p[0]==0x3e&&p[1]==0x6b&&p[2]==0x92;observed=(uint16_t(p[3])<<8)|p[4];}
  else {const uint8_t bytes[]={0x6b,0x92,uint8_t(observed>>8),uint8_t(observed)};wire&=n==3&&p[0]==0x60&&p[1]==mosaico_gauge::macChecksum(bytes,4)&&p[2]==6;}
  return tx==failTx?1:0;
 }
 constexpr bool write(uint16_t value) BODY
};
constexpr bool faults(){
 for(unsigned v:{200,5}){Sim s;if(!s.write(v)||!s.wire||s.tx!=2||s.reads!=2||s.observed!=v)return false;}
 for(unsigned v:{0,1,6,199,201,65535}){Sim s;if(s.write(v)||s.tx)return false;}
 for(unsigned op:{0,0x22,0x426,0x423}){Sim s;s.operation=op;if(s.write(5)||s.tx)return false;}
 {Sim s;s.identity=false;if(s.write(5)||s.tx)return false;}
 for(int tx:{1,2}){Sim s;s.failTx=tx;if(s.write(5)||s.tx!=tx||s.reads)return false;}
 for(int read:{1,2}){Sim s;s.failRead=read;if(s.write(5)||s.tx!=2||s.reads!=read)return false;}
 return true;
}
static_assert(faults());
'''.replace('BODY',body('bool near_write('))
  compile_fixture(self,code,'writer_fixture.cpp')
 def test_physical_mac_domain_only(self):
  physical=body('bool near_physical_mac(')
  self.assertIn('esp_read_mac(mac,ESP_MAC_EFUSE_FACTORY)==ESP_OK',physical)
  for name in ('esp_err_t near_load(', 'bool near_save(', 'bool Hal::gaugeNearFull('):
   b=body(name);self.assertIn('near_physical_mac(',b);self.assertNotIn('getFactoryMac(',b)
  self.assertIn('mac_binding=physical_efuse',body('bool Hal::gaugeNearFull('))
  self.assertIn('boot_capture_stage=cfg_before_exit',body('bool Hal::gaugeNearFull('))
 def test_closed_writer_and_snapshot(self):
  w=body('bool near_write(')
  self.assertIn('!mosaico_near::allowed(value)',w);self.assertIn('0x3e,0x6b,0x92',w)
  self.assertEqual(w.count('i2c_master_transmit'),2);self.assertIn('i<2',w)
  for wrong in ('0x0092','0x929f','0x929d','Reset','SOC'):self.assertNotIn(wrong,w)
  snapshot=body('bool near_snapshot(')
  for read in ('mosaico_threshold::readStable','mosaico_calibration::readStable','mosaico_public_extra::readStable'):self.assertIn(read,snapshot)
  setter=body('bool Hal::gaugeNearFull(')
  self.assertLess(setter.index('near_save(r)'),setter.index('GaugeCommand::EnterConfig'))
  self.assertIn('unchanged(near_snapshots[0],near_snapshots[1])',setter)
  self.assertLess(setter.index('near_after_known=entered&&near_snapshot'),setter.index('gauge_exit_config()'))
  self.assertLess(setter.index('native65=gauge_word'),setter.index('r[3]=ok?(restore?Restored:Active)'))
  self.assertIn('cfg_after_write',body('bool Hal::gaugeNearFullField('))
  self.assertIn('GaugeAccessAction::Restore',setter)
  self.assertEqual(setter.count('near_rollbackOwn(before,target)'),1)
  self.assertEqual(setter.count('near_write(target)'),1)
 def test_noninterference_and_protocol(self):
  boot=body('Hal::GaugeBootReloadStatus Hal::gaugeBootReload(')
  self.assertNotIn('near_boot_finish(',boot)
  self.assertNotIn('near_boot_apply(',boot)
  self.assertEqual(boot.count('gauge_official_apply_locked(capacity,details,sizeof(details),capacity==130)'),1)
  nominal=body('bool Hal::gaugeSetNominalCapacity(')
  self.assertEqual(nominal.count('near_boot_apply()'),1)
  self.assertIn('if (forward && boot_factory_reload_scope && !restore) near_boot_apply();',nominal)
  self.assertLess(nominal.index('near_boot_apply()'),nominal.index('bool exited = gauge_exit_config();',nominal.index('near_boot_apply()')))
  serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
  command=serial[serial.index('if (std::strcmp(command, "gauge-near-full")'):serial.index('if (std::strcmp(command, "gauge-selftest")')]
  self.assertIn('"CONFIRM"',command);self.assertNotIn('_reply_uart',command)
  self.assertIn('gauge-near-full',(ROOT/'main/debug/serial_debug_transport.h').read_text())
class SetterTests(unittest.TestCase):
 def test_actual_setter_autoseal_rollback_and_commit(self):
  code=r'''
#include "main/hal/mosaico/mosaico_near_full.h"
#include <cstring>
using namespace mosaico_near;
constexpr uint8_t mac[]={1,2,3,4,5,6};
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,MALLOC_CAP_SPIRAM=1,MALLOC_CAP_8BIT=2;
constexpr const char* Tag="test";constexpr int ESP_MAC_EFUSE_FACTORY=0x56;
template<class... T> constexpr void log(T... args){((void)args,...);}
#define ESP_LOGI(...) log(__VA_ARGS__)
namespace mock {
constexpr int strcmp(const char* a,const char* b){while(*a&&*a==*b){++a;++b;}return *a-*b;}
template<class... T> constexpr int snprintf(char*,size_t,const char*,T...){return 0;}
}
namespace mosaico_gauge {constexpr bool configExitAccepted(uint16_t op){return !(op&0x401);}}
struct NormalGaugeStorageLease {bool held=true;};
enum class GaugeCommand {EnterConfig};
enum class GaugeAccessAction {Open,Restore};
struct GaugeThresholdBackend {bool normalTarget65=false;constexpr bool preflight(uint8_t*){return true;}constexpr bool runtimeGate(){return true;}};
struct Sim {
 Record durable=make(mac,200,Active);Snapshot snapshots[2]{};Snapshot* near_snapshots=snapshots;
 bool near_before_known=false,near_after_known=false,near_boot_known=false;
 uint16_t near_boot_value=0,actual=200;int writes=0,reads=0,opens=0,saves=0;
 bool fa=false,cfg=false,sealed=true,badSeal=false,otherDelta=false,badAfter=false,dmAfterExit=false,earlyActive=false;
 bool absent=false,failPhysical=false,wrongMacEnum=false;int macReads=0;
 int ackFault=0,failRead=0,failSave=0;bool gauge_access_cleanup_attempted=false,gauge_access_cleanup_succeeded=false;
 constexpr auto getFactoryMac(){return std::array<uint8_t,6>{1,2,3,4,5,6};}
 constexpr int near_load(Record& out){out=durable;return absent?ESP_ERR_NVS_NOT_FOUND:ESP_OK;}
 constexpr int esp_read_mac(uint8_t* out,int type){++macReads;wrongMacEnum|=type!=ESP_MAC_EFUSE_FACTORY;if(failPhysical)return 9;for(unsigned i=0;i<6;++i)out[i]=mac[i];return ESP_OK;}
 constexpr bool near_physical_mac(uint8_t (&mac)[6]) MACBODY
 constexpr bool near_save(Record& r){++saves;if(saves==failSave)return false;if((r[3]==Active||r[3]==Restored)&&!sealed)earlyActive=true;seal(r);durable=r;absent=false;return true;}
 constexpr bool normal_gauge_fence_locked(){return true;}
 constexpr void* heap_caps_calloc(int,size_t,int){return nullptr;}
 constexpr bool near_snapshot(Snapshot& out){
  if(!fa){dmAfterExit=true;return false;}
  out={};out[index]=actual;if(cfg&&otherDelta)out[10]=999;
  return !(cfg&&badAfter);
 }
 constexpr bool gaugeAccess(GaugeAccessAction action,char*,size_t){
  if(action==GaugeAccessAction::Open){++opens;fa=true;sealed=false;return true;}
  if(badSeal){sealed=false;return false;}sealed=true;fa=cfg=false;return true;
 }
 constexpr bool gauge_identity(){return true;}
 constexpr bool gauge_word(int reg,uint16_t& out){out=reg==0x3a?(sealed?0x26:cfg?0x422:0x22):65;return true;}
 constexpr bool gauge_quiet_full(uint16_t){return true;}
 constexpr bool gauge_control(GaugeCommand){cfg=true;return true;}
 constexpr bool gauge_wait_config(bool){return cfg;}
 constexpr bool near_write(uint16_t value){++writes;if(writes==1&&ackFault){if(ackFault==1)actual=value;if(ackFault==3)actual=77;return false;}actual=value;return true;}
 constexpr bool near_read(uint16_t& out){++reads;out=actual;return reads!=failRead;}
 constexpr bool near_rollbackOwn(uint16_t before,uint16_t desired) ROLLBODY
 constexpr bool gauge_exit_config(){cfg=fa=false;sealed=true;return true;} // real 0091 autoSEALED
 constexpr bool run(const char* action,uint16_t expected,uint16_t target,char* details,size_t size) SETBODY
};
constexpr bool faults(){
 char out[400]{};
 {Sim s;if(!s.run("set",200,5,out,sizeof(out))||s.writes!=1||s.opens!=1||s.durable[3]!=Active||s.dmAfterExit||s.earlyActive||s.snapshots[1][index]!=5)return false;}
 {Sim s;s.badSeal=true;if(s.run("set",200,5,out,sizeof(out))||s.durable[3]!=Failed||s.earlyActive)return false;}
 {Sim s;s.failSave=2;if(s.run("set",200,5,out,sizeof(out))||s.durable[3]!=Pending||s.earlyActive)return false;}
 for(int fault:{1,2,3}){Sim s;s.ackFault=fault;if(s.run("set",200,5,out,sizeof(out))||s.durable[3]!=Failed||s.writes!=(fault==1?2:1)||s.dmAfterExit)return false;}
 {Sim s;s.ackFault=1;s.failRead=1;if(s.run("set",200,5,out,sizeof(out))||s.writes!=1||s.actual!=5||s.durable[3]!=Failed)return false;}
 {Sim s;s.ackFault=2;if(s.run("set",200,200,out,sizeof(out))||s.writes!=1||s.actual!=200)return false;}
 {Sim s;s.otherDelta=true;if(s.run("set",200,5,out,sizeof(out))||s.writes!=2||s.actual!=200||s.snapshots[1][10]!=999||s.snapshots[1][index]!=5||s.durable[3]!=Failed)return false;}
 {Sim s;s.badAfter=true;if(s.run("set",200,5,out,sizeof(out))||s.writes!=2||s.actual!=200)return false;}
 {Sim s;s.actual=5;s.durable=make(mac,5,Active);if(!s.run("restore",0,0,out,sizeof(out))||s.durable[3]!=Restored||s.actual!=200||s.earlyActive)return false;}
 return true;
}
static_assert(faults());
constexpr bool macBinding(){char out[400]{};
 {Sim s;s.absent=true;if(!s.run("set",200,5,out,sizeof(out))||!valid(s.durable,mac)||s.wrongMacEnum||s.macReads!=1||s.writes!=1)return false;}
 {Sim s;s.absent=true;s.failPhysical=true;if(s.run("set",200,5,out,sizeof(out))||s.saves||s.writes||s.opens||!s.absent||s.wrongMacEnum)return false;}
 return true;
}
static_assert(macBinding());
'''
  setter=body('bool Hal::gaugeNearFull(')
  setter=re.sub(r'std::lock_guard<[^>]+>\s+\w+\([^;]+\);','',setter)
  setter=setter.replace('std::strcmp','mock::strcmp').replace('std::snprintf','mock::snprintf')
  code=code.replace('MACBODY',body('bool near_physical_mac(')).replace('ROLLBODY',body('bool near_rollbackOwn(')).replace('SETBODY',setter)
  compile_fixture(self,code,'setter_fixture.cpp')

if __name__=='__main__':unittest.main(verbosity=2)
