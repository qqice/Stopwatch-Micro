"""Atomic evidence envelope model; injected TEST hash is NOT cryptography."""
from pathlib import Path
import json
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
    def compile(self, body, both=True):
        directory = ROOT / 'artifacts/gauge-cold-envelope-tests'
        directory.mkdir(parents=True, exist_ok=True)
        for mode in (('legacy', 'workspace') if both else ('explicit',)):
            extra, code = EXTRA, body
            if mode == 'workspace':
                # Run EVERY original positive/negative assertion through the
                # explicit-workspace APIs too; adapters allocate only in tests.
                adapters = r'''
 template<class H> constexpr bool workspaceEncode(const m::Record& r,const v::Frames& f,uint8_t n,const m::Binding& b,const m::Digest& d,const H& h,q::Record& out){v::Workspace w{};return v::encode(r,f,n,b,d,h,out,w);}
 template<class H> constexpr bool workspaceDecode(const q::Record& r,const m::Binding& b,const m::Digest& d,const H& h,v::Decoded& out){v::Workspace w{};return v::decode(r,b,d,h,out,w);}
'''
                extra = extra.replace('v::encode(', 'workspaceEncode(').replace('v::decode(', 'workspaceDecode(')
                extra = extra.replace('struct NotCryptoTestHash', adapters + 'struct NotCryptoTestHash')
                policy = r'''
struct WorkspacePolicy {
 constexpr bool valid(const q::Record& r)const{v::Workspace w{};return v::Policy<NotCryptoTestHash>{binding(),trustedDigest(),{},&w}.valid(r);}
 constexpr bool initial(const q::Record& r)const{v::Workspace w{};return v::Policy<NotCryptoTestHash>{binding(),trustedDigest(),{},&w}.initial(r);}
 constexpr bool transition(const q::Record& a,const q::Record& b)const{v::Workspace w{};return v::Policy<NotCryptoTestHash>{binding(),trustedDigest(),{},&w}.transition(a,b);}
};
constexpr WorkspacePolicy envelopePolicy(){return {};}
'''
                extra = extra.replace('constexpr v::Policy<NotCryptoTestHash> envelopePolicy(){return {binding(),trustedDigest(),{}};}', policy)
                code = code.replace('v::encode(', 'workspaceEncode(').replace('v::decode(', 'workspaceDecode(')
            with tempfile.TemporaryDirectory(dir=directory) as tmp:
                source = Path(tmp) / 'envelope.cpp'
                source.write_text(FIXTURE + extra + code, encoding='utf-8')
                result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                         '-fconstexpr-ops-limit=100000000', '-fsyntax-only',
                                         '-I' + str(ROOT / 'main/hal/mosaico'), str(source)],
                                        capture_output=True, text=True)
                log = directory / (self._testMethodName + '.' + mode + '.log')
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


    def test_workspace_failure_outputs_aliases_and_reentrant_guard(self):
        self.compile(r'''
constexpr bool same(const q::Record& a,const q::Record& b){return a.sequence==b.sequence&&a.phase==b.phase&&a.nonce==b.nonce&&a.binding==b.binding&&a.size==b.size&&a.payload==b.payload;}
constexpr bool same(const v::Decoded& a,const v::Decoded& b){return m::sameRecord(a.metadata,b.metadata)&&a.frames==b.frames&&a.count==b.count&&a.rawHash==b.rawHash;}
constexpr bool test(){v::Workspace w{};const auto fs=frames();auto done=captured();done.evidenceDigest=bodyDigest(fs,2);const auto good=bundle(done,fs,2);
 q::Record out=bundle(armed());const auto original=out;
 if(v::encode(done,fs,2,binding(),trustedDigest(),NotCryptoTestHash{true},out,w)||!same(out,original)||w.busy)return false;
 if(v::encode(done,fs,3,binding(),trustedDigest(),NotCryptoTestHash{},out,w)||!same(out,original))return false;
 v::Decoded decoded{};if(!v::decode(good,binding(),trustedDigest(),NotCryptoTestHash{},decoded,w))return false;const auto before=decoded;
 auto bad=good;bad.payload[16]^=1;if(v::decode(bad,binding(),trustedDigest(),NotCryptoTestHash{},decoded,w)||!same(decoded,before)||w.busy)return false;
 if(v::decode(good,binding(),trustedDigest(),NotCryptoTestHash{true},decoded,w)||!same(decoded,before))return false;
 w.inner=good;const auto inner=w.inner;if(v::decode(w.inner,binding(),trustedDigest(),NotCryptoTestHash{},decoded,w)||!same(w.inner,inner)||!same(decoded,before))return false;
 w.staged=good;if(v::decode(w.staged,binding(),trustedDigest(),NotCryptoTestHash{},decoded,w)||!same(w.staged,good)||!same(decoded,before))return false;
 if(v::encode(done,fs,2,binding(),trustedDigest(),NotCryptoTestHash{},w.inner,w)||!same(w.inner,inner))return false;
 w.candidate=decoded;const auto candidate=w.candidate;
 if(v::decode(good,binding(),trustedDigest(),NotCryptoTestHash{},w.candidate,w)||!same(w.candidate,candidate))return false;
 if(v::decode(good,w.candidate.metadata.binding,trustedDigest(),NotCryptoTestHash{},decoded,w)||!same(decoded,before))return false;
 if(v::decode(good,binding(),w.candidate.rawHash,NotCryptoTestHash{},decoded,w)||!same(decoded,before))return false;
 if(v::encode(w.candidate.metadata,fs,2,binding(),trustedDigest(),NotCryptoTestHash{},out,w)||!same(out,original))return false;
 if(v::encode(done,w.candidate.frames,2,binding(),trustedDigest(),NotCryptoTestHash{},out,w)||!same(out,original))return false;
 w.busy=true;if(v::decode(good,binding(),trustedDigest(),NotCryptoTestHash{},decoded,w)||!same(decoded,before)||!w.busy)return false;
 w.busy=false;return true;
}static_assert(test());
''', both=False)

    def test_workspace_trusted_digest_and_binding_never_alias_overwritten_scratch(self):
        self.compile(r'''
constexpr bool equalOut(const v::Decoded& a,const v::Decoded& b){return m::sameRecord(a.metadata,b.metadata)&&a.frames==b.frames&&a.count==b.count&&a.rawHash==b.rawHash;}
constexpr bool test(){v::Workspace w{};auto bad=bundle(armed());bad.binding[0]^=1;
 v::Decoded out{};out.count=7;const auto before=out;const auto external=trustedDigest();
 if(v::decode(bad,binding(),external,NotCryptoTestHash{},out,w)||external!=trustedDigest()||!equalOut(out,before))return false;
 // Explicit reviewer counterexample: reject BEFORE inner.binding is replaced
 // with the bad outer binding, preserving the original trusted anchor exactly.
 w.inner.binding=trustedDigest();
 if(v::decode(bad,binding(),w.inner.binding,NotCryptoTestHash{},out,w)||w.inner.binding!=trustedDigest()||!equalOut(out,before)||w.busy)return false;
 m::Digest* anchors[]={&w.inner.binding,&w.staged.binding,
  &w.candidate.rawHash,&w.candidate.metadata.evidenceDigest,&w.candidate.metadata.binding.slotHashes[0],&w.candidate.metadata.binding.slotHashes[1],&w.candidate.metadata.binding.contractDigest,&w.candidate.metadata.binding.source131ReceiptDigest,
  &w.before.rawHash,&w.before.metadata.evidenceDigest,&w.before.metadata.binding.slotHashes[0],&w.before.metadata.binding.slotHashes[1],&w.before.metadata.binding.contractDigest,&w.before.metadata.binding.source131ReceiptDigest,
  &w.after.rawHash,&w.after.metadata.evidenceDigest,&w.after.metadata.binding.slotHashes[0],&w.after.metadata.binding.slotHashes[1],&w.after.metadata.binding.contractDigest,&w.after.metadata.binding.source131ReceiptDigest};
 q::Record encoded=bundle(handed());const auto original=encoded;
 for(auto* anchor:anchors){*anchor=trustedDigest();
  if(v::decode(bad,binding(),*anchor,NotCryptoTestHash{},out,w)||*anchor!=trustedDigest()||!equalOut(out,before)||w.busy)return false;
  if(v::encode(armed(),{},0,binding(),*anchor,NotCryptoTestHash{},encoded,w)||*anchor!=trustedDigest()||encoded.payload!=original.payload||encoded.binding!=original.binding||encoded.sequence!=original.sequence||w.busy)return false;
 }
 m::Binding* bindings[]={&w.candidate.metadata.binding,&w.before.metadata.binding,&w.after.metadata.binding};
 for(auto* expected:bindings){*expected=binding();
  if(v::decode(bad,*expected,trustedDigest(),NotCryptoTestHash{},out,w)||!m::sameBinding(*expected,binding())||!equalOut(out,before))return false;
  if(v::encode(armed(),{},0,*expected,trustedDigest(),NotCryptoTestHash{},encoded,w)||!m::sameBinding(*expected,binding())||encoded.payload!=original.payload)return false;
 }
 return true;
}static_assert(test());
''', both=False)

    def test_workspace_policy_preserves_both_decodes_and_continuous_chain(self):
        self.compile(r'''
constexpr bool test(){v::Workspace w{};const v::Policy<NotCryptoTestHash> policy{binding(),trustedDigest(),{},&w};
 const auto fs=frames();const auto a=bundle(armed()),h=bundle(handed()),claim=bundle(claimed(),fs,1);
 auto done=captured();done.evidenceDigest=bodyDigest(fs,2);const auto full=bundle(done,fs,2);
 if(!policy.initial(a)||!policy.valid(h)||!policy.transition(a,h)||!policy.transition(h,claim)||!policy.transition(claim,full))return false;
 if(w.before.count!=1||w.after.count!=2||w.before.metadata.phase!=m::Phase::CaptureClaimed||w.after.metadata.phase!=m::Phase::Captured)return false;
 if(policy.transition(full,claim)||policy.transition(a,full)||w.busy)return false;
 if(!policy.transition(claim,full)||w.before.count!=1||w.after.count!=2)return false;
 // Hash failures/negative transitions never leave the exclusive workspace busy.
 const v::Policy<NotCryptoTestHash> broken{binding(),trustedDigest(),{true},&w};
 if(broken.transition(claim,full)||w.busy)return false;
 w.busy=true;if(policy.valid(a)||policy.initial(a)||policy.transition(a,h)||!w.busy)return false;
 return true;
}static_assert(test());
''', both=False)

    def test_workspace_nested_hash_callback_requires_separate_workspace(self):
        self.compile(r'''
struct NestedHash {v::Workspace* nested;const q::Record* record;unsigned* attempts;unsigned* accepted;
 constexpr bool operator()(const uint8_t* p,size_t n,m::Digest& digest)const{
  ++*attempts;v::Decoded out{};if(v::decode(*record,binding(),trustedDigest(),NotCryptoTestHash{},out,*nested))++*accepted;
  return NotCryptoTestHash{}(p,n,digest);
 }};
constexpr bool test(){const auto fs=frames();auto done=captured();done.evidenceDigest=bodyDigest(fs,2);const auto full=bundle(done,fs,2);const auto empty=bundle(armed());
 v::Workspace one{},two{};v::Decoded out{};unsigned attempts=0,accepted=0;
 if(!v::decode(full,binding(),trustedDigest(),NestedHash{&one,&empty,&attempts,&accepted},out,one)||attempts!=1||accepted||one.busy)return false;
 attempts=accepted=0;if(!v::decode(full,binding(),trustedDigest(),NestedHash{&two,&empty,&attempts,&accepted},out,one)||attempts!=1||accepted!=1||one.busy||two.busy)return false;
 return out.count==2;
}static_assert(test());
''', both=False)

    def test_rv32_explicit_workspace_stack_frames_and_evidence_budget(self):
        directory = ROOT / 'artifacts/gauge-cold-envelope-tests'
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / 'stack.cpp'
        source.write_text(r'''
#include "mosaico_gauge_cold_envelope.h"
namespace v=mosaico_gauge_cold_envelope;
extern bool trustedHash(const uint8_t*,size_t,v::trial::Digest&);
struct Hash {bool operator()(const uint8_t* p,size_t n,v::trial::Digest& d)const{return trustedHash(p,n,d);}};
extern "C" bool wsEncode(const v::trial::Record& r,const v::Frames& f,uint8_t n,const v::trial::Binding& b,const v::trial::Digest& d,v::store::Record& out,v::Workspace& w){return v::encode(r,f,n,b,d,Hash{},out,w);}
extern "C" bool wsDecode(const v::store::Record& r,const v::trial::Binding& b,const v::trial::Digest& d,v::Decoded& out,v::Workspace& w){return v::decode(r,b,d,Hash{},out,w);}
extern "C" bool wsValid(const v::Policy<Hash>& p,const v::store::Record& r){return p.valid(r);}
extern "C" bool wsInitial(const v::Policy<Hash>& p,const v::store::Record& r){return p.initial(r);}
extern "C" bool wsTransition(const v::Policy<Hash>& p,const v::store::Record& a,const v::store::Record& b){return p.transition(a,b);}
''', encoding='utf-8')
        result = subprocess.run([str(COMPILER), '-std=c++20', '-Os', '-Wall', '-Wextra', '-Werror',
            '-fstack-usage', '-c', '-I' + str(ROOT / 'main/hal/mosaico'), str(source),
            '-o', str(directory / 'stack.o')], capture_output=True, text=True)
        log = directory / 'stack-compile.log'
        log.write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, str(log))
        frames = []
        for line in (directory / 'stack.su').read_text().splitlines():
            name, size, category = line.rsplit('\t', 2)
            frames.append(dict(function=name, bytes=int(size), kind=category))
        production = [entry for entry in frames if 'convenience' not in entry['function']]
        self.assertTrue(production)
        self.assertTrue(all(entry['kind'] == 'static' and entry['bytes'] <= 768 for entry in production))
        self.assertTrue(any('decodeInto' in entry['function'] for entry in production))
        self.assertTrue(any('evidence::decode' in entry['function'] for entry in production))
        report = dict(compiler=str(COMPILER), flags='-std=c++20 -Os -fstack-usage',
            per_function_limit_bytes=768, production_max_frame=max(e['bytes'] for e in production),
            frames=frames, whole_chain_limit_768_claimed=False,
            cumulative_example='Policy64+decodeInto96+frameRules48+existing evidenceDecode704+put48=960B; add actual SHA/NAND/runtime callers separately',
            production_requires_nonnull_workspace=True,
            null_workspace_fallback='compatibility only; large stack explicitly excluded from production bound')
        (directory / 'stack-report.json').write_text(json.dumps(report, indent=2))

if __name__ == '__main__':
    unittest.main()
