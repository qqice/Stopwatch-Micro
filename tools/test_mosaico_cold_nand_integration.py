"""Offline cold NAND wiring/lease mocks; no SDK build, NAND or device operation."""
from pathlib import Path
import subprocess
import re
import test_mosaico_nand_store as legacy
import tempfile
import unittest
from test_microlink_peer_probe import function
from test_mosaico_gauge_cold_trial import COMPILER, FIXTURE as TRIAL

ROOT=Path(__file__).resolve().parents[1]
NAND=(ROOT/'main/hal/mosaico/mosaico_nand_store.cpp').read_text()
OTA=(ROOT/'main/ota/mosaico_ota.cpp').read_text()
HEADER=(ROOT/'main/hal/mosaico/mosaico_nand_store.h').read_text()


def struct(source,name):
    return function(source,'struct '+name+' {')+';'


class ColdNandIntegrationTests(unittest.TestCase):
    def compile(self,code,ops=100000000):
        directory=ROOT/'artifacts/cold-nand-integration-tests'
        directory.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            temp=Path(temporary)
            (temp/'esp_err.h').write_text('#pragma once\nusing esp_err_t=int;constexpr int ESP_OK=0,ESP_ERR_INVALID_ARG=1;\n')
            path=temp/'test.cpp';path.write_text(code,encoding='utf-8')
            result=subprocess.run([str(COMPILER),'-std=c++20','-Wall','-Wextra','-Werror',
                '-fconstexpr-ops-limit='+str(ops),'-fsyntax-only','-I'+str(temp),
                '-I'+str(ROOT/'main/hal/mosaico'),'-I'+str(ROOT),str(path)],capture_output=True,text=True)
            log=directory/(self._testMethodName+'.log');log.write_text(result.stdout+result.stderr,encoding='utf-8')
            self.assertEqual(result.returncode,0,str(log))

    def test_exact_task_owner_nested_busy_and_wrong_task_release(self):
        methods='\n'.join(function(OTA,s) for s in ('bool tryStorageLease(', 'bool currentTaskOwnsStorageLease(', 'void releaseStorageLease('))
        methods=methods.replace('bool tryStorageLease(', 'constexpr bool tryStorageLease(').replace('bool currentTaskOwnsStorageLease(', 'constexpr bool currentTaskOwnsStorageLease(').replace('void releaseStorageLease(', 'constexpr void releaseStorageLease(')
        code=r'''
#include <atomic>
#include <cstdint>
using TaskHandle_t=const int*;
template<class T>struct Atomic {T value;constexpr void store(T v,std::memory_order){value=v;}constexpr T load(std::memory_order)const{return value;}};
struct Fixture {
 int a=1,b=2;TaskHandle_t current=&a;
 struct Mutex {TaskHandle_t* current;TaskHandle_t owner=nullptr;unsigned depth=0,attempts=0,unlocks=0;
  constexpr bool try_lock(){++attempts;if(depth&&owner!=*current)return false;owner=*current;++depth;return true;}
  constexpr void unlock(){++unlocks;if(--depth==0)owner=nullptr;}
 }storageLeaseMutex{&current};
 Atomic<TaskHandle_t> storageLeaseOwner{nullptr};uint32_t storageLeaseDepth=0;bool busyFlag=false;
 constexpr TaskHandle_t xTaskGetCurrentTaskHandle(){return current;}constexpr bool busy(){return busyFlag;}
'''+methods+r'''
};
constexpr bool test(){Fixture f;
 if(f.currentTaskOwnsStorageLease()||!f.tryStorageLease()||!f.currentTaskOwnsStorageLease())return false;
 if(!f.tryStorageLease()||f.storageLeaseDepth!=2)return false;
 f.releaseStorageLease();if(!f.currentTaskOwnsStorageLease()||f.storageLeaseDepth!=1)return false;
 f.current=&f.b;const auto unlocks=f.storageLeaseMutex.unlocks;f.releaseStorageLease();
 if(f.currentTaskOwnsStorageLease()||f.tryStorageLease()||f.storageLeaseMutex.unlocks!=unlocks)return false;
 f.current=&f.a;f.busyFlag=true;if(f.tryStorageLease()||!f.currentTaskOwnsStorageLease()||f.storageLeaseDepth!=1)return false;
 f.busyFlag=false;f.releaseStorageLease();if(f.currentTaskOwnsStorageLease()||f.storageLeaseMutex.depth)return false;
 f.current=nullptr;if(f.tryStorageLease()||f.currentTaskOwnsStorageLease()||f.storageLeaseMutex.depth)return false;
 return true;
}static_assert(test());
'''
        self.compile(code)

    def test_actual_storage_lease_inherited_vs_acquired(self):
        lease=struct(NAND,'StorageLease').replace('MosaicoOta::','ota.')
        lease=lease.replace('bool held=false,acquired=false;', 'Ota& ota;bool held=false,acquired=false;')
        lease=lease.replace('explicit StorageLease(bool inherit=false){','constexpr explicit StorageLease(Ota& o,bool inherit=false):ota(o){')
        lease=lease.replace('~StorageLease()', 'constexpr ~StorageLease()').replace('explicit operator bool()', 'constexpr explicit operator bool()')
        self.compile(r'''
struct Ota {bool owned=false,busyFlag=false,allow=true;unsigned tries=0,releases=0;
 constexpr bool currentTaskOwnsStorageLease(){return owned;}constexpr bool busy(){return busyFlag;}
 constexpr bool tryStorageLease(){++tries;return allow;}constexpr void releaseStorageLease(){++releases;}};
'''+lease+r'''
constexpr bool test(){Ota o;
 {StorageLease s(o,true);if(s||o.tries||o.releases)return false;}
 o.owned=true;{StorageLease s(o,true);if(!s||o.tries)return false;}if(o.releases)return false;
 o.busyFlag=true;{StorageLease s(o,true);if(s||o.tries)return false;}o.busyFlag=false;
 {StorageLease s(o,false);if(!s||o.tries!=1)return false;}if(o.releases!=1)return false;
 o.allow=false;{StorageLease s(o,false);if(s||o.tries!=2)return false;}
 return o.releases==1;
}static_assert(test());
''')

    def io_fixture(self):
        mapper=struct(NAND,'ColdIO').replace('    bool ', '    constexpr bool ')
        base=function(NAND,'bool coldPageBase(').replace('bool coldPageBase(', 'constexpr bool coldPageBase(')
        same=function(NAND,'bool sameColdRecord(').replace('bool sameColdRecord(', 'constexpr bool sameColdRecord(')
        return TRIAL+r'''
#include "mosaico_nand_store.h"
#include "mosaico_gauge_cold_capsule.h"
using namespace MosaicoNandStore;namespace cold=mosaico_gauge_cold_store;namespace k=mosaico_gauge_cold_capsule;
struct IO {
 std::array<cold::Page,70> pages{};int lastError=0;unsigned writes=0,syncs=0,lastWrite=0;
 bool failWrite=false,failSync=false,failRead=false;
 constexpr IO(){for(auto& p:pages)p.fill(255);cold::nand::marker(pages[0]);cold::nand::marker(pages[1]);}
 constexpr bool read(uint32_t page,cold::Page& out){if(page>=pages.size()||failRead){lastError=7;return false;}out=pages[page];lastError=0;return true;}
 constexpr bool write(uint32_t page,const cold::Page& in){if(page>=pages.size()||failWrite){lastError=8;return false;}++writes;lastWrite=page;pages[page]=in;return true;}
 constexpr bool sync(){++syncs;return !failSync;}
};
constexpr m::Digest digest(){m::Digest d{};d[31]=44;return d;}
constexpr bool valid(void*,const ColdRecord& r){return k::Policy{binding(),digest()}.valid(r);}
constexpr bool initial(void*,const ColdRecord& r){return k::Policy{binding(),digest()}.initial(r);}
constexpr bool transition(void*,const ColdRecord& a,const ColdRecord& b){return k::Policy{binding(),digest()}.transition(a,b);}
constexpr ColdPolicy policy(){return {nullptr,valid,initial,transition};}
constexpr ColdRecord pack(const m::Record& t){ColdRecord r{};k::encode(t,binding(),digest(),r);return r;}
'''+base+mapper+same

    def test_actual_mapper_whitelist_and_complete_record_comparison(self):
        self.compile(self.io_fixture()+r'''
constexpr bool test(){uint32_t base=0;if(!coldPageBase(ColdArea::Journal,base)||base!=8||!coldPageBase(ColdArea::Proof,base)||base!=12||coldPageBase(ColdArea(255),base))return false;
 IO io;ColdIO proof{io,12};cold::Page page{};cold::nand::marker(page);
 for(uint32_t i=0;i<70;++i){unsigned writes=io.writes;bool ok=proof.write(i,page);if(ok!=(i==8||i==9)||io.writes!=writes+unsigned(ok))return false;}
 if(io.lastWrite!=13||!proof.read(0,page)||!proof.read(1,page)||!proof.read(8,page)||proof.read(12,page))return false;
 const auto a=pack(armed());if(!sameColdRecord(a,a))return false;
 for(unsigned kind=0;kind<6;++kind){auto b=a;
  if(kind==0){++b.sequence;}if(kind==1){++b.phase;}if(kind==2){b.nonce[15]^=1;}
  if(kind==3){b.binding[31]^=1;}if(kind==4){++b.size;}if(kind==5){b.payload[1535]^=1;}
  if(sameColdRecord(a,b))return false;
 }
 return true;
}static_assert(test());
''')

    def test_production_policy_callbacks_pair_isolation_and_io_errors(self):
        self.compile(self.io_fixture()+r'''
constexpr bool test(){IO io;const auto untouched=io.pages;cold::Scratch scratch{};auto p=policy();if(!p.ready())return false;
 auto invalid=p;invalid.initialFn=nullptr;if(invalid.ready()||invalid.initial(pack(armed())))return false;
 ColdIO journal{io,8},proof{io,12};const auto a=pack(armed()),h=pack(handed());
 if(cold::save(journal,scratch,a,p,false)!=cold::Code::Rejected||io.writes)return false;
 if(cold::save(journal,scratch,a,p,true)!=cold::Code::Ready||io.lastWrite!=8)return false;
 if(cold::save(proof,scratch,a,p,true)!=cold::Code::Ready||io.lastWrite!=12)return false;
 if(cold::save(journal,scratch,h,p,true)!=cold::Code::Ready||io.lastWrite!=9)return false;
 auto loaded=cold::load(journal,scratch,p);if(loaded.code!=cold::Code::Ready||!sameColdRecord(scratch.records[unsigned(loaded.selected)],h))return false;
 if(cold::load(proof,scratch,p).code!=cold::Code::Ready||!sameColdRecord(scratch.records[0],a))return false;
 for(unsigned i=0;i<70;++i)if(i!=8&&i!=9&&i!=12&&i!=13&&io.pages[i]!=untouched[i])return false;
 IO failed;ColdIO f{failed,12};failed.failWrite=true;if(cold::save(f,scratch,a,p,true)!=cold::Code::Io)return false;
 failed.failWrite=false;failed.failSync=true;if(cold::save(f,scratch,a,p,true)!=cold::Code::Io)return false;
 failed.failRead=true;return cold::load(f,scratch,p).code==cold::Code::Io;
}static_assert(test());
''')

    def test_actual_cold_transaction_reopen_output_and_cleanup_faults(self):
        # Reuse the legacy lifecycle fixture without weakening any old asserts.
        # This test alone extends media capacity and supplies two new fault knobs.
        fixture=legacy.adapter_fixture()
        fixture=fixture.replace('std::array<Model::Page,Model::RequiredPages> durable{},current{};',
            'std::array<Model::Page,70> durable{},current{};unsigned failReadAt=0;')
        fixture=fixture.replace('++reads;if(readError)', '++reads;if(reads==failReadAt)return -77;if(readError)')
        fixture=fixture.replace('bool coldPageBase(', 'constexpr bool coldPageBase(')
        fixture=fixture.replace('bool sameColdRecord(', 'constexpr bool sameColdRecord(')
        code=fixture+TRIAL+r"""
#include "main/hal/mosaico/mosaico_gauge_cold_capsule.h"
namespace capsule=mosaico_gauge_cold_capsule;
constexpr m::Digest coldDigest(){m::Digest d{};d[31]=44;return d;}
constexpr bool coldValid(void*,const ColdRecord& r){return capsule::Policy{binding(),coldDigest()}.valid(r);}
constexpr bool coldInitial(void*,const ColdRecord& r){return capsule::Policy{binding(),coldDigest()}.initial(r);}
constexpr bool coldTransition(void*,const ColdRecord& a,const ColdRecord& b){return capsule::Policy{binding(),coldDigest()}.transition(a,b);}
constexpr ColdPolicy coldPolicy(){return {nullptr,coldValid,coldInitial,coldTransition};}
constexpr ColdRecord coldFirst(){ColdRecord r{};capsule::encode(armed(),binding(),coldDigest(),r);return r;}
constexpr auto saveCold(Fixture& f,bool inherit=false){const auto r=coldFirst();const auto p=coldPolicy();return f.transaction(Fixture::Operation::ColdSave,nullptr,nullptr,true,nullptr,nullptr,nullptr,nullptr,0,ColdArea::Journal,&r,nullptr,&p,inherit);}
constexpr auto loadCold(Fixture& f,ColdRecord& out,bool inherit=false){const auto p=coldPolicy();return f.transaction(Fixture::Operation::ColdLoad,nullptr,nullptr,true,nullptr,nullptr,nullptr,nullptr,0,ColdArea::Journal,nullptr,&out,&p,inherit);}
constexpr bool coldFaults(){
 Fixture baseline;baseline.seed(pattern(1));const auto success=saveCold(baseline);
 if(success.error||success.cleanupError||success.coldCode!=cold::Code::Ready||success.stage!=Stage::Complete||!clean(baseline)||baseline.inits!=2||baseline.deinits!=2||baseline.leaseReleases!=1)return false;
 ColdRecord out{};if(loadCold(baseline,out).error||out.payload!=coldFirst().payload)return false;
 Fixture inherited;inherited.seed(pattern(1));inherited.leaseDepth=1;
 const auto nested=saveCold(inherited,true);
 if(nested.error||nested.cleanupError||inherited.leaseDepth!=1||inherited.leaseAttempts||inherited.leaseReleases)return false;
 for(unsigned kind=0;kind<3;++kind){Fixture f;f.seed(pattern(1));
  if(kind==0){f.failDeinit=true;}if(kind==1){f.failInitAt=2;}if(kind==2){f.failReadAt=baseline.reads-4-15;} // baseline includes one later coldLoad(4 reads)
  const auto failed=saveCold(f);
  if(failed.error==ESP_OK||failed.stage!=Stage::Reopen||f.leaseDepth||f.leaseReleases!=1||f.allocations!=f.frees)return false;
  if(kind==0){if(failed.cleanupError!=-72||!f.cleanupFailed||f.removes||f.busFrees)return false;}
  if(kind==1){if(failed.error!=-42||failed.cleanupError||f.busLive||f.deviceLive)return false;}
  if(kind==2){if(failed.error!=-77||failed.cleanupError||f.busLive||f.deviceLive)return false;}
 }
 Fixture cleanup;cleanup.seed(pattern(1));if(saveCold(cleanup).error)return false;
 cleanup.failRemove=true;const auto sentinel=coldFirst();out=sentinel;out.payload[1535]=99;const auto unchanged=out;
 const auto failed=loadCold(cleanup,out);
 if(failed.error||failed.cleanupError!=-70||!cleanup.cleanupFailed||out.payload!=unchanged.payload||cleanup.leaseDepth||cleanup.leaseReleases!=2)return false;
 const auto allocations=cleanup.allocations;const auto refused=loadCold(cleanup,out);
 return refused.error==ESP_ERR_INVALID_STATE&&cleanup.allocations==allocations&&out.payload==unchanged.payload;
}static_assert(coldFaults(),"cold fresh resume/deinit/reinit/protected-read/cleanup failures retain errors, never publish output, and balance owned/inherited lease");
"""
        self.compile(code,ops=500000000)

    def test_actual_full_nand_adapter_syntax(self):
        prefix=legacy.ADAPTER_SYNTAX_PREFIX.replace('namespace MosaicoOta {bool tryStorageLease();void releaseStorageLease();}',
            'namespace MosaicoOta {bool tryStorageLease();void releaseStorageLease();bool currentTaskOwnsStorageLease();bool busy();}')
        prefix+='#include "main/hal/mosaico/mosaico_gauge_cold_store_model.h"\n'
        header=re.sub(r'^#.*$', '',HEADER,flags=re.M)
        body=re.sub(r'^#.*$', '',NAND,flags=re.M)
        self.compile(prefix+header+body)

    def test_transaction_reopen_and_allocation_source_contract(self):
        body=function(NAND,'Result transaction(')
        self.assertIn('StorageLease storageLease(inheritStorageLease)',body)
        self.assertLess(body.index('if(!authorized)'),body.index('StorageLease storageLease'))
        self.assertIn('coldOperation?sizeof(ColdWorkspace):sizeof(Workspace)',body)
        self.assertIn('MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT',body)
        self.assertNotIn('Cold',struct(NAND,'Workspace'))
        cold=body.split('if(coldOperation){',1)[1].split('if(journal){',1)[0]
        for token in ('cold::save(', 'Stage::Reopen', 'spi_nand_flash_deinit_device(nand)', 'spi_nand_flash_init_device(&config,&nand)', 'cold::load(', 'sameColdRecord(', 'protectedCrc[i]', 'cold::Code::Verify'):
            self.assertIn(token,cold)
        self.assertLess(cold.index('spi_nand_flash_init_device(&config,&nand)'),cold.index('cold::load('))
        self.assertNotIn('erase',cold.lower());self.assertNotIn('formatConfirm',cold)
        self.assertIn('coldOutput&&coldWorkspace->publish',body)
        self.assertIn('result.error==ESP_OK&&result.cleanupError==ESP_OK',body)
        self.assertIn('if(result.cleanupError!=ESP_OK)cleanupFailed=true',body)
        for signature in ('Result coldLoad(', 'Result coldSave('):
            self.assertIn('inheritStorageLease',function(NAND,signature))
        self.assertIn('currentTaskOwnsStorageLease()&&!MosaicoOta::busy()',struct(NAND,'StorageLease'))
        self.assertIn('std::atomic<TaskHandle_t> storageLeaseOwner',OTA)
        self.assertIn('void* context=nullptr',HEADER)
        self.assertNotIn('gauge_',cold);self.assertNotIn('battery_',cold)


if __name__=='__main__':unittest.main()
