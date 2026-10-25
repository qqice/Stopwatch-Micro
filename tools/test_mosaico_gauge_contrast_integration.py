"""Execute the production isolated GCT parser and maintenance provider on RV32."""
import unittest
import test_mosaico_factory_scale_integration as old
from test_microlink_peer_probe import function

class IntegrationTests(unittest.TestCase):
    def test_actual_maintenance_provider_both_namespaces(self):
        m=old.maintenance
        fixture,cases,run,reset=m.FIXTURE,m.CASES,m.RUN,m.RESET
        hooks='''
#define MOSAICO_FACTORY_SCALE_RUNTIME
#define MOSAICO_GAUGE_CONTRAST_RUNTIME
static bool factorySafe=true,factoryOriginal=false,contrastSafe=true,contrastOriginal=false;
bool factory_scale_maintenance_cached_safe_locked(){return factorySafe;}
bool factory_scale_maintenance_original_cached_locked(){return factoryOriginal;}
bool contrast_maintenance_cached_safe_locked(){return contrastSafe;}
bool contrast_maintenance_original_cached_locked(){return contrastOriginal;}
'''
        extra=[
            ('missing','contrastSafe=true;contrastOriginal=false;scale_load_status=scale_journal::Status::Missing;',True,False),
            ('original_boot_recovery_reason','contrastOriginal=true;std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',True,False),
            ('pending','contrastSafe=false;',False,True),
            ('corrupt','contrastSafe=false;contrastOriginal=false;',False,True),
            ('wrong_critical_reason','contrastOriginal=true;std::snprintf(gauge_boot_info.reason,128,"unexpected_critical");',True,False),
            ('attempted_critical','contrastOriginal=true;gauge_boot_info.attempted=true;std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',True,False),
            ('active_factory','factorySafe=false;contrastOriginal=true;',False,True),
            ('untrusted_gauge130','contrastOriginal=true;dc=fcc=finalDc=finalFcc=130;',False,False),
        ]
        try:
            m.FIXTURE=fixture.replace('#include "mosaico_maintenance_power.inc"',hooks+'\n#include "mosaico_maintenance_power.inc"')
            m.CASES=list(cases[:5])+extra;m.RUN=old.RUN/'contrast-maintenance'
            m.RESET=reset.replace('cacheLocks=0;','cacheLocks=0;factorySafe=true;factoryOriginal=false;contrastSafe=true;contrastOriginal=false;')
            m.MaintenancePowerTests('test_actual_provider_rv32').test_actual_provider_rv32()
        finally:m.FIXTURE,m.CASES,m.RUN,m.RESET=fixture,cases,run,reset

    def test_boot_cache_order_and_old_writer_lock_ownership(self):
        source=(old.ROOT/'main/hal/mosaico/mosaico_gauge_scale_runtime.inc').read_text()
        fence=function(source,'bool scale_boot_fence_locked(')
        self.assertLess(fence.index('scale_load_locked()'),fence.index('contrast_boot_fence_locked()'))
        self.assertLess(fence.index('factory_scale_boot_fence_locked()'),fence.index('if(!contrastAllowed)'))
        source=(old.ROOT/'main/hal/mosaico/mosaico_factory_scale_runtime.inc').read_text()
        for name in ('Prepare','Apply','Restore'):
            body=function(source,'bool Hal::gaugeFactoryScale'+name+'(')
            self.assertLess(body.index('transaction(gauge_transaction_mutex)'),body.index('contrast_present_locked()'))

    def test_actual_parser_and_consume(self):
        source=(old.ROOT/'main/debug/serial_debug.cpp').read_text()
        prefix=old.PARSER_PREFIX.replace(' bool handleContrast(char*){return false;}', ' bool handleContrast(char*);')
        prefix=prefix.replace('struct Hal {', '''struct Hal {
 bool gaugeContrastStatus(char*p,size_t n){return call(7,p,n);}
 bool gaugeContrastImages(const char*,char*p,size_t n){return call(8,p,n);}
 bool gaugeContrastPrepare(const char*a,char*p,size_t n){indexSeen=*a;return call(9,p,n);}
 bool gaugeContrastApply(char*p,size_t n){return call(10,p,n);}
 bool gaugeContrastRestore(char*p,size_t n){return call(11,p,n);}
 bool gaugeContrastObservedPage(size_t i,char*p,size_t n){indexSeen=i;return call(12,p,n);}
''')
        transport=(old.ROOT/'main/debug/serial_debug_transport.h').read_text()
        transport='namespace serial_debug_transport {'+transport.split('namespace serial_debug_transport {',1)[1].split('// No driver waits',1)[0]+'bool uartAllowed(const char*){++authCalls;return allowUart;}\n}\n'
        unsigned='bool parseUnsignedStrict'+source.split('bool parseUnsignedStrict',1)[1].split('#ifdef MOSAICO_BOARD',1)[0]
        support=old.base.MOCKS.split('static Hal hal;',1)[0]+old.base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
        runner=r'''
static SerialDebug serial;
static const char* commands[]={"debug gauge-contrast","debug gauge-contrast status","debug gauge-contrast images abc","debug gauge-contrast prepare C CONFIRM","debug gauge-contrast prepare B CONFIRM","debug gauge-contrast apply CONFIRM_EXTERNAL_POWER","debug gauge-contrast restore CONFIRM","debug gauge-contrast observed-page 4",
"debug gauge-contrast prepare C","debug gauge-contrast prepare A CONFIRM","debug gauge-contrast prepare C CONFIRM extra","debug gauge-contrast apply CONFIRM","debug gauge-contrast observed-page 5","debug gauge-contrast status extra","debug gauge-contrast images abc extra","debug gauge-contrast restore","debug gauge-contrast observed-page -1","debug gauge-contrast prepare BC CONFIRM"};
static int run(int id){halCalls=results=pass=authCalls=nandCalls=0;allowUart=true;nandBusy=false;serial._reply_uart=false;char line[1536]{};
 int n=id<18?id:3;std::memcpy(line,commands[n],std::strlen(commands[n])+1);
 if(id==18){serial._reply_uart=true;allowUart=false;}
 if(id==19)nandBusy=true;
 if(!serial.handleContrast(line)||results!=1)return 1;
 if(id<8){const int expected[]={7,7,8,9,9,10,11,12};if(halCalls!=1||!pass||actionSeen!=expected[id])return 2;if(id==3&&indexSeen!='C')return 3;if(id==4&&indexSeen!='B')return 4;}
 else if(halCalls||pass)return 5;
 return 0;}
extern "C" {volatile int test_results[20]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<20;++i)test_results[i]=run(i);test_done();}
'''
        code=prefix+transport+unsigned+function(source,'bool __attribute__((noinline)) SerialDebug::handleContrast(')+function(source,'bool __attribute__((noinline)) SerialDebug::handleFactoryScale(')+function(source,'void SerialDebug::consume(')+support+runner
        # Reuse the RV32 emulator and stack-bound executor.
        original=old.execute
        original(self,code,'contrast-parser',20)

if __name__=='__main__':unittest.main()
