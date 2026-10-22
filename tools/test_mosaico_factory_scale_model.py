"""Offline closed factory rollback semantics. No HAL inclusion or device I/O.

Actual parameter fixture remains private; skip rather than substitute invented
source bytes if the independently verified complete export is unavailable.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'.artifacts/private/mosaico/gauge94r10-fresh131-revalidate-001/actual-public131-backup.json'
EXPECTED='9ddd3a18dab6537a0673eee45c8d5050c42427298ecf7ab6c3d3007b4142a40a'
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')


class FactoryScaleModelTests(unittest.TestCase):
    def test_not_integrated_or_hardware_authorized(self):
        hal=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        self.assertNotIn('#include "mosaico_factory_scale_model.h"',hal)
        text=(ROOT/'main/hal/mosaico/mosaico_factory_scale_model.h').read_text()
        for token in ('nvs_set','gauge_access_word','gauge_control(','esp_ota_set','nvs_erase'):
            self.assertNotIn(token,text)

    def test_actual_closed_images_all_write_boundaries_and_factory_never_native(self):
        if not PRIVATE.exists():self.skipTest('private complete131 export unavailable')
        self.assertEqual(hashlib.sha256(PRIVATE.read_bytes()).hexdigest(),EXPECTED)
        data=json.loads(PRIVATE.read_text())
        self.assertTrue(data['complete']);self.assertEqual(len(data['fields']),131)
        rows=[]
        for f in data['fields']:
            raw=bytes.fromhex(f['rawBE']).ljust(4,b'\0')
            rows.append('{0x%s,%d,{%s},{%s}}'%(f['address'],f['width'],','.join(map(str,raw)),','.join(map(str,raw))))
        code=r'''
#include "mosaico_factory_scale_model.h"
namespace f=mosaico_factory_scale;namespace r=mosaico_gauge_recovery;
constexpr r::Snapshot sample(){r::Snapshot s{};s.identity={0x220,3,4};s.binding.bytes[31]=0xa5;s.layoutId=r::Layout::Public131V1;
s.generation=1;s.epoch=10;s.scale=r::Scale::Two;s.count=131;s.phase=r::Phase::Original;
constexpr r::Field fields[]={ROWS};
for(unsigned i=0;i<131;++i){s.fields[i]=fields[i];for(const auto& c:f::changes){if(c.address!=s.fields[i].address)continue;
for(unsigned j=0;j<c.width;++j){s.fields[i].target[j]=uint8_t(c.after>>(8*(c.width-j-1)));}}}return s;}
constexpr auto image=sample();
constexpr f::Envelope envelope(){return {3,f::Profile::Factory3000RollbackToNominal65Virtual130,f::factorySourceCrc,f::digest(image,true),true,true};}
constexpr r::Context context(){r::Context c{};c.identity=image.identity;c.binding=image.binding;c.layoutId=image.layoutId;c.sourceCrcAttested=c.reviewedTargetAttested=true;return c;}
constexpr r::Observation observation(unsigned prefix){r::Observation o{};o.count=131;o.authenticated=o.sealed=o.cfg0=true;unsigned n=0;
for(unsigned i=0;i<131;++i){bool owned=false;for(const auto& c:f::changes){owned|=c.address==image.fields[i].address;}
const bool target=owned&&n++<prefix;for(unsigned j=0;j<4;++j){o.raw[i][j]=target?image.fields[i].target[j]:image.fields[i].original[j];}}return o;}
constexpr r::DurableProof durable(){return {true,true,true,true,true,1,11};}
constexpr bool safe(f::Result v){return !v.capacityCalibrated&&!v.nativeWaiver&&!v.clearHistoricalFailure&&!v.decision.publishPhysicalTelemetry&&!v.decision.parameterWritesAuthorized;}
constexpr bool denied(f::Result v){return safe(v)&&v.decision.action==r::Action::FailClosed&&v.units==f::Units::Unknown;}
static_assert(!f::readyForHardware);
static_assert(f::closedProfile(envelope(),image));
static_assert(f::digest(image,false)==0x70a767be);
constexpr bool cases(){
const auto e=envelope();const auto c=context();const auto original=observation(0);const auto target=observation(17);const auto p=durable();
auto start=f::startup(e,image,c,original);if(!safe(start)||start.units!=f::Units::Unknown||start.decision.action!=r::Action::OriginalNoWrite)return false;
auto a=f::apply(e,image,c,original,p,11);if(!safe(a)||a.decision.action!=r::Action::WriteReviewedTarget)return false;
for(unsigned cut=0;cut<=17;++cut){auto pending=image;pending.phase=r::Phase::Pending;const auto current=observation(cut);
auto boot=f::startup(e,pending,c,current);if(!safe(boot)||boot.units!=f::Units::Unknown||boot.decision.action!=r::Action::RestoreRequired)return false;
auto back=f::restore(e,pending,c,current,p,11);if(!safe(back)||back.decision.action!=r::Action::RestoreOriginal)return false;
auto unknown=current;unknown.cfg0=false;if(!denied(f::restore(e,pending,c,unknown,p,11)))return false;
unknown=current;unknown.sealed=false;if(!denied(f::restore(e,pending,c,unknown,p,11)))return false;}
auto pending=image;pending.phase=r::Phase::Pending;auto applied=f::finishApply(e,pending,c,target,p,11);
if(!safe(applied)||applied.units!=f::Units::ExperimentalVirtualTwo||applied.decision.action!=r::Action::MarkApplied)return false;
auto restoring=image;restoring.phase=r::Phase::Restoring;auto done=f::finishRestore(e,restoring,c,original,p,11);
if(!safe(done)||done.units!=f::Units::Unknown||done.decision.action!=r::Action::MarkOriginal)return false;
for(unsigned i=0;i<131;++i){auto third=original;uint8_t value=0;while(value==image.fields[i].original[0]||value==image.fields[i].target[0]){++value;}
third.raw[i][0]=value;if(!denied(f::restore(e,restoring,c,third,p,11)))return false;}
for(unsigned missing=0;missing<5;++missing){auto bad=p;switch(missing){case 0:bad.originalDurable=false;break;case 1:bad.originalReadbackIdentical=false;break;case 2:bad.pendingDurable=false;break;case 3:bad.pendingReadbackIdentical=false;break;default:bad.journalCrcAttested=false;}
if(!denied(f::apply(e,image,c,original,bad,11))||!denied(f::restore(e,restoring,c,target,bad,11)))return false;}
for(unsigned i=0;i<131;++i){auto wrong=image;wrong.fields[i].target[0]^=1;auto bad=e;bad.targetCrc=f::digest(wrong,true);if(f::closedProfile(bad,wrong))return false;}
auto bad=e;bad.version=2;if(!denied(f::startup(bad,image,c,original)))return false;
bad=e;bad.completeImageHashesVerified=false;if(!denied(f::apply(bad,image,c,original,p,11)))return false;
bad=e;bad.bothActualSignedImagesSupportProfile=false;if(!denied(f::apply(bad,image,c,original,p,11)))return false;
bad=e;bad.profile=f::Profile::Unknown;if(!denied(f::startup(bad,image,c,original)))return false;
bad=e;bad.sourceCrc^=1;if(f::closedProfile(bad,image))return false;
auto wrong=image;wrong.scale=r::Scale::One;if(f::closedProfile(e,wrong))return false;
return true;}
static_assert(cases(),"factory rollback must remain untrusted across every write/commit boundary");
'''.replace('ROWS',','.join(rows))
        out=ROOT/'.artifacts/private/mosaico/factory-scale-model-tests';out.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.factory-scale-model-',dir=out) as td:
            src=Path(td)/'test.cpp';src.write_text(code)
            result=subprocess.run([str(COMPILER),'-std=c++20','-Wall','-Wextra','-Werror','-fconstexpr-ops-limit=200000000','-fsyntax-only','-I'+str(ROOT/'main/hal/mosaico'),str(src)],capture_output=True,text=True)
            (out/'compile.log').write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(out/'compile.log'))


if __name__=='__main__':unittest.main()
