"""Offline constexpr contrast transaction/fault tests. No SDK/device operations."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
FIXTURE = r'''
#include "mosaico_gauge_contrast_journal.h"
namespace c=mosaico_gauge_contrast;
namespace j=mosaico_gauge_contrast_journal;
constexpr c::Record sample(){c::Record r{};r.mac={0x24,1,2,3,4,5};r.generation=1;r.epoch=1;r.source=c::sourceFixture;return r;}
constexpr c::Record step(c::Record r,c::Phase phase){++r.generation;++r.epoch;r.phase=phase;return r;}
struct Store {
 std::array<j::Blob,2> blobs{};std::array<bool,2> present{};
 size_t space=56;int writes=0,commits=0,reads=0,capacityCalls=0;
 bool failWrite=false,failCommit=false,failReadback=false,io=false,capacityOk=true;
 constexpr j::ReadResult read(unsigned i,j::Blob& b){++reads;if(io)return j::ReadResult::Error;
  if(!present[i])return j::ReadResult::Missing;
  b=blobs[i];
  if(failReadback&&commits)b[0]^=1;
  return j::ReadResult::Present;}
 constexpr bool write(unsigned i,const j::Blob& b){++writes;if(failWrite)return false;blobs[i]=b;present[i]=true;return true;}
 constexpr bool commit(){++commits;return !failCommit;}
 constexpr bool available(size_t& n){++capacityCalls;n=space;return capacityOk;}
};
inline constexpr j::Admission admitted{true,true,true};
'''


class ContrastTests(unittest.TestCase):
    def compile(self, code):
        self.assertTrue(COMPILER.is_file())
        logdir = ROOT / 'artifacts/gauge-contrast-tests'
        logdir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=logdir) as directory:
            source = Path(directory) / 'contrast.cpp'
            source.write_text(FIXTURE + code)
            result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                     '-fconstexpr-ops-limit=100000000', '-fsyntax-only',
                                     '-I' + str(ROOT / 'main/hal/mosaico'), str(source)], capture_output=True, text=True)
            log = ROOT / 'artifacts/gauge-contrast-tests' / f'{self._testMethodName}.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, str(log))

    def test_codec_and_exact_source(self):
        self.compile(r'''
constexpr bool codec(){auto r=sample();j::Blob b{};c::Record out{};
 if(!j::encode(r,b)||!j::decode(b,r.mac,out)||out.source!=r.source||j::blobSize!=320||c::rawDigest(r.source)!=0x70a767be)return false;
 for(size_t i=0;i<b.size();++i){auto bad=b;bad[i]^=0x80;if(j::decode(bad,r.mac,out))return false;}
 // Recompute CRC: CRC alone must not admit source/schema/layout/padding aliases.
 for(size_t i=0;i<b.size()-4;++i){
  if((i>=19&&i<=20)||(i>=24&&i<36))continue; // valid states/counters covered separately
  auto bad=b;bad[i]^=0x80;j::bytes::put(bad.data()+316,j::bytes::crc32(bad.data(),316),4);
  if(j::decode(bad,r.mac,out))return false;
 }
 for(auto p:{4,127,255}){auto x=b;x[19]=uint8_t(p);j::bytes::put(x.data()+316,j::bytes::crc32(x.data(),316),4);if(j::decode(x,r.mac,out))return false;}
 for(auto a:{0,3,255}){auto x=b;x[20]=uint8_t(a);j::bytes::put(x.data()+316,j::bytes::crc32(x.data(),316),4);if(j::decode(x,r.mac,out))return false;}
 for(size_t i=0;i<c::rawSize;++i){auto x=r;x.source[i]^=1;if(c::shape(x)||j::encode(x,b))return false;}
 auto wrong=r.mac;wrong[5]^=1;if(j::decode(b,wrong,out))return false;
 r=sample();r.identity.build=3;if(c::shape(r))return false;r=sample();r.mac={};if(c::shape(r))return false;
 r=sample();r.generation=0;if(c::shape(r))return false;r=sample();r.epoch=0;if(c::shape(r))return false;
 return true;
}static_assert(codec());
''')

    def test_transition_chain_and_arm_switch(self):
        self.compile(r'''
constexpr bool chain(){auto original=sample();
 for(auto a:{c::Phase::Original,c::Phase::Pending,c::Phase::Applied,c::Phase::Restoring})
 for(auto b:{c::Phase::Original,c::Phase::Pending,c::Phase::Applied,c::Phase::Restoring}){
  auto x=original;x.phase=a;auto y=step(x,b);
  bool expected=(a==c::Phase::Original&&(b==c::Phase::Original||b==c::Phase::Pending))||
   (a==c::Phase::Pending&&(b==c::Phase::Applied||b==c::Phase::Restoring))||
   (a==c::Phase::Applied&&b==c::Phase::Restoring)||(a==c::Phase::Restoring&&b==c::Phase::Original);
  if(c::transition(x,y)!=expected)return false;
  y.arm=c::Arm::B;if(c::transition(x,y)!=(a==c::Phase::Original&&b==c::Phase::Original))return false;
 }
 auto y=step(original,c::Phase::Pending);y.epoch=original.epoch;if(c::transition(original,y))return false;
 y=step(original,c::Phase::Pending);y.generation+=1;if(c::transition(original,y))return false;
 original.generation=UINT32_MAX;y=step(original,c::Phase::Pending);if(c::transition(original,y))return false;
 return true;
}static_assert(chain());
''')

    def test_full131_ownership_third_values_and_deadline(self):
        self.compile(r'''
constexpr bool ownership(){auto r=sample();c::Observation o{r.source,true,true,true};
 if(c::classify(r,o)!=c::Image::Original||c::restoreRequired(r,o)||c::physicalUnitsTrusted)return false;
 const auto target=c::targetRaw(r);size_t changed=0;
 for(size_t i=0;i<c::recovery::fieldCount;++i){const auto f=c::recovery::layout[i];const auto n=c::rawOffset(i);bool diff=false;
  for(size_t k=0;k<f.width;++k)diff|=r.source[n+k]!=target[n+k];
  changed+=diff;
  auto pending=step(r,c::Phase::Pending);
  if(c::writable(pending,o,f.address,false)!=c::owned(f.address)||c::writable(r,o,f.address,false))return false;
  auto restoring=step(pending,c::Phase::Restoring);
  if(c::writable(restoring,o,f.address,true)!=c::owned(f.address))return false;
  auto unknown=o;unknown.raw[n]^=0x80;
  if(c::classify(r,unknown)!=c::Image::Unexpected||c::writable(restoring,unknown,0x929d,true))return false;
 }
 if(changed!=3)return false;
 r.arm=c::Arm::B;auto b=c::targetRaw(r);changed=0;
 for(size_t i=0;i<c::rawSize;++i)changed+=b[i]!=target[i];
 if(changed!=1)return false;
 for(auto p:{c::Phase::Pending,c::Phase::Applied,c::Phase::Restoring}){r.phase=p;if(!c::restoreRequired(r,o))return false;}
 c::Lease lease{};if(!c::beginLease(lease,c::Arm::C,100)||c::beginLease(lease,c::Arm::C,200)||
  !c::leaseActive(lease,c::Arm::C,720099)||c::leaseActive(lease,c::Arm::C,720100)||
  c::leaseActive(lease,c::Arm::B,101)||c::leaseActive(lease,c::Arm::C,99)||c::hostLeaseMs>660000)return false;
 return true;
}static_assert(ownership());
''')

    def test_storage_faults_capacity_and_source_retention(self):
        self.compile(r'''
constexpr bool storage(){auto r=sample();j::Workspace w{};Store s{};
 if(j::loadInto(s,r.mac,w)!=j::Status::Missing)return false;
 s.space=55;if(j::saveInto(s,r,admitted,w)!=j::Status::NoSpace||s.writes)return false;
 s.space=56;if(j::saveInto(s,r,admitted,w)!=j::Status::Ready)return false;
 const auto first=s.blobs[0];auto pending=step(r,c::Phase::Pending);
 for(unsigned fault=0;fault<3;++fault){auto broken=s;broken.commits=0;broken.failWrite=fault==0;broken.failCommit=fault==1;broken.failReadback=fault==2;
  const auto result=j::saveInto(broken,pending,admitted,w);
  if(result!=(fault==0?j::Status::WriteFailed:fault==1?j::Status::CommitFailed:j::Status::ReadbackFailed)||broken.blobs[0]!=first)return false;
  c::Record recovered{};if(!j::decode(broken.blobs[0],r.mac,recovered)||recovered.source!=c::sourceFixture)return false;
  // A failed commit/readback is NOT proof that Pending was never persisted.
  // Fresh startup must authenticate/load actual media, retaining source and
  // requiring restore even when the gauge itself still equals full Original.
  broken.failReadback=false;
  if(j::loadInto(broken,r.mac,w)!=j::Status::Ready)return false;
  const auto& boot=w.loaded.records[size_t(w.loaded.selected)];
  if(fault){c::Observation unchanged{r.source,true,true,true};
   if(boot.phase!=c::Phase::Pending||!c::restoreRequired(boot,unchanged)||boot.source!=c::sourceFixture)return false;}
  else if(boot.phase!=c::Phase::Original)return false;
 }
 // available_entries is not a perpetual restore blocker; no fake reclaimed credit.
 s.space=0;s.capacityOk=false;int calls=s.capacityCalls;
 if(j::saveInto(s,pending,admitted,w)!=j::Status::Ready||s.capacityCalls!=calls)return false;
 if(j::loadInto(s,r.mac,w)!=j::Status::Ready||w.loaded.selected!=1)return false;
 if(j::saveInto(s,w.loaded.records[1],admitted,w)!=j::Status::Rejected)return false;
 auto restoring=step(pending,c::Phase::Restoring);if(j::saveInto(s,restoring,admitted,w)!=j::Status::Ready)return false;
 auto restored=step(restoring,c::Phase::Original);if(j::saveInto(s,restored,admitted,w)!=j::Status::Ready)return false;
 if(j::loadInto(s,r.mac,w)!=j::Status::Ready||w.loaded.records[size_t(w.loaded.selected)].source!=c::sourceFixture)return false;
 auto corrupt=s;corrupt.blobs[0][0]^=1;if(j::loadInto(corrupt,r.mac,w)!=j::Status::Corrupt)return false;
 auto io=s;io.io=true;if(j::loadInto(io,r.mac,w)!=j::Status::IoError)return false;
 auto lone=s;lone.present={false,true};if(j::loadInto(lone,r.mac,w)!=j::Status::Ambiguous)return false;
 auto epoch=s;c::Record mismatch{};if(!j::decode(epoch.blobs[1],r.mac,mismatch))return false;
 mismatch.epoch+=100;if(!j::encode(mismatch,epoch.blobs[1]))return false;
 if(j::loadInto(epoch,r.mac,w)!=j::Status::Ambiguous)return false;
 return true;
}static_assert(storage());
''')

    def test_coordinator_all_breakpoints(self):
        self.compile(r'''
struct Backend {
 int failApply=-1,failRestore=-1,applyStep=0,restoreStep=0,keys=0,protectedCount=0,revoked=0;
 bool restoring=false,pending=false;
 constexpr bool check(bool r){restoring=r;int n=r?restoreStep++:applyStep++;return n!=(r?failRestore:failApply);}
 constexpr bool admit(bool r){return check(r);}
 constexpr bool intent(bool r){bool ok=check(r);if(ok&&!r)pending=true;return ok;}
 constexpr bool open(bool r){++keys;if(!r&&!pending)return false;return check(r);}
 constexpr bool observe(bool r){return check(r);}
 constexpr bool enter(){return check(restoring);}
 constexpr bool write(bool r){return check(r);}
 constexpr bool verify(bool r){return check(r);}
 constexpr bool exit(){return check(restoring);}
 constexpr bool seal(){return check(restoring);}
 constexpr bool final(bool r){return check(r);}
 constexpr bool complete(bool r){return check(r);}
 constexpr void revoke(){++revoked;}
 constexpr void protect(){++protectedCount;}
};
constexpr bool coordinator(){using namespace mosaico_gauge_contrast_trial;
 for(int i=0;i<=10;++i){Backend b{};b.failApply=i;auto result=apply(b);
  if(i==0){if(result!=Result::Blocked||b.keys)return false;}
  else if(i==1){if(result!=Result::FailedClosed||b.keys||b.restoreStep)return false;}
  else if(result!=Result::Restored||!b.pending||b.restoreStep!=11)return false;
 }
 for(int i=0;i<=10;++i){Backend b{};b.failApply=5;b.failRestore=i;
  if(apply(b)!=Result::FailedClosed||b.protectedCount!=1)return false;}
 Backend success{};if(apply(success)!=Result::Applied||success.restoreStep||!success.pending)return false;
 return true;
}static_assert(coordinator());
''')


if __name__ == '__main__':
    unittest.main()
