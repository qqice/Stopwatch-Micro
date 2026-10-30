"""Bounded NAND DERP metadata schema and cross-page candidate reconstruction."""
from pathlib import Path
import copy,importlib.util,json,struct,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('candidate_export',ROOT/'tools/mosaico_system_logs.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class DerpJournalTests(unittest.TestCase):
 def test_model_actual_cpp(self):
  code=r'''
#include "mosaico_system_log_model.h"
using namespace MosaicoSystemLog::Model;
struct E{uint32_t version=1,seq=9,attempt_id=3;uint64_t monotonic_ms=0x123456789ULL;uint32_t elapsed_ms=5000;int32_t result=-1,error=116;uint16_t region=902,port=33380,node_index=65535;uint8_t family=4,source=1,stage=4;bool errno_valid=true;uint8_t address[16]={203,0,113,7};uint16_t candidate_len=3;bool candidate_truncated=false;char candidate[128]={'a','\n','b'};};
constexpr bool test(){E e;std::array<Record,5> g{};if(derpCandidateRecords(e,g)!=2)return false;
 if(g[0].monotonicMs!=e.monotonic_ms||g[0].flags!=0x100||g[0].args[0]!=9||g[0].args[2]!=(902U|(33380U<<16))||g[0].args[7]!=0x077100cb||g[0].args[4]!=uint32_t(-1))return false;
 if((g[0].args[3]&65535)!=65535||g[1].args[3]!=0x00620a61||g[1].args[2]!=3)return false;
 e.family=6;for(unsigned i=0;i<16;++i)e.address[i]=i;e.candidate_len=127;e.candidate_truncated=true;for(unsigned i=0;i<127;++i)e.candidate[i]='A';
 if(derpCandidateRecords(e,g)!=5||g[0].args[10]!=0x0f0e0d0c||g[4].args[10]!=0x00414141||!(g[0].args[3]&(1U<<29)))return false;
 Queue q;for(unsigned i=0;i<124;++i)q.push(g[0]);const auto before=q.count;
 if(q.pushGroup(g,5)||q.count!=before||q.dropped)return false;
 if(!q.consume(1)||!q.pushGroup(g,5)||q.count!=128)return false;
 if(q.pushGroup(g,0)||q.pushGroup(g,6))return false;
 // Execute the consumer's peek -> complete group -> consume sequence
 // against the actual bounded Queue. Queue refusal must retain the producer.
 E waiting;std::array<Record,5> retry{};if(derpCandidateRecords(waiting,retry)!=2)return false;
 Queue q2;for(unsigned i=0;i<127;++i)q2.push(retry[0]);bool pending=true;unsigned consumed=0;
 auto drain=[&](){if(!pending)return;if(!q2.pushGroup(retry,2))return;pending=false;++consumed;};
 drain();if(!pending||consumed||q2.count!=127||q2.dropped)return false;
 q2.consume(1);drain();if(pending||consumed!=1||q2.count!=128)return false;
 if(decode(q2.data[127]).type!=Type::DerpCandidate||decode(q2.data[0]).type!=Type::DerpCandidateName)return false;
 drain();if(consumed!=1||q2.count!=128)return false;
 e.candidate_len=128;if(derpCandidateRecords(e,g))return false;e.candidate_len=0;e.stage=0;if(derpCandidateRecords(e,g))return false;
 e.stage=4;e.family=8;if(derpCandidateRecords(e,g))return false;e.family=0;e.version=2;if(derpCandidateRecords(e,g))return false;
 e.version=1;if(derpCandidateRecords(e,g)!=2||g[1].args[2]!=0x10000)return false;
 return true;}
static_assert(test());static_assert(RecordBytes==64&&Capacity==128);
'''
  d=ROOT/'artifacts/derp-journal-tests';d.mkdir(parents=True,exist_ok=True);p=d/'model.cpp';p.write_text(code,encoding='utf8')
  compilers=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'));self.assertTrue(compilers)
  r=subprocess.run([str(compilers[0]),'-std=c++20','-Wall','-Wextra','-Werror','-fsyntax-only','-I',str(ROOT/'main/host'),str(p)],capture_output=True,text=True)
  (d/'model.log').write_text(r.stdout+r.stderr,encoding='utf8');self.assertEqual(r.returncode,0,r.stderr)
 def rows(self,name=b'host\n\x1b.example',family=4):
  packed=65535|(family<<16)|(3<<20)|(4<<24)|(1<<28);ip=bytes([203,0,113,7])+bytes(12)if family==4 else bytes(range(16))
  h=dict(type_id=16,flags=0x100,page_boot=1,monotonic_ms=44,args=[9,3,902|(33380<<16),packed,0xffffffff,116,5000,*struct.unpack('<4I',ip)])
  # Use the production page decoder, with real page/header CRCs.
  a=h['args'];p=bytearray(2048);struct.pack_into('<IHHQIHH',p,0,m.PAGE_MAGIC,1,60,5,1,1,0);struct.pack_into('<I',p,24,m.crc(p[:24]));struct.pack_into('<HHQQ11I',p,60,16,0x100,44,0,*a);struct.pack_into('<I',p,2044,m.crc(p[:2044]));h=m.decode_page(p,5)[0]
  ns=[]
  for j in range(max(1,(len(name)+31)//32)):
   chunk=name[j*32:(j+1)*32].ljust(32,b'\0');ns.append(dict(type_id=17,flags=0x100,page_boot=1,page_seq=6,monotonic_ms=44,args=[9,j,len(name),*struct.unpack('<8I',chunk)]))
  return [h]+ns
 def test_roundtrip_and_cross_page(self):
  rows=self.rows();m.attach_derp_candidates(rows);d=rows[0]['derp_candidate'];self.assertTrue(d['candidate_complete']);self.assertEqual(d['candidate'],'host\n\x1b.example');self.assertEqual(d['address'],'203.0.113.7');self.assertEqual(d['result'],-1)
  rows=self.rows(b'x'*127,6);m.attach_derp_candidates(rows);self.assertTrue(rows[0]['derp_candidate']['candidate_complete']);self.assertEqual(rows[0]['derp_candidate']['address'],'1:203:405:607:809:a0b:c0d:e0f')
 def test_missing_mismatched_chunks_explicit(self):
  for mutate in (lambda r:r.pop(),lambda r:r[1]['args'].__setitem__(0,10),lambda r:r[1].__setitem__('page_boot',2),lambda r:r[1].__setitem__('monotonic_ms',45),lambda r:r[1]['args'].__setitem__(1,1),lambda r:r[1]['args'].__setitem__(2,128)):
   rows=self.rows();mutate(rows);m.attach_derp_candidates(rows);self.assertFalse(rows[0]['derp_candidate']['candidate_complete'])
 def test_consumer_only_after_atomic_enqueue(self):
  s=(ROOT/'main/host/mosaico_system_log.cpp').read_text(encoding='utf8');body=s.split('void collectDerpCandidates(){',1)[1].split('void collect(',1)[0]
  self.assertIn('if(!enqueueDerpGroup(group,count,failed))break;',body);self.assertIn('if(!microlink_derp_candidate_consume(e.seq))',body)
  self.assertIn('storage->queue.pushGroup(group,count)',s)
if __name__=='__main__':unittest.main()
