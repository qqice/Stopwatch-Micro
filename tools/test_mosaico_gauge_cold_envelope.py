"""Atomic evidence envelope model; injected TEST hash is NOT cryptography."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_mosaico_gauge_cold_capsule import FIXTURE, COMPILER

ROOT = Path(__file__).resolve().parents[1]
EXTRA = r'''
#include "mosaico_gauge_cold_envelope.h"
namespace v=mosaico_gauge_cold_envelope;
struct NotCryptoTestHash {
 bool fail=false;
 constexpr bool operator()(const uint8_t* p,size_t n,m::Digest& out)const{
  if(fail)return false;
  uint32_t h=2166136261;
  for(size_t i=0;i<n;++i)h=(h^p[i])*16777619;
  for(size_t i=0;i<32;++i)out[i]=uint8_t((h>>(8*(i%4)))^i);
  return true;
 }
};
constexpr v::Frames frames(bool failure=false){v::Frames fs{};v::evidence::Frame f;
 f.bootNonce=claimed().claimedBoot.nonce;f.kind=v::evidence::Kind::Initial;
 f.standard.sampledUs=1000;f.standard.validMask=mosaico_gauge_status::AllRaw;
 f.engine.sampledUs=2000;f.engine.validMask=0x1fff;f.engine.echo73=0x73;f.engine.echo76=0x76;f.engine.echo02=2;
 f.engine.rawIdentity[0]=2;f.engine.rawIdentity[1]=0x20;f.engine.rawIdentity[3]=3;f.engine.rawIdentity[5]=4;
 v::evidence::encode(f,fs[0]);f.kind=failure?v::evidence::Kind::Failure:v::evidence::Kind::Final;
 f.standard.sampledUs=30000;f.engine.sampledUs=40000;f.error=failure?0xdeadbeef:0;v::evidence::encode(f,fs[1]);return fs;
}
constexpr m::Digest bodyDigest(const v::Frames& fs,uint8_t count){q::Record scratch{};m::Digest d{};
 for(size_t i=0;i<count;++i)for(size_t j=0;j<256;++j)scratch.payload[v::RawOffset+i*256+j]=fs[i][j];
 v::rawDigest(scratch,count,NotCryptoTestHash{},d);return d;
}
constexpr q::Record bundle(const m::Record& r,const v::Frames& fs={},uint8_t count=0){q::Record w{};
 v::encode(r,fs,count,binding(),trustedDigest(),NotCryptoTestHash{},w);return w;}
constexpr v::Policy<NotCryptoTestHash> envelopePolicy(){return {binding(),trustedDigest(),{}};}
'''


class ColdEnvelopeTests(unittest.TestCase):
    def compile(self, body):
        directory = ROOT / 'artifacts/gauge-cold-envelope-tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            source = Path(tmp) / 'envelope.cpp'
            source.write_text(FIXTURE + EXTRA + body, encoding='utf-8')
            result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                     '-fconstexpr-ops-limit=100000000', '-fsyntax-only',
                                     '-I' + str(ROOT / 'main/hal/mosaico'), str(source)],
                                    capture_output=True, text=True)
            log = directory / (self._testMethodName + '.log')
            log.write_text(result.stdout + result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, 0, str(log))

    def test_atomic_claim_prefix_capture_and_restore_chain(self):
        self.compile(r'''
constexpr bool test(){IO io;q::Scratch s;const auto policy=envelopePolicy();const auto fs=frames();
 auto a=bundle(armed());if(q::save(io,s,a,policy,true)!=q::Code::Ready)return false;
 auto h=bundle(handed());if(q::save(io,s,h,policy,true)!=q::Code::Ready)return false;
 auto c=bundle(claimed(),fs,1);if(q::save(io,s,c,policy,true)!=q::Code::Ready)return false;
 v::Decoded decoded{};if(!v::decode(c,binding(),trustedDigest(),NotCryptoTestHash{},decoded)||decoded.count!=1||decoded.metadata.capture!=m::CaptureResult::None)return false;
 auto done=captured();done.evidenceDigest=bodyDigest(fs,2);auto full=bundle(done,fs,2);
 if(q::save(io,s,full,policy,true)!=q::Code::Ready)return false;
 auto restore=done;++restore.sequence;restore.phase=m::Phase::Restoring;
 if(q::save(io,s,bundle(restore,fs,2),policy,true)!=q::Code::Ready)return false;
 ++restore.sequence;restore.phase=m::Phase::Restored;
 if(q::save(io,s,bundle(restore,fs,2),policy,true)!=q::Code::Ready)return false;
 return io.writes==6&&v::RawOffset+512<=q::PayloadMax;
}static_assert(test());
''')

    def test_digest_bytes_counts_and_durable_prefix_not_mutable(self):
        self.compile(r'''
constexpr bool test(){auto fs=frames();auto done=captured();done.evidenceDigest=bodyDigest(fs,2);const auto good=bundle(done,fs,2);auto policy=envelopePolicy();
 if(!policy.valid(good))return false;
 for(auto at:{0U,4U,6U,8U,10U,12U,13U,16U,47U,48U,v::RawOffset,v::RawOffset+256}){auto bad=good;bad.payload[at]^=1;if(policy.valid(bad))return false;}
 auto missing=good;missing.size-=256;for(size_t i=missing.size;i<missing.payload.size();++i)missing.payload[i]=0;if(policy.valid(missing))return false;
 const auto claim=bundle(claimed(),fs,1);auto changed=fs;v::evidence::Frame f{};
 v::evidence::decode(changed[0],f);++f.standard.rawSoc;v::evidence::encode(f,changed[0]);
 done.evidenceDigest=bodyDigest(changed,2);const auto changedDone=bundle(done,changed,2);
 if(!policy.valid(changedDone)||policy.transition(claim,changedDone))return false;
 auto metaOnly=pack(captured());if(policy.valid(metaOnly))return false;
 v::Decoded d{};return !v::decode(good,binding(),trustedDigest(),NotCryptoTestHash{true},d);
}static_assert(test());
''')

    def test_failure_raw_error_retained_and_success_cannot_hide_bad_reads(self):
        self.compile(r'''
constexpr bool test(){const auto fs=frames(true);auto failed=captured(m::CaptureResult::KnownReadFailure);failed.evidenceDigest=bodyDigest(fs,2);
 const auto w=bundle(failed,fs,2);v::Decoded out{};
 if(!v::decode(w,binding(),trustedDigest(),NotCryptoTestHash{},out))return false;
 v::evidence::Frame raw{};if(!v::evidence::decode(out.frames[1],raw)||raw.error!=0xdeadbeef)return false;
 auto good=frames();v::evidence::decode(good[1],raw);raw.engine.validMask=0;v::evidence::encode(raw,good[1]);
 auto done=captured();done.evidenceDigest=bodyDigest(good,2);q::Record rejected{};
 if(v::encode(done,good,2,binding(),trustedDigest(),NotCryptoTestHash{},rejected))return false;
 good=frames();v::evidence::decode(good[1],raw);raw.standard.sampledUs=1500;v::evidence::encode(raw,good[1]);done.evidenceDigest=bodyDigest(good,2);
 return !v::encode(done,good,2,binding(),trustedDigest(),NotCryptoTestHash{},rejected);
}static_assert(test());
''')

    def test_interrupted_boot_and_blocked_prefix_keep_original_raw(self):
        self.compile(r'''
constexpr bool test(){const auto policy=envelopePolicy();auto fs=frames(true);v::evidence::Frame f{};
 const auto originalClaim=bundle(claimed(),fs,1);
 v::evidence::decode(fs[1],f);f.bootNonce[0]=12;f.standard.sampledUs=1;f.engine.sampledUs=2;v::evidence::encode(f,fs[1]);
 auto newBoot=nextBoot();newBoot.boot.nonce[0]=12;
 auto interrupted=m::interruptedEvidence(claimed(),newBoot,bodyDigest(fs,2));
 if(!interrupted.hasNext)return false;
 const auto failed=bundle(interrupted.next,fs,2);
 if(!policy.valid(failed)||!policy.transition(originalClaim,failed))return false;
 auto blocked=claimed();++blocked.sequence;blocked.phase=m::Phase::BlockedUnknown;
 const auto preserved=bundle(blocked,fs,1);
 if(!policy.valid(preserved)||!policy.transition(originalClaim,preserved))return false;
 auto drop=bundle(blocked);if(policy.transition(originalClaim,drop))return false;
 newBoot.startupKind=m::StartupKind::Software;
 v::Frames rejectedFrames{};rejectedFrames[0]=fs[1];
 const auto rejection=m::rejectedStartupEvidence(handed(),newBoot,bodyDigest(rejectedFrames,1));
 if(!rejection.hasNext)return false;
 return policy.valid(bundle(rejection.next,rejectedFrames,1));
}static_assert(test());
''')

    def test_final_frame_must_fit_immutable_claim_budget(self):
        self.compile(r'''
constexpr bool test(){auto fs=frames();auto done=captured();v::evidence::Frame f{};q::Record out{};
 v::evidence::decode(fs[1],f);
 for(auto time:{9999ULL,30010000ULL,59000000ULL}){
  f.standard.sampledUs=f.engine.sampledUs=time;v::evidence::encode(f,fs[1]);done.evidenceDigest=bodyDigest(fs,2);
  if(v::encode(done,fs,2,binding(),trustedDigest(),NotCryptoTestHash{},out))return false;
 }
 return true;
}static_assert(test());
''')

    def test_public_digest_helper_rejects_count_before_hasher(self):
        self.compile(r'''
struct CountHash {unsigned* called;constexpr bool operator()(const uint8_t*,size_t,m::Digest&)const{++*called;return true;}};
constexpr bool test(){q::Record r{};m::Digest d{};unsigned called=0;
 for(auto count:{3,5,255})if(v::rawDigest(r,uint8_t(count),CountHash{&called},d))return false;
 return called==0&&!m::nonzero(d);
}static_assert(test());
''')


if __name__ == '__main__':
    unittest.main()
