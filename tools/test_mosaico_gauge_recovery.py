"""Pure offline recovery planning tests; no storage/CRC implementation or hardware."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
FIXTURE=r'''
#include "mosaico_gauge_recovery_model.h"
using namespace mosaico_gauge_recovery;
constexpr Snapshot sample(){Snapshot s{};s.identity={0x0220,0x0003,4};s.binding.bytes[31]=0xa5;
 s.layoutId=Layout::Public131V1;s.generation=1;s.scale=Scale::Two;s.epoch=10;s.count=fieldCount;
 for(size_t i=0;i<fieldCount;++i){s.fields[i].address=layout[i].address;s.fields[i].width=layout[i].width;
  for(size_t j=0;j<layout[i].width;++j){s.fields[i].original[j]=uint8_t(0x10+i+j);s.fields[i].target[j]=s.fields[i].original[j]^1;}
 }return s;
}
constexpr Context context(const Snapshot& s){Context c{};c.identity=s.identity;c.binding=s.binding;c.layoutId=s.layoutId;c.sourceCrcAttested=c.reviewedTargetAttested=true;return c;}
constexpr Observation observe(const Snapshot& s,bool target=false){Observation o{};o.count=fieldCount;o.authenticated=o.sealed=o.cfg0=true;
 for(size_t i=0;i<fieldCount;++i){for(size_t j=0;j<s.fields[i].width;++j){o.raw[i][j]=target?s.fields[i].target[j]:s.fields[i].original[j];}}
 return o;
}
constexpr DurableProof proof(const Snapshot& s,uint64_t nextEpoch){DurableProof p{};
 p.originalDurable=p.originalReadbackIdentical=p.pendingDurable=p.pendingReadbackIdentical=p.journalCrcAttested=true;
 p.generation=s.generation;p.intendedEpoch=nextEpoch;return p;
}
constexpr bool denied(Decision d){return d.action==Action::FailClosed&&!d.publishPhysicalTelemetry&&!d.parameterWritesAuthorized;}
static_assert(!readyForHardware,"deliberately no production/hardware admission");
'''
class RecoveryTests(unittest.TestCase):
 def compile(self,code):
  self.assertTrue(COMPILER.is_file())
  with tempfile.TemporaryDirectory() as directory:
   source=Path(directory)/'recovery.cpp';source.write_text(FIXTURE+code)
   result=subprocess.run([str(COMPILER),'-std=c++20','-Wall','-Wextra','-Werror','-fconstexpr-ops-limit=100000000','-fsyntax-only','-I'+str(ROOT/'main/hal/mosaico'),str(source)],capture_output=True,text=True)
   log=ROOT/'artifacts/gauge-recovery-tests'/f'{self._testMethodName}.log'
   log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(log))

 def test_fixed131_fields_widths_binding_identity_and_attestation(self):
  self.compile(r'''
constexpr bool cases(){const auto valid=sample();const auto c=context(valid);
 if(fieldCount!=131||validate(valid,c)!=Error::None||!validCanonicalLayout())return false;
 size_t ones=0,twos=0,fours=0;
 for(auto f:layout){ones+=f.width==1;twos+=f.width==2;fours+=f.width==4;}
 if(!ones||!twos||fours!=2||ones+twos+fours!=131)return false;
 for(size_t i=0;i<fieldCount;++i){
  auto s=valid;s.fields[i].address^=1;if(validate(s,c)!=Error::Shape)return false;
  if(i){s=valid;s.fields[i].address=s.fields[i-1].address;if(validate(s,c)!=Error::Shape)return false;}
  for(unsigned width=0;width<=5;++width){if(width==layout[i].width)continue;
   s=valid;s.fields[i].width=uint8_t(width);if(validate(s,c)!=Error::Shape)return false;
  }
  for(size_t j=layout[i].width;j<4;++j){s=valid;s.fields[i].original[j]=1;if(validate(s,c)!=Error::Shape)return false;
   s=valid;s.fields[i].target[j]=1;if(validate(s,c)!=Error::Shape)return false;
  }
 }
 for(size_t count=0;count<=132;++count){if(count==131)continue;auto s=valid;s.count=count;if(validate(s,c)!=Error::Shape)return false;}
 for(auto scale:{Scale::One,Scale::Two,Scale::Four}){auto s=valid;s.scale=scale;if(validate(s,c)!=Error::None)return false;}
 for(auto scale:{0,3,5,255}){auto s=valid;s.scale=Scale(scale);if(validate(s,c)!=Error::Shape)return false;}
 auto s=valid;s.generation=0;if(validate(s,c)!=Error::Shape)return false;
 s=valid;s.phase=Phase(255);if(validate(s,c)!=Error::Shape)return false;
 s=valid;s.identity.device^=1;if(validate(s,c)!=Error::Identity)return false;
 s=valid;s.identity.firmware^=1;if(validate(s,c)!=Error::Identity)return false;
 s=valid;s.identity.build=3;if(validate(s,c)!=Error::Identity)return false;
 auto bad=c;bad.identity.build=5;if(validate(valid,bad)!=Error::Identity)return false;
 bad=c;bad.identity.device=0;if(validate(valid,bad)!=Error::Identity)return false;
 bad=c;bad.identity.firmware=0;if(validate(valid,bad)!=Error::Identity)return false;
 for(size_t i=0;i<32;++i){bad=c;bad.binding.bytes[i]^=1;if(validate(valid,bad)!=Error::Binding)return false;}
 bad=c;bad.binding={};if(validate(valid,bad)!=Error::Binding)return false;
 s=valid;s.binding={};if(validate(s,c)!=Error::Binding)return false;
 s=valid;s.layoutId=Layout::Unknown;if(validate(s,c)!=Error::Layout)return false;
 bad=c;bad.layoutId=Layout::Unknown;if(validate(valid,bad)!=Error::Layout)return false;
 bad=c;bad.sourceCrcAttested=false;if(validate(valid,bad)!=Error::Attestation)return false;
 bad=c;bad.reviewedTargetAttested=false;if(validate(valid,bad)!=Error::Attestation)return false;
 return true;
}
static_assert(cases(),"exact131 canonical fields widths all unused bytes identity opaque binding scale layout and external attestations");
''')

 def test_every_recovery_breakpoint_and_unexpected_third_value(self):
  self.compile(r'''
constexpr bool cases(unsigned section){auto s=sample();auto c=context(s);auto original=observe(s);auto target=observe(s,true);
 if(classify(s,original)!=Image::Original||classify(s,target)!=Image::Target)return false;
 if(section==0)for(auto phase:{Phase::Pending,Phase::Applied,Phase::Restoring})for(size_t completed=0;completed<=fieldCount;++completed){
  s.phase=phase;auto o=original;
  for(size_t i=0;i<completed;++i)for(size_t j=0;j<s.fields[i].width;++j)o.raw[i][j]=target.raw[i][j];
  auto boot=startup(s,c,o);
  if(boot.action!=Action::RestoreRequired||boot.publishPhysicalTelemetry||boot.parameterWritesAuthorized)return false;
  auto d=restore(s,c,o,proof(s,11),11);
  if(d.action!=Action::RestoreOriginal||d.nextPhase!=Phase::Restoring||d.nextEpoch!=11||d.parameterWritesAuthorized||d.publishPhysicalTelemetry)return false;
  if(!denied(apply(s,c,o,proof(s,11),11)))return false;
 }
 s.phase=Phase::Original;
 if(section==1)for(size_t i=0;i<fieldCount;++i){auto o=original;
  for(size_t j=0;j<s.fields[i].width;++j)o.raw[i][j]=target.raw[i][j];
  if(classify(s,o)!=Image::Mixed||startup(s,c,o).action!=Action::RestoreRequired||apply(s,c,o,proof(s,11),11).action!=Action::RestoreRequired)return false;
 }
 if(section==2)for(size_t i=0;i<fieldCount;++i)for(size_t j=0;j<s.fields[i].width;++j){auto o=original;o.raw[i][j]^=0x40;
  if(classify(s,o)!=Image::Unexpected||!denied(startup(s,c,o))||!denied(restore(s,c,o,proof(s,11),11))||!denied(apply(s,c,o,proof(s,11),11)))return false;
  auto restoring=s;restoring.phase=Phase::Restoring;
  if(!denied(finishRestore(restoring,c,o,proof(s,11),11)))return false;
 }
 auto o=original;o.authenticated=false;if(!denied(startup(s,c,o))||!denied(restore(s,c,o,proof(s,11),11)))return false;
 for(size_t count=0;count<=132;++count){if(count==131)continue;o=original;o.count=count;
  if(!denied(startup(s,c,o))||!denied(restore(s,c,o,proof(s,11),11)))return false;
 }
 return true;
}
static_assert(cases(0),"all131 breakpoints across Pending Applied Restoring");
static_assert(cases(1),"all mixed fields only restore");
static_assert(cases(2),"every third-value byte fails closed");
''')

 def test_durable_readback_attestations_and_epoch_monotonicity(self):
  self.compile(r'''
constexpr bool cases(){auto s=sample();auto c=context(s);auto o=observe(s);auto good=proof(s,11);
 for(unsigned fault=0;fault<7;++fault){auto p=good;
  if(fault==0)p.originalDurable=false;
  if(fault==1)p.originalReadbackIdentical=false;
  if(fault==2)p.pendingDurable=false;
  if(fault==3)p.pendingReadbackIdentical=false;
  if(fault==4)p.journalCrcAttested=false;
  if(fault==5)p.generation=2;
  if(fault==6)p.intendedEpoch=10;
  if(!denied(apply(s,c,o,p,11))||!denied(restore(s,c,o,p,11)))return false;
  auto restoring=s;restoring.phase=Phase::Restoring;if(!denied(finishRestore(restoring,c,o,p,11)))return false;
  auto pending=s;pending.phase=Phase::Pending;if(!denied(finishApply(pending,c,observe(s,true),p,11)))return false;
 }
 if(!denied(apply(s,c,o,DurableProof{},11)))return false;
 for(auto next:{uint64_t(0),uint64_t(9),uint64_t(10)}){
  if(!denied(apply(s,c,o,proof(s,next),next))||!denied(restore(s,c,o,proof(s,next),next)))return false;
  auto r=s;r.phase=Phase::Restoring;if(!denied(finishRestore(r,c,o,proof(r,next),next)))return false;
  r.phase=Phase::Pending;if(!denied(finishApply(r,c,observe(s,true),proof(r,next),next)))return false;
 }
 s.epoch=UINT64_MAX;
 for(auto next:{uint64_t(0),UINT64_MAX})if(!denied(apply(s,c,o,proof(s,next),next))||!denied(restore(s,c,o,proof(s,next),next)))return false;
 s.phase=Phase::Restoring;if(!denied(finishRestore(s,c,o,proof(s,0),0)))return false;
 s.phase=Phase::Pending;if(!denied(finishApply(s,c,observe(s,true),proof(s,0),0)))return false;
 return true;
}
static_assert(cases(),"each torn/durable/CRC/stale generation or intent attestation fails; epochs strictly advance and cannot wrap");
''')

 def test_full_roundtrip_and_completion_gates(self):
  self.compile(r'''
constexpr bool cases(){for(auto scale:{Scale::One,Scale::Two,Scale::Four}){
 auto s=sample();s.scale=scale;auto c=context(s);auto o=observe(s);auto t=observe(s,true);
 auto boot=startup(s,c,o);if(boot.action!=Action::OriginalNoWrite||!boot.publishPhysicalTelemetry||boot.parameterWritesAuthorized)return false;
 auto d=apply(s,c,o,proof(s,11),11);if(d.action!=Action::WriteReviewedTarget||d.nextPhase!=Phase::Pending||d.nextEpoch!=11||d.parameterWritesAuthorized)return false;
 s.phase=d.nextPhase;s.epoch=d.nextEpoch;
 if(startup(s,c,t).publishPhysicalTelemetry)return false;
 d=finishApply(s,c,t,proof(s,12),12);if(d.action!=Action::MarkApplied||d.publishPhysicalTelemetry)return false;
 auto mixed=t;for(size_t j=0;j<s.fields[0].width;++j)mixed.raw[0][j]=o.raw[0][j];
 if(finishApply(s,c,mixed,proof(s,12),12).action!=Action::RestoreRequired)return false;
 s.phase=d.nextPhase;s.epoch=d.nextEpoch;
 d=restore(s,c,mixed,proof(s,13),13);if(d.action!=Action::RestoreOriginal||d.nextEpoch!=13)return false;
 s.phase=d.nextPhase;s.epoch=d.nextEpoch;
 if(!denied(finishRestore(s,c,t,proof(s,14),14))||!denied(finishRestore(s,c,mixed,proof(s,14),14)))return false;
 auto bad=o;bad.sealed=false;if(!denied(finishRestore(s,c,bad,proof(s,14),14)))return false;
 bad=o;bad.cfg0=false;if(!denied(finishRestore(s,c,bad,proof(s,14),14)))return false;
 bad=o;bad.authenticated=false;if(!denied(finishRestore(s,c,bad,proof(s,14),14)))return false;
 d=finishRestore(s,c,o,proof(s,14),14);
 if(d.action!=Action::MarkOriginal||d.nextPhase!=Phase::Original||d.nextEpoch!=14||d.publishPhysicalTelemetry||d.parameterWritesAuthorized)return false;
 s.phase=d.nextPhase;s.epoch=d.nextEpoch;boot=startup(s,c,o);
 if(boot.action!=Action::OriginalNoWrite||!boot.publishPhysicalTelemetry)return false;
 }
 // No-op control scale: identical target/original is still an exact target.
 auto s=sample();s.scale=Scale::One;for(auto& f:s.fields)for(size_t j=0;j<4;++j)f.target[j]=f.original[j];
 s.phase=Phase::Pending;auto c=context(s);auto o=observe(s);
 return finishApply(s,c,o,proof(s,11),11).action==Action::MarkApplied;
}
static_assert(cases(),"offline lifecycle and final131 exact original plus authenticated SEALED CFG0; no hardware authorization");
''')

 def test_no_production_sdk_storage_or_parameter_write_entry(self):
  model=(ROOT/'main/hal/mosaico/mosaico_gauge_recovery_model.h').read_text()
  for forbidden in ('heap_caps_', 'malloc(', 'new ', 'nvs_', 'i2c_', 'esp_ota_', 'writeChunk(', 'gauge_access_word('):
   self.assertNotIn(forbidden,model)
  self.assertIn('readyForHardware=false',model)
  self.assertIn('parameterWritesAuthorized=false',model)
  self.assertIn('Attestations are caller responsibilities',model)
  for source in ROOT.joinpath('main').rglob('*.cpp'):
   self.assertNotIn('#include "mosaico_gauge_recovery_model.h"',source.read_text(encoding='utf-8'))

if __name__=='__main__':unittest.main()
