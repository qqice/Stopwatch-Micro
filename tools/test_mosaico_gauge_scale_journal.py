"""Portable compile-time storage fault tests, no firmware/device writes."""
from pathlib import Path
from datetime import datetime, timezone
import os
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
RUN_DIR=ROOT/'artifacts'/'gauge-scale-journal-tests'/datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')
CXX=os.environ.get('CXX', 'C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
FIXTURE=r'''
#include "mosaico_gauge_scale_journal.h"
using namespace mosaico_gauge_scale_journal;
constexpr Record sample(){Record r{};r.mac={0x24,1,2,3,4,5};auto& s=r.snapshot;
 s.identity={0x0220,3,4};s.binding=binding(r.mac);s.layoutId=recovery::Layout::Public131V1;
 s.count=131;s.generation=1;s.epoch=1;s.scale=recovery::Scale::Two;
 for(size_t i=0;i<131;++i){auto& f=s.fields[i];f.address=recovery::layout[i].address;f.width=recovery::layout[i].width;
  for(size_t j=0;j<f.width;++j){f.original[j]=uint8_t(i+j);f.target[j]=uint8_t(i+j+1);}}
 return r;}
constexpr Record advance(Record r,recovery::Phase p){++r.snapshot.generation;++r.snapshot.epoch;r.snapshot.phase=p;return r;}
constexpr WriteAdmission admission{true,true,true};
struct Fake {
 std::array<Blob,2> disk{};std::array<bool,2> exists{};Blob pending{};unsigned slot=0,writes=0;
 bool writeFail=false,commitFail=false,torn=false,readbackFail=false,io=false,staged=false;
 constexpr ReadResult read(unsigned i,Blob& b){if(io||(readbackFail&&writes))return ReadResult::Error;
  if(!exists[i])return ReadResult::Missing;
  b=disk[i];return ReadResult::Present;}
 constexpr bool write(unsigned i,const Blob& b){++writes;if(writeFail)return false;slot=i;pending=b;staged=true;return true;}
 constexpr bool commit(){if(commitFail)return false;if(!staged)return false;
  disk[slot]=pending;exists[slot]=true;if(torn)disk[slot][70]^=1;staged=false;return true;}
};
'''
class JournalTests(unittest.TestCase):
 def compile(self, code):
  with tempfile.TemporaryDirectory(prefix='.gauge-journal-',dir=ROOT) as td:
   p=Path(td)/'test.cpp';p.write_text(FIXTURE+code)
   result=subprocess.run([CXX,'-std=c++20','-Wall','-Wextra','-Werror','-fconstexpr-ops-limit=200000000','-fsyntax-only','-I'+str(ROOT/'main/hal/mosaico'),str(p)],capture_output=True,text=True)
   log=RUN_DIR/f'{self._testMethodName}.log';log.parent.mkdir(parents=True,exist_ok=True)
   log.write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(log))
 def test_codec_identity_canonical_all131_crc(self):
  self.compile(r'''
constexpr bool test(){const auto r=sample();Blob b{};Record out{};
 if(blobSize!=512||rawImageSize()!=222||!encode(r,b)||!decode(b,r.mac,out)||!sameRecoveryImage(r,out))return false;
 for(size_t i=0;i<131;++i){auto bad=b;bad[payloadOffset(i)]^=1;
  if(decode(bad,r.mac,out))return false;}
 auto mac=r.mac;mac[5]^=1;if(decode(b,mac,out))return false;
 for(auto offset:{size_t(0),size_t(4),size_t(6),size_t(14),size_t(16),size_t(18),size_t(19),size_t(20),size_t(21),size_t(22),size_t(36),size_t(38),size_t(40),size_t(44),size_t(48),size_t(52)}){
  auto bad=b;bad[offset]^=0x80;put(bad.data()+blobSize-4,crc32(bad.data(),blobSize-4),4);
  if(decode(bad,r.mac,out))return false;}
 auto extremal=r;
 for(size_t i=0;i<131;++i){
  for(size_t j=0;j<r.snapshot.fields[i].width;++j){extremal.snapshot.fields[i].original[j]=uint8_t(0x80|((i+j)&0x7f));extremal.snapshot.fields[i].target[j]=uint8_t(0x7f^((i+j)&0x7f));}
  for(size_t j=0;j<4;++j){out.snapshot.fields[i].original[j]=0xff;out.snapshot.fields[i].target[j]=0xff;}
  auto badWidth=r;badWidth.snapshot.fields[i].width=r.snapshot.fields[i].width==1?2:1;if(encode(badWidth,b))return false;
  auto badAddress=r;badAddress.snapshot.fields[i].address^=1;if(encode(badAddress,b))return false;
  for(size_t j=r.snapshot.fields[i].width;j<4;++j){auto invalid=r;invalid.snapshot.fields[i].original[j]=1;if(encode(invalid,b))return false;
   invalid=r;invalid.snapshot.fields[i].target[j]=0x80;if(encode(invalid,b))return false;}
 }
 if(!encode(extremal,b)||!decode(b,r.mac,out)||!sameRecoveryImage(extremal,out))return false;
 Blob legacy{};encode(r,legacy);legacy[3]='1';put(legacy.data()+4,1,2);put(legacy.data()+6,1509,2);put(legacy.data()+blobSize-4,crc32(legacy.data(),blobSize-4),4);
 if(decode(legacy,r.mac,out))return false;
 auto invalid=r;invalid.snapshot.fields[0].width=3;if(encode(invalid,b))return false;
 invalid=r;invalid.snapshot.binding.bytes[31]=1;if(encode(invalid,b))return false;
 return true;}
static_assert(test());
''')
 def test_transitions_missing_ambiguous_retained_recovery(self):
  self.compile(r'''
constexpr bool test(){Fake f{};auto r=sample();
 if(load(f,r.mac).status!=Status::Missing)return false;
 if(save(f,r,{})!=Status::Rejected||f.writes)return false;
 if(save(f,r,admission)!=Status::Ready)return false;
 auto l=load(f,r.mac);if(l.status!=Status::Ready||l.selected!=0)return false;
 for(auto phase:{recovery::Phase::Pending,recovery::Phase::Applied,recovery::Phase::Restoring,recovery::Phase::Original}){
  auto next=advance(r,phase);if(save(f,next,admission)!=Status::Ready)return false;
  l=load(f,r.mac);if(l.status!=Status::Ready||l.records[size_t(l.selected)].snapshot.phase!=phase)return false;r=next;}
 auto bad=advance(r,recovery::Phase::Pending);bad.snapshot.fields[1].original[0]^=1;
 const auto writes=f.writes;if(save(f,bad,admission)!=Status::Rejected||f.writes!=writes)return false;
 // Same generation, gaps, wrap, rollback epoch, changed recovery originals fail closed.
 for(unsigned mode=0;mode<5;++mode){Fake g{};auto a=sample();auto b=advance(a,recovery::Phase::Pending);
  if(mode==0)b.snapshot.generation=1;
  if(mode==1)b.snapshot.generation=3;
  if(mode==2)b.snapshot.generation=UINT32_MAX;
  if(mode==3)b.snapshot.epoch=1;
  if(mode==4)b.snapshot.fields[0].original[0]^=1;
  encode(a,g.disk[0]);encode(b,g.disk[1]);g.exists={true,true};
  l=load(g,a.mac);if(l.status!=Status::Ambiguous||!l.valid[0]||!l.valid[1]||l.selected!=-1)return false;
  if(save(g,advance(b,recovery::Phase::Restoring),admission)==Status::Ready||g.writes)return false;}
 Fake g{};r=advance(sample(),recovery::Phase::Pending);encode(r,g.disk[0]);g.exists[0]=true;
 return load(g,r.mac).status==Status::Ambiguous;
}
static_assert(test());
''')
 def test_storage_powerloss_failures_never_overwrite_last_good(self):
  self.compile(r'''
constexpr bool test(){for(unsigned fault=0;fault<5;++fault){Fake f{};auto r=sample();
 if(save(f,r,admission)!=Status::Ready)return false;
 const auto original=f.disk[0];f.writes=0;
 if(fault==0)f.writeFail=true;
 if(fault==1)f.commitFail=true;
 if(fault==2)f.torn=true;
 if(fault==3)f.readbackFail=true;
 if(fault==4)f.io=true;
 auto next=advance(r,recovery::Phase::Pending);const auto status=save(f,next,admission);
 const Status expected[]={Status::WriteFailed,Status::CommitFailed,Status::ReadbackFailed,Status::ReadbackFailed,Status::IoError};
 if(status!=expected[fault]||f.disk[0]!=original)return false;
 f.readbackFail=false;f.io=false;const auto l=load(f,r.mac);
 if(!l.valid[0]||!sameRecoveryImage(l.records[0],r))return false;
 if(fault==2&&(l.status!=Status::Corrupt||l.selected!=-1))return false;
 }return true;}
static_assert(test());
''')
 def test_workspace_scratch_alias_and_corrupt_either_slot(self):
  self.compile(r'''
constexpr bool test(){Fake f{};Workspace w{};w.scratch=sample();
 if(saveInto(f,w.scratch,admission,w)!=Status::Ready)return false;
 if(loadInto(f,w.scratch.mac,w)!=Status::Ready)return false;
 if(saveInto(f,w.loaded.records[0],admission,w)!=Status::Rejected)return false;
 w.scratch=advance(w.loaded.records[0],recovery::Phase::Pending);
 if(saveInto(f,w.scratch,admission,w)!=Status::Ready)return false;
 const auto mac=w.scratch.mac;
 if(loadInto(f,mac,w)!=Status::Ready||w.loaded.selected!=1)return false;
 for(unsigned broken=0;broken<2;++broken){auto g=f;g.disk[broken][70]^=1;
  const auto preserved=g.disk[1-broken];const auto writes=g.writes;
  if(loadInto(g,mac,w)!=Status::Corrupt||w.loaded.selected!=-1||!w.loaded.valid[1-broken]||w.loaded.valid[broken])return false;
  w.scratch=advance(sample(),recovery::Phase::Pending);
  if(saveInto(g,w.scratch,admission,w)!=Status::Corrupt||g.writes!=writes||g.disk[1-broken]!=preserved)return false;
 }
 return true;}
static_assert(test());
''')
 def test_nvs_adapter_compiles_and_stack_budget(self):
  logdir=RUN_DIR;logdir.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.gauge-journal-',dir=ROOT) as td:
   td=Path(td)
   (td/'nvs.h').write_text("""#pragma once
#include <cstdint>
#include <cstddef>
using nvs_handle_t=uint32_t;
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
int nvs_open(const char*,int,nvs_handle_t*);
void nvs_close(nvs_handle_t);
int nvs_get_blob(nvs_handle_t,const char*,void*,size_t*);
int nvs_set_blob(nvs_handle_t,const char*,const void*,size_t);
int nvs_commit(nvs_handle_t);
""")
   obj=logdir/'journal-adapter.o'
   result=subprocess.run([CXX,'-std=c++20','-Wall','-Wextra','-Werror','-Os','-DESP_PLATFORM','-fstack-usage','-c','-I'+str(td),str(ROOT/'main/hal/mosaico/mosaico_gauge_scale_journal.cpp'),'-o',str(obj)],capture_output=True,text=True)
   (logdir/'adapter.log').write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(logdir/'adapter.log'))
   usage=obj.with_suffix('.su').read_text()
   for line in usage.splitlines():
    self.assertLessEqual(int(line.split('\t')[1]),256,line)
   (td/'sizes.cpp').write_text('#include "mosaico_gauge_scale_journal.h"\nchar journal_workspace[sizeof(mosaico_gauge_scale_journal::Workspace)];\nchar journal_record[sizeof(mosaico_gauge_scale_journal::Record)];\n')
   result=subprocess.run([CXX,'-std=c++20','-S','-I'+str(ROOT/'main/hal/mosaico'),str(td/'sizes.cpp'),'-o',str(logdir/'sizes.s')],capture_output=True,text=True)
   (logdir/'sizes.log').write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0)
if __name__=='__main__':unittest.main()
