"""GSJ3 portable storage tests, with actual SDK-compiled NVS adapter mock harness.

Private full131 fixture is SHA-verified and never committed. Portable fault tests
are evaluated by SDK static_assert. The NVS harness links the real adapter and
executes it on the existing private RV32 Unicorn package, mocking only NVS/libc.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_mosaico_factory_scale_journal as codec_tests
from test_mosaico_factory_scale_journal import COMPILER, ROOT

OUT=ROOT/'.artifacts/private/mosaico/factory-scale-storage-tests'
MODEL=r'''
#include "mosaico_factory_scale_storage_model.h"
struct Memory {
 std::array<j::Blob,2> slots{}; std::array<bool,2> present{};
 bool io=false,writeOk=true,commitOk=true,torn=false,readbackError=false;
 unsigned reads=0,writes=0,commits=0;
 constexpr j::ReadResult read(unsigned i,j::Blob& b){++reads;
  if(io||(readbackError&&commits))return j::ReadResult::Error;
  if(!present[i])return j::ReadResult::Missing;b=slots[i];return j::ReadResult::Present;}
 constexpr bool write(unsigned i,const j::Blob& b){++writes;if(!writeOk)return false;
  slots[i]=b;present[i]=true;if(torn)slots[i][100]^=1;return true;}
 constexpr bool commit(){++commits;return commitOk;}
};
constexpr j::WriteAdmission admitted{true,true,true};
constexpr bool closed(const j::StorageWorkspace& w){return w.loaded.status!=j::Status::Ready&&w.loaded.selected==-1;}
'''

MOCK=r'''
#pragma once
#include <stddef.h>
#include <stdint.h>
using esp_err_t=int;using nvs_handle_t=unsigned;
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
struct nvs_stats_t {size_t used_entries,free_entries,available_entries,total_entries,namespace_count;};
esp_err_t nvs_open(const char*,int,nvs_handle_t*);
void nvs_close(nvs_handle_t);
esp_err_t nvs_get_blob(nvs_handle_t,const char*,void*,size_t*);
esp_err_t nvs_set_blob(nvs_handle_t,const char*,const void*,size_t);
esp_err_t nvs_commit(nvs_handle_t);
esp_err_t nvs_get_stats(const char*,nvs_stats_t*);
using nvs_iterator_t=void*;enum nvs_type_t {NVS_TYPE_ANY,NVS_TYPE_BLOB};
struct nvs_entry_info_t {char namespace_name[16],key[16];nvs_type_t type;};
esp_err_t nvs_get_used_entry_count(nvs_handle_t,size_t*);
esp_err_t nvs_entry_find_in_handle(nvs_handle_t,nvs_type_t,nvs_iterator_t*);
esp_err_t nvs_entry_info(nvs_iterator_t,nvs_entry_info_t*);
esp_err_t nvs_entry_next(nvs_iterator_t*);
void nvs_release_iterator(nvs_iterator_t);

'''
HARNESS=r'''
#include "mosaico_factory_scale_journal.h"
#include "nvs.h"
#include <cstring>
#include <cassert>
struct MockNvs {
 std::array<j::Blob,2> slots{}; std::array<bool,2> present{};
 int opened=0,closed=0,mode=-1,sets=0,commits=0,gets=0;
 bool exists=false,openError=false,statsError=false,probeError=false,dataError=false;
 bool setError=false,commitError=false,torn=false,readbackError=false;
 size_t free=1000,available=74,probeSize=512,dataSize=512;
 bool consumeEntries=false;
} nv;
esp_err_t nvs_open(const char* name,int mode,nvs_handle_t* out){
 assert(std::strcmp(name,"gscale_f3_v1")==0);++nv.opened;nv.mode=mode;
 if(nv.openError)return 2;if(!nv.exists&&mode==NVS_READONLY)return ESP_ERR_NVS_NOT_FOUND;
 nv.exists=true;*out=1;return ESP_OK;}
void nvs_close(nvs_handle_t handle){assert(handle==1);++nv.closed;}
int index(const char* key){assert(std::strcmp(key,"recover0")==0||std::strcmp(key,"recover1")==0);return key[7]-'0';}
esp_err_t nvs_get_stats(const char* partition,nvs_stats_t* stats){assert(partition==nullptr);stats->free_entries=nv.free;stats->available_entries=nv.available;return nv.statsError?2:ESP_OK;}
esp_err_t nvs_get_blob(nvs_handle_t,const char* key,void* out,size_t* size){
 ++nv.gets;auto i=index(key);if(nv.probeError||(!out&&nv.readbackError&&nv.commits))return 2;
 if(!nv.present[i])return ESP_ERR_NVS_NOT_FOUND;
 if(!out){*size=nv.probeSize;return ESP_OK;}if(nv.dataError)return 2;
 assert(*size==512);std::memcpy(out,nv.slots[i].data(),512);*size=nv.dataSize;return ESP_OK;}
esp_err_t nvs_set_blob(nvs_handle_t,const char* key,const void* b,size_t size){
 ++nv.sets;assert(nv.mode==NVS_READWRITE&&size==512);if(nv.setError)return 2;
 auto i=index(key);std::memcpy(nv.slots[i].data(),b,512);if(nv.consumeEntries&&!nv.present[i])nv.available-=i?18:19;nv.present[i]=true;if(nv.torn)nv.slots[i][100]^=1;return ESP_OK;}
esp_err_t nvs_commit(nvs_handle_t){++nv.commits;return nv.commitError?2:ESP_OK;}
int main(){j::StorageWorkspace w{};auto a=sample();const auto immutable=a;
 assert(j::loadNvs(a.mac,w)==j::Status::Missing&&!nv.exists&&nv.mode==NVS_READONLY);
 for(unsigned bits=0;bits<7;++bits){nv={};j::WriteAdmission adm{bool(bits&1),bool(bits&2),bool(bits&4)};
 assert(j::saveNvs(a,adm,w)==j::Status::Rejected&&nv.opened==0&&nv.sets==0&&closed(w));}
 for(unsigned fault=0;fault<13;++fault){nv={};
 switch(fault){case 0:nv.available=73;break;case 1:nv.statsError=true;break;case 2:nv.openError=true;break;
 case 3:nv.probeError=true;break;case 4:nv.setError=true;break;case 5:nv.commitError=true;break;
 case 6:nv.torn=true;break;case 7:nv.readbackError=true;break;
 default:nv.exists=true;nv.present[0]=true;assert(j::encode(a,nv.slots[0],w.codec));
 if(fault==8)nv.probeSize=511;if(fault==9)nv.probeSize=513;if(fault==10)nv.dataSize=511;
 if(fault==11)nv.dataSize=513;if(fault==12)nv.dataError=true;}
 auto b=fault>=8?next(a,r::Phase::Pending):a;
 assert(j::saveNvs(b,admitted,w)!=j::Status::Ready&&closed(w));
 if(fault<3)assert(nv.sets==0);assert(nv.closed==(nv.opened&&!nv.openError?1:0));}
 // SDK6.1 free includes reserved GC space: high free cannot substitute available.
 nv={};nv.available=73;assert(nv.free==1000);
 assert(j::saveNvs(a,admitted,w)==j::Status::Rejected&&nv.opened==0&&closed(w));
 nv={};nv.available=74;
 assert(j::saveNvs(a,admitted,w)==j::Status::Ready&&w.loaded.selected==0);
 // Simulate initial peak consumption; overwrites do not need initial headroom.
 nv={};nv.available=95;nv.consumeEntries=true;
 assert(j::saveNvs(a,admitted,w)==j::Status::Ready&&nv.available==76);
 auto state=next(a,r::Phase::Pending);
 assert(j::saveNvs(state,admitted,w)==j::Status::Ready&&nv.available==58);
 const r::Phase phases[]={r::Phase::Applied,r::Phase::Restoring,r::Phase::Original};
 for(unsigned i=0;i<3;++i){
  state=next(state,phases[i]);
  assert(j::saveNvs(state,admitted,w)==j::Status::Ready&&nv.available==58);
 }
 nv.slots[0][100]^=1;const auto corrupted=nv.slots;const auto writes=nv.sets;
 state=next(state,r::Phase::Pending);
 assert(j::saveNvs(state,admitted,w)==j::Status::Corrupt&&closed(w)&&nv.sets==writes&&nv.slots==corrupted);
 nv={};assert(j::saveNvs(a,admitted,w)==j::Status::Ready&&w.loaded.selected==0);
 assert(j::sameRecoveryImage(a,immutable)&&a.snapshot.generation==immutable.snapshot.generation);
 auto b=next(a,r::Phase::Pending);assert(j::saveNvs(b,admitted,w)==j::Status::Ready&&w.loaded.selected==1);
 assert(j::loadNvs(a.mac,w)==j::Status::Ready&&w.loaded.records[1].snapshot.generation==2);
 nv.slots[0][100]^=1;const auto saved=nv.slots;const int sets=nv.sets;
 auto c=next(b,r::Phase::Applied);assert(j::saveNvs(c,admitted,w)==j::Status::Corrupt&&closed(w));
 assert(nv.slots==saved&&nv.sets==sets);return 0;}
'''

class StorageTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  codec_tests.FactoryScaleJournalTests.setUpClass()
  cls.fixture=codec_tests.FactoryScaleJournalTests.fixture
 def compile(self,code,adapter=False):
  OUT.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.store-',dir=OUT) as td:
   td=Path(td);src=td/'test.cpp';src.write_text(self.fixture+MODEL+code)
   args=[str(COMPILER),'-std=c++20','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
      '-fconstexpr-ops-limit=200000000','-fsyntax-only','-I'+str(ROOT/'main/hal/mosaico')]
   if adapter:
    (td/'nvs.h').write_text(MOCK);args+=['-DESP_PLATFORM','-I'+str(td),str(ROOT/'main/hal/mosaico/mosaico_factory_scale_journal.cpp')]
   result=subprocess.run(args+[str(src)],capture_output=True,text=True)
   log=OUT/(self._testMethodName+'.log');log.write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(log))
 def test_initial_reload_all_phases_and_immutable(self):
  self.compile(r'''
constexpr bool test(){Memory m{};j::StorageWorkspace w{};auto a=sample();const auto before=a;
if(j::loadInto(m,a.mac,w)!=j::Status::Missing||w.loaded.selected!=-1)return false;
if(j::saveInto(m,a,admitted,w)!=j::Status::Ready||w.loaded.selected!=0)return false;
if(!j::sameRecoveryImage(a,before)||a.snapshot.generation!=1)return false;
for(auto p:{r::Phase::Pending,r::Phase::Applied,r::Phase::Restoring,r::Phase::Original}){
a=next(a,p);if(j::saveInto(m,a,admitted,w)!=j::Status::Ready||j::loadInto(m,a.mac,w)!=j::Status::Ready)return false;
if(w.loaded.records[w.loaded.selected].snapshot.generation!=a.snapshot.generation)return false;}
return m.writes==5&&m.commits==5&&j::sameRecoveryImage(a,before);}
static_assert(test());
''')
 def test_faults_reject_alias_and_admission(self):
  self.compile(r'''
constexpr bool test(){const auto a=sample();
for(unsigned fault=0;fault<5;++fault){Memory m{};j::StorageWorkspace w{};
if(fault==0)m.io=true;if(fault==1)m.writeOk=false;if(fault==2)m.commitOk=false;if(fault==3)m.torn=true;if(fault==4)m.readbackError=true;
if(j::saveInto(m,a,admitted,w)==j::Status::Ready||!closed(w))return false;}
for(unsigned bits=0;bits<7;++bits){Memory m{};j::StorageWorkspace w{};
if(j::saveInto(m,a,{bool(bits&1),bool(bits&2),bool(bits&4)},w)!=j::Status::Rejected||m.reads||m.writes||!closed(w))return false;}
Memory m{};j::StorageWorkspace w{};w.loaded.records[0]=a;w.loaded.records[1]=a;w.scratch=a;
for(const auto* alias:{&w.loaded.records[0],&w.loaded.records[1],&w.scratch}){
if(j::saveInto(m,*alias,admitted,w)!=j::Status::Rejected||m.reads||m.writes||!closed(w))return false;}
return true;}
static_assert(test());
''')
 def test_torn_crc_schema_mac_standalone_and_history(self):
  self.compile(r'''
constexpr bool test(){const auto a=sample();
for(unsigned mode=0;mode<11;++mode){Memory m{};j::StorageWorkspace w{};auto b=a;
if(mode==0)b=next(a,r::Phase::Pending);if(mode==1)b.snapshot.generation=2;
if(!j::encode(b,m.slots[0],w.codec))return false;m.present[0]=true;
if(mode==2)m.slots[0][100]^=1;
if(mode==3){m.slots[0][3]='2';old::put(m.slots[0].data()+4,2,2);m.slots[0][21]=0;repair(m.slots[0]);}
if(mode==4){m.slots[0][13]^=1;repair(m.slots[0]);}
if(mode>=5){b=next(a,r::Phase::Pending);
if(mode==5)b.snapshot.generation=1;if(mode==6)b.snapshot.generation=3;
if(mode==7)b.snapshot.epoch=a.snapshot.epoch;if(mode==8)b.snapshot.phase=r::Phase::Applied;
if(mode==9)b.snapshot.epoch=a.snapshot.epoch-1;
if(!j::encode(b,m.slots[1],w.codec))return false;m.present[1]=true;
if(mode==10)m.slots[1][100]^=1;}
if(j::loadInto(m,a.mac,w)==j::Status::Ready||!closed(w))return false;
const auto slots=m.slots;const auto valid=w.loaded.valid;
if(j::saveInto(m,next(a,r::Phase::Pending),admitted,w)==j::Status::Ready||m.writes||m.slots!=slots||!closed(w))return false;
if(mode>=5&&!valid[0])return false;}
// No absent history legitimizes an already-mutated record.
Memory m{};j::StorageWorkspace w{};
return j::saveInto(m,next(a,r::Phase::Pending),admitted,w)==j::Status::Rejected&&m.writes==0&&closed(w);}
static_assert(test());
''')
 def test_actual_nvs_source_mock_harness_sdk_compiles(self):
  self.compile(HARNESS,adapter=True)



# The actual adapter is also linked and executed on the existing private RV32
# emulator. Only external NVS and libc are mocked, never the storage functions.
def _test_actual_nvs_rv32(self):
 import os,sys,struct,hashlib
 import test_mosaico_gauge_scale_backend as base
 OUT.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.nvs-rv32-',dir=OUT) as td:
  td=Path(td)
  for name,text in base.HEADERS.items():
   if name!='nvs.h':(td/name).write_text(text)
  (td/'nvs.h').write_text(MOCK.replace('<stddef.h>','<cstddef>').replace('<stdint.h>','<cstdint>'))
  pre=r'''
#include <cstring>
#include <cstdio>
extern "C" {volatile int test_results[1]={-999};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
#define assert(expr) do {if(!(expr)){test_results[0]=__LINE__;test_done();}}while(false)
'''
  support=base.MOCKS.split('static Hal hal;',1)[0]
  src=td/'nvs-test.cpp';src.write_text(self.fixture+MODEL+pre+support+HARNESS.replace('#include <cassert>','')+r'''
extern "C" void _start(){test_results[0]=main();test_done();}
''')
  elf=OUT/'nvs-mock.elf'
  args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1',
   '-Wall','-Wextra','-Werror','-Wno-missing-multilib','-Wno-misleading-indentation','-fconstexpr-steps=100000000',
   '-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib',
   '-DESP_PLATFORM','-I'+str(td),'-I'+str(ROOT/'main/hal/mosaico'),str(src),
   str(ROOT/'main/hal/mosaico/mosaico_factory_scale_journal.cpp'),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),
   '-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
  result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TEMP':str(td),'TMP':str(td)})
  (OUT/'rv32-compile.log').write_text(result.stdout+result.stderr)
  self.assertEqual(result.returncode,0,str(OUT/'rv32-compile.log'))
 sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
 from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
 from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
 raw=elf.read_bytes();entry=struct.unpack_from('<I',raw,24)[0];phoff=struct.unpack_from('<I',raw,28)[0]
 phsize,phnum=struct.unpack_from('<HH',raw,42);uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
 for i in range(phnum):
  kind,off,addr,_,n,_,_,_=struct.unpack_from('<8I',raw,phoff+i*phsize)
  if kind==1:uc.mem_write(addr,raw[off:off+n])
 shoff=struct.unpack_from('<I',raw,32)[0];shsize,shnum=struct.unpack_from('<HH',raw,46);symbols={}
 for i in range(shnum):
  sh=struct.unpack_from('<10I',raw,shoff+i*shsize)
  if sh[1]!=2:continue
  strings=struct.unpack_from('<10I',raw,shoff+sh[6]*shsize);names=raw[strings[4]:strings[4]+strings[5]]
  for pos in range(sh[4],sh[4]+sh[5],sh[9]):
   name,value,_,_,_,_=struct.unpack_from('<IIIBBH',raw,pos);symbols[names[name:].split(b'\0',1)[0].decode()]=value
 uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=120000000,count=500000000)
 value=struct.unpack('<i',bytes(uc.mem_read(symbols['test_results'],4)))[0]
 (OUT/'rv32-execution.log').write_text(f'Actual GSJ3 adapter, mocked NVS/libc, RV32 emulator. Result={value}; ELF_SHA256={hashlib.sha256(raw).hexdigest()}\n')
 self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(value,0)

StorageTests.test_actual_nvs_rv32=_test_actual_nvs_rv32
if __name__=='__main__':unittest.main()
