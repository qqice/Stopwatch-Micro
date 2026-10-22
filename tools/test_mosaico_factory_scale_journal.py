"""OFFLINE GSJ3 compile-time codec tests; real full131 fixture remains PRIVATE.

No NVS backend, signed-image attestation, hardware admission or device writes.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_mosaico_factory_scale_model import ROOT, PRIVATE, EXPECTED, COMPILER

OUT=ROOT/'.artifacts/private/mosaico/factory-scale-journal-tests'
FIXTURE=r'''
#include "mosaico_factory_scale_journal_model.h"
namespace j=mosaico_factory_scale_journal;
namespace old=mosaico_gauge_scale_journal;
namespace f=mosaico_factory_scale;
namespace r=mosaico_gauge_recovery;
constexpr j::Record sample(){j::Record out{};out.profile=f::Profile::Factory3000RollbackToNominal65Virtual130;
out.mac={0x30,0xed,0xa0,0xff,0xfe,0xf4};auto& s=out.snapshot;
s.identity={0x220,3,4};s.binding=old::binding(out.mac);s.layoutId=r::Layout::Public131V1;
s.count=131;s.generation=1;s.epoch=10;s.scale=r::Scale::Two;s.phase=r::Phase::Original;
constexpr r::Field fields[]={ROWS};
for(unsigned i=0;i<131;++i){s.fields[i]=fields[i];for(const auto& c:f::changes){if(c.address!=s.fields[i].address)continue;
for(unsigned k=0;k<c.width;++k)s.fields[i].target[k]=uint8_t(c.after>>(8*(c.width-k-1)));}}
return out;}
constexpr void repair(j::Blob& b){old::put(b.data()+508,old::crc32(b.data(),508),4);}
constexpr j::Record next(j::Record a,r::Phase phase){++a.snapshot.generation;++a.snapshot.epoch;a.snapshot.phase=phase;return a;}
static_assert(!j::readyForHardware&&!j::durableBackendImplemented);
static_assert(j::blobSize==512&&f::schema==3);
static_assert(f::closedImageTemplate(sample().snapshot));
'''


class FactoryScaleJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PRIVATE.exists():raise unittest.SkipTest('real private complete131 fixture unavailable')
        if hashlib.sha256(PRIVATE.read_bytes()).hexdigest()!=EXPECTED:
            raise AssertionError('private fixture SHA256 disagreement')
        data=json.loads(PRIVATE.read_bytes())
        if data.get('complete') is not True or len(data.get('fields',[]))!=131:
            raise AssertionError('private fixture not complete131')
        rows=[]
        for field in data['fields']:
            raw=bytes.fromhex(field['rawBE']).ljust(4,b'\0')
            rows.append('{0x%s,%d,{%s},{%s}}'%(field['address'],field['width'],','.join(map(str,raw)),','.join(map(str,raw))))
        cls.fixture=FIXTURE.replace('ROWS',','.join(rows))

    def compile(self, code):
        OUT.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.codec-',dir=OUT) as td:
            src=Path(td)/'test.cpp';src.write_text(self.fixture+code)
            result=subprocess.run([str(COMPILER),'-std=c++20','-Wall','-Wextra','-Werror',
                '-fconstexpr-ops-limit=200000000','-fsyntax-only','-I'+str(ROOT/'main/hal/mosaico'),str(src)],capture_output=True,text=True)
            log=OUT/(self._testMethodName+'.log');log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_roundtrip_schema_separation_all_phases_and_no_external_proof_fact(self):
        self.compile(r'''
constexpr bool test(){const auto a=sample();j::Workspace work{};j::Blob b{},canonical{};j::Record decoded{};
if(!j::encode(a,b,work)||b[3]!='3'||old::get(b.data()+4,2)!=3||b[21]!=1||!j::decode(b,a.mac,decoded,work)||!j::sameRecoveryImage(a,decoded))return false;
const auto before=b;if(!j::decode(b,a.mac,decoded,work)||b!=before)return false;
if(!j::encode(decoded,canonical,work)||canonical!=b)return false;
work.bytes=b;const auto aliasBefore=work.bytes;
if(j::decode(work.bytes,a.mac,decoded,work)||work.bytes!=aliasBefore)return false;
old::Record oldOut{};if(old::decode(b,a.mac,oldOut))return false;
old::Record oldRecord{a.mac,a.snapshot};old::Blob oldBytes{};
if(!old::encode(oldRecord,oldBytes)||j::decode(oldBytes,a.mac,decoded,work))return false;
for(auto phase:{r::Phase::Original,r::Phase::Pending,r::Phase::Applied,r::Phase::Restoring}){
auto state=a;state.snapshot.phase=phase;if(!j::encode(state,b,work)||!j::decode(b,a.mac,decoded,work)||decoded.snapshot.phase!=phase)return false;}
auto wrongMac=a.mac;wrongMac[5]^=1;if(j::decode(b,wrongMac,decoded,work))return false;
// Wire data cannot manufacture either signed-image/complete-image proof.
f::Envelope e{3,f::Profile::Factory3000RollbackToNominal65Virtual130,f::factorySourceCrc,f::candidateTargetCrc,false,false};
return !f::closedProfile(e,decoded.snapshot);
}
static_assert(test());
''')

    def test_all512_single_byte_corruption_and_repaired_header_aliases(self):
        self.compile(r'''
constexpr bool corrupt(unsigned index){j::Workspace w{};j::Blob b{};j::Record out{};const auto a=sample();
if(!j::encode(a,b,w)){return false;}b[index]^=1;return !j::decode(b,a.mac,out,w);}
ASSERT_CORRUPTION
constexpr bool header(unsigned offset){j::Workspace w{};j::Blob b{};j::Record out{};const auto a=sample();
if(!j::encode(a,b,w)){return false;}b[offset]^=0x80;repair(b);return !j::decode(b,a.mac,out,w);}
ASSERT_HEADERS
'''.replace('ASSERT_CORRUPTION','\n'.join(f'static_assert(corrupt({i}));' for i in range(512)))
            .replace('ASSERT_HEADERS','\n'.join(f'static_assert(header({i}));' for i in
                (0,1,2,3,4,5,6,7,8,13,14,15,16,17,18,19,20,21,22,23,36,37,38,39,40,44,48,49,50,51,*range(52,64)))))

    def test_repaired_zero_generation_epoch_profile_and_shape(self):
        self.compile(r'''
constexpr bool test(){const auto a=sample();j::Workspace w{};j::Record out{};j::Blob b{};
for(unsigned mode=0;mode<8;++mode){if(!j::encode(a,b,w))return false;
switch(mode){case 0:old::put(b.data()+24,0,4);break;case 1:old::put(b.data()+28,0,8);break;
case 2:b[21]=0;break;case 3:b[21]=2;break;case 4:b[3]='2';old::put(b.data()+4,2,2);break;
case 5:old::put(b.data()+22,257,2);break;case 6:old::put(b.data()+36,130,2);break;default:b[20]=1;}
repair(b);if(j::decode(b,a.mac,out,w))return false;}
for(unsigned mode=0;mode<9;++mode){auto bad=a;
switch(mode){case 0:bad.profile=f::Profile::Unknown;break;case 1:bad.snapshot.binding.bytes[31]=1;break;
case 2:bad.mac={};bad.snapshot.binding=old::binding(bad.mac);break;
case 3:bad.mac[0]|=1;bad.snapshot.binding=old::binding(bad.mac);break;case 4:bad.snapshot.generation=0;break;
case 5:bad.snapshot.epoch=0;break;case 6:bad.snapshot.identity.firmware=4;break;
case 7:bad.snapshot.fields[0].address^=1;break;default:bad.snapshot.fields[0].width=1;}
if(j::encode(bad,b,w))return false;}
for(unsigned i=0;i<131;++i)for(unsigned k=a.snapshot.fields[i].width;k<4;++k){auto bad=a;
bad.snapshot.fields[i].original[k]=1;if(j::encode(bad,b,w))return false;
bad=a;bad.snapshot.fields[i].target[k]=1;if(j::encode(bad,b,w))return false;}
return true;}
static_assert(test());
''')

    def test_all131_source_target_and_coherent_nonowned_drift_rejected(self):
        self.compile(r'''
constexpr bool drift(unsigned index,unsigned mode){const auto a=sample();auto changed=a;
if(mode!=1)changed.snapshot.fields[index].original[0]^=1;
if(mode!=0)changed.snapshot.fields[index].target[0]^=1;
j::Workspace w{};j::Blob b{};if(j::encode(changed,b,w))return false;
// Recompute every stored CRC using the old generic wire codec: the new pure
// closed template must still reject this internally self-consistent drift.
old::Record generic{changed.mac,changed.snapshot};if(!old::encode(generic,b))return false;
b[3]='3';old::put(b.data()+4,3,2);b[21]=1;repair(b);j::Record out{};
return !j::decode(b,a.mac,out,w);}
ASSERT_DRIFT
'''.replace('ASSERT_DRIFT','\n'.join(f'static_assert(drift({i},{mode}));' for i in range(131) for mode in range(3))))

    def test_transitions_chain_missing_initial_and_never_implicit_native(self):
        self.compile(r'''
constexpr bool test(){auto a=sample();
if(j::validateChain(nullptr,nullptr)!=j::ChainStatus::MissingFailClosed||!j::initialRecord(a)||j::validateChain(nullptr,&a)!=j::ChainStatus::InitialRecord)return false;
for(auto phase:{r::Phase::Pending,r::Phase::Applied,r::Phase::Restoring}){auto bad=a;bad.snapshot.phase=phase;if(j::initialRecord(bad)||j::validateChain(nullptr,&bad)!=j::ChainStatus::Rejected)return false;}
for(auto phase:{r::Phase::Pending,r::Phase::Applied,r::Phase::Restoring,r::Phase::Original}){
auto b=next(a,phase);if(!j::transition(a,b)||j::validateChain(&a,&b)!=j::ChainStatus::Sequential)return false;a=b;}
// Every phase pair uses the exact existing directed graph, no direct restore->Applied.
for(unsigned from=0;from<4;++from)for(unsigned to=0;to<4;++to){a=sample();a.snapshot.phase=r::Phase(from);auto b=next(a,r::Phase(to));
bool allowed=(from==0&&(to==1||to==3))||(from==1&&(to==2||to==3))||(from==2&&to==3)||(from==3&&to==0);
if(j::transition(a,b)!=allowed)return false;}
a=sample();for(unsigned mode=0;mode<10;++mode){auto b=next(a,r::Phase::Pending);
switch(mode){case 0:b.snapshot.generation=1;break;case 1:b.snapshot.generation=3;break;case 2:b.snapshot.epoch=a.snapshot.epoch;break;
case 3:b.snapshot.epoch=a.snapshot.epoch-1;break;case 4:b.profile=f::Profile::Unknown;break;
case 5:b.mac[5]^=1;b.snapshot.binding=old::binding(b.mac);break;
case 6:b.snapshot.fields[0].original[0]^=1;break;case 7:b.snapshot.fields[0].target[0]^=1;break;
case 8:b.snapshot.scale=r::Scale::One;break;default:b.snapshot.identity.build=5;}
if(j::transition(a,b)||j::validateChain(&a,&b)!=j::ChainStatus::Rejected)return false;}
a.snapshot.generation=UINT32_MAX;auto b=next(a,r::Phase::Pending);if(j::transition(a,b))return false;
return j::validateChain(&sampleRecord,nullptr)==j::ChainStatus::Rejected;
}
'''.replace('constexpr bool test(){auto a=sample();','constexpr auto sampleRecord=sample();\nconstexpr bool test(){auto a=sample();')+'\nstatic_assert(test());\n')

    def test_source_not_integrated_and_old_namespace_unchanged(self):
        header=(ROOT/'main/hal/mosaico/mosaico_factory_scale_journal_model.h').read_text()
        self.assertNotIn('nvsNamespace',header)
        for token in ('nvs_set','nvs_open','gauge_access_word','gauge_control(','loadNvs','saveNvs','Envelope e'):
            self.assertNotIn(token,header)
        hal=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        self.assertNotIn('#include "mosaico_factory_scale_journal_model.h"',hal)
        old=(ROOT/'main/hal/mosaico/mosaico_gauge_scale_journal.h').read_text()
        self.assertIn('nvsNamespace="gscale_v1"',old)
        self.assertIn('nvsSlotKeys[2]={"recover0","recover1"}',old)


if __name__=='__main__':unittest.main()

