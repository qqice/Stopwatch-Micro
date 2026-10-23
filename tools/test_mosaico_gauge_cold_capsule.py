"""Offline typed cold metadata capsule tests. No raw evidence body or hardware IO."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_mosaico_gauge_cold_trial import FIXTURE as TRIAL_FIXTURE, COMPILER
from test_mosaico_gauge_cold_store import FIXTURE as STORE_FIXTURE

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = TRIAL_FIXTURE + r'''
#include "mosaico_gauge_cold_capsule.h"
namespace k=mosaico_gauge_cold_capsule;
namespace q=mosaico_gauge_cold_store;
constexpr m::Digest trustedDigest(){m::Digest d{};for(unsigned i=0;i<32;++i)d[i]=uint8_t(i+1);return d;}
constexpr k::Policy policy(){return {binding(),trustedDigest()};}
constexpr q::Record pack(const m::Record& r){q::Record out{};k::encode(r,r.binding,trustedDigest(),out);return out;}
'''
# Reuse the existing store's bounded in-memory transport, not its toy Policy.
FIXTURE += STORE_FIXTURE[STORE_FIXTURE.index('struct IO {'):].replace('c::', 'q::')


class ColdCapsuleTests(unittest.TestCase):
    def compile(self, body):
        directory = ROOT / 'artifacts/gauge-cold-capsule-tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            source = Path(temporary) / 'capsule.cpp'
            source.write_text(FIXTURE + body, encoding='utf-8')
            result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
                '-fconstexpr-ops-limit=100000000', '-fsyntax-only',
                '-I' + str(ROOT / 'main/hal/mosaico'), str(source)], capture_output=True, text=True)
            log = directory / (self._testMethodName + '.log')
            log.write_text(result.stdout + result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, 0, str(log))

    def test_roundtrip_all_phases_and_metadata_fields(self):
        self.compile(r'''
constexpr bool test(){auto terminal=restoring();++terminal.sequence;terminal.phase=m::Phase::Restored;
 auto blocked=restoring();++blocked.sequence;blocked.phase=m::Phase::BlockedUnknown;
 for(const auto& r:{armed(),handed(),claimed(),captured(),captured(m::CaptureResult::KnownTimeout),restoring(),terminal,blocked}){
  q::Record wire{};m::Record out{};if(!k::encode(r,binding(),trustedDigest(),wire)||wire.size!=288||!k::decode(wire,binding(),trustedDigest(),out)||!m::sameRecord(r,out)||!policy().valid(wire))return false;
  for(size_t i=k::WireSize;i<wire.payload.size();++i)if(wire.payload[i])return false;
 }
 auto r=armed();r.armedMs=0x0102030405060708ULL;auto w=pack(r);
 if(w.payload[232]!=8||w.payload[233]!=7||w.payload[239]!=1||w.payload[12]!=1||w.payload[16]!=0x24)return false;
 return k::WireSize==288&&k::BindingWireSize==188;
}static_assert(test());
''')

    def test_complete_expected_binding_and_digest_not_first_byte(self):
        self.compile(r'''
constexpr bool test(){const auto good=pack(armed());m::Record out{};
 // Every byte of every complete Binding field must be checked, not a prefix.
 for(size_t i=16;i<196;++i){auto bad=good;bad.payload[i]^=1;if(k::decode(bad,binding(),trustedDigest(),out)||policy().valid(bad))return false;}
 for(size_t i=0;i<32;++i){auto d=trustedDigest();d[i]^=1;if(k::decode(good,binding(),d,out))return false;auto bad=good;bad.binding[i]^=1;if(policy().valid(bad))return false;}
 auto wrong=binding();wrong.source131ReceiptDigest[31]=44;if(k::decode(good,wrong,trustedDigest(),out))return false;
 wrong=binding();wrong.slotHashes[1][31]=99;if(k::decode(good,wrong,trustedDigest(),out))return false;
 auto r=armed();r.binding=wrong;q::Record wire{};if(k::encode(r,binding(),trustedDigest(),wire))return false;
 auto invalid=policy();invalid.trustedBindingDigest={};return !invalid.valid(good);
}static_assert(test());
''')

    def test_canonical_binding_sha_input_both_recovery_modes(self):
        self.compile(r'''
constexpr bool test(){k::BindingBytes bytes{};auto b=binding();if(!k::encodeBinding(b,bytes))return false;
 if(bytes[0]!='G'||bytes[1]!='C'||bytes[2]!='B'||bytes[3]!='1'||bytes[4]!=1||bytes[6]!=188||bytes[8]!=0x24)return false;
 auto w=pack(armed());for(size_t i=0;i<180;++i)if(bytes[8+i]!=w.payload[16+i])return false;
 // Independently change every valid receipt/hash byte; canonical input changes.
 for(unsigned i=0;i<32;++i){auto x=b;x.slotHashes[1][i]^=8;k::BindingBytes other{};if(!k::encodeBinding(x,other)||other==bytes)return false;
  x=b;x.source131ReceiptDigest[i]^=8;if(!k::encodeBinding(x,other)||other==bytes)return false;}
 b.recovery=m::RecoveryBinding::ContractDigest;b.slotHashes={};for(unsigned i=0;i<32;++i)b.contractDigest[i]=uint8_t(32-i);
 auto r=armed();r.binding=b;q::Record outer{};m::Record out{};
 if(!k::encodeBinding(b,bytes)||!k::encode(r,b,trustedDigest(),outer)||!k::decode(outer,b,trustedDigest(),out)||!m::sameRecord(r,out))return false;
 for(unsigned i=0;i<32;++i){auto x=outer;x.payload[128+i]^=1;if(k::decode(x,b,trustedDigest(),out))return false;}
 b.slotHashes[0][31]=1;return !k::encodeBinding(b,bytes);
}static_assert(test());
''')

    def test_shell_crosschecks_sizes_versions_booleans_and_tail(self):
        self.compile(r'''
constexpr bool test(){const auto good=pack(armed());m::Record out=handed();const auto untouched=out;
 for(unsigned kind=0;kind<11;++kind){auto b=good;
  if(kind==0)++b.sequence;
  if(kind==1)b.sequence=uint64_t(UINT32_MAX)+1;
  if(kind==2)++b.phase;
  if(kind==3)b.nonce[15]^=1;
  if(kind==4)--b.size;
  if(kind==5)++b.size;
  if(kind==6)b.payload[230]=1;
  if(kind==7)b.payload[231]=1;
  if(kind==8)b.payload[1535]=1;
  if(kind==9)b.payload[212]=2;
  if(kind==10)b.payload[229]=2;
  if(k::decode(b,binding(),trustedDigest(),out)||!m::sameRecord(out,untouched))return false;
 }
 for(auto offset:{0,4,6,8,10,11,27}){auto b=good;b.payload[offset]=255;if(k::decode(b,binding(),trustedDigest(),out))return false;}
 for(auto offset:{196,211}){auto b=good;b.payload[offset]=0; // armed boot is no longer valid only when first nonzero byte cleared
  if(offset==196&&k::decode(b,binding(),trustedDigest(),out))return false;}
 return true;
}static_assert(test());
''')

    def test_capture_and_boot_fields_canonical_no_raw_evidence_claim(self):
        self.compile(r'''
constexpr bool test(){const auto good=pack(captured());m::Record out{};
 for(size_t i=196;i<288;++i){if(i==230||i==231)continue;auto b=good;b.payload[i]^=1;
  // Valid data changes can roundtrip, but are never silently omitted.
  if(k::decode(b,binding(),trustedDigest(),out)&&m::sameRecord(out,captured()))return false;
 }
 auto r=armed();r.evidenceDigest[31]=1;q::Record w{};if(k::encode(r,binding(),trustedDigest(),w))return false;
 r=armed();r.phase=m::Phase::BlockedUnknown;r.evidenceDigest[31]=1;if(k::encode(r,binding(),trustedDigest(),w))return false;
 r=captured();r.evidenceDigest={};if(k::encode(r,binding(),trustedDigest(),w))return false;
 r=claimed();r.claimedBoot.verified=false;if(k::encode(r,binding(),trustedDigest(),w))return false;
 r=armed();r.claimedBoot.nonce[15]=1;if(k::encode(r,binding(),trustedDigest(),w))return false;
 r=armed();r.capture=m::CaptureResult(255);return !k::encode(r,binding(),trustedDigest(),w);
}static_assert(test());
''')

    def test_production_policy_forwards_initial_and_typed_transition(self):
        self.compile(r'''
constexpr bool test(){const auto a=pack(armed()),h=pack(handed()),c=pack(claimed()),e=pack(captured()),r=pack(restoring());
 if(!policy().initial(a)||policy().initial(h)||!policy().transition(a,h)||!policy().transition(h,c)||!policy().transition(c,e)||!policy().transition(e,r))return false;
 if(policy().transition(a,c)||policy().transition(c,h)||policy().transition(a,a))return false;
 auto altered=handed();altered.armedMs++;auto w=pack(altered);if(policy().transition(a,w))return false;
 altered=handed();altered.sequence+=2;w=pack(altered);if(policy().transition(a,w))return false;
 altered=handed();altered.binding.originalGctEpoch++;w=pack(altered);return !policy().valid(w)&&!policy().transition(a,w);
}static_assert(test());
''')

    def test_blocked_unknown_capture_enums_recomputed_crc_rejected(self):
        self.compile(r'''
constexpr bool test(){auto blocked=armed();++blocked.sequence;blocked.phase=m::Phase::BlockedUnknown;
 const auto a=pack(armed()),b=pack(blocked);const auto p=policy();
 if(!p.valid(b)||!p.transition(a,b))return false;
 for(auto unknown:{8,9,255}){
  auto bad=blocked;bad.capture=m::CaptureResult(unknown);bad.evidenceDigest[31]=77;q::Record out{};
  if(k::canonical(bad)||k::encode(bad,binding(),trustedDigest(),out))return false;
  IO io;q::Scratch scratch{};if(q::save(io,scratch,a,p,true)!=q::Code::Ready)return false;
  auto forged=b;forged.payload[11]=uint8_t(unknown);forged.payload[287]=77;
  // Outer storage shape/CRC are deliberately valid: production typed Policy
  // must reject the unknown capture value rather than trust the page checksum.
  if(!q::encode(forged,io.pages[9])||p.valid(forged)||p.transition(a,forged))return false;
  q::nand::put(io.pages[9],2044,q::nand::crc(io.pages[9],2044),4);
  if(q::load(io,scratch,p).code!=q::Code::Corrupt)return false;
 }
 return true;
}static_assert(test());
''')

    def test_actual_store_chain_faults_and_pages8_9_with_production_policy(self):
        self.compile(r'''
constexpr bool test(){IO io;const auto protectedPages=io.pages;q::Scratch scratch{};const auto p=policy();
 const auto a=pack(armed());if(q::save(io,scratch,a,p,false)!=q::Code::Rejected||io.writes)return false;
 for(const auto& next:{a,pack(handed()),pack(claimed()),pack(captured()),pack(restoring())}){
  if(q::save(io,scratch,next,p,true)!=q::Code::Ready)return false;
  auto loaded=q::load(io,scratch,p);if(loaded.code!=q::Code::Ready||scratch.records[unsigned(loaded.selected)].payload!=next.payload)return false;
 }
 for(unsigned i=0;i<8;++i)if(io.pages[i]!=protectedPages[i])return false;
 // A changed payload remains rejected even after outer CRC is recomputed.
 auto page=io.pages[8];page[q::PayloadOffset+16+175]^=1;q::nand::put(page,2044,q::nand::crc(page,2044),4);io.pages[8]=page;
 return q::load(io,scratch,p).code==q::Code::Corrupt;
}static_assert(test());
''')


if __name__ == '__main__':
    unittest.main()
