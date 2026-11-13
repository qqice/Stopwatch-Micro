"""Compile-time execution of the production cold RESET helper, no device access.
Mock transport sits below the unchanged vendor security APIs. No firmware build.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_mosaico_official_gauge as official
from test_mosaico_capacity130 import function, compiler

BASE = official.BASE

class ColdFactoryResetTests(unittest.TestCase):
    def test_actual_helper_fault_injection(self):
        source = (BASE/'mosaico_official_gauge_driver.c').read_text()
        helper = function(source, 'bool mosaico_bq_boot_factory_reset(')
        write = function(source, 'static esp_err_t mosaico_bq_write(').replace('static esp_err_t', 'constexpr esp_err_t').replace('memcpy(', 'copy_bytes(')
        vendor = (official.VENDOR/'bq27220.c').read_text()
        security = '\n'.join(function(vendor, 'esp_err_t '+name+'(') for name in ('bq27220_unseal','bq27220_seal'))
        code = r'''
#include <cstdint>
#include <cstddef>
using esp_err_t=int;
using i2c_master_dev_handle_t=unsigned;
using i2c_bus_device_handle_t=unsigned;
struct bq27220_data_t {unsigned i2c_device_handle;};
using bq27220_handle_t=bq27220_data_t*;
struct operation_status_t {bool CALMD:1;uint8_t SEC:2;bool EDV2:1;bool VDQ:1;bool INITCOMP:1;bool SMTH:1;bool BTPINT:1;uint8_t RSVD1:2;bool CFGUPDATE:1;uint8_t RSVD0:5;};
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_INVALID_ARG=-2,ESP_ERR_INVALID_SIZE=-3;
constexpr unsigned OPERATION_STATUS_SEC_FULL=1;
#define pdMS_TO_TICKS(x) (x)
#define vTaskDelay(x) delay(x)
#define ESP_LOGI(...) (void)0
#define ESP_LOGW(...) (void)0
#define ESP_LOGE(...) (void)0
#define ESP_RETURN_ON_FALSE(a,b,...) if(!(a))return b
#define delay_ms(x) delay(x)
constexpr unsigned COMMAND_OPERATION_STATUS=0x3a,COMMAND_DESIGN_CAPACITY=0x3c,COMMAND_FULL_CHARGE_CAPACITY=0x12;
constexpr unsigned CONTROL_RESET=0x41,CONTROL_SEALED=0x30,UNSEALKEY1=0x414,UNSEALKEY2=0x3672;
constexpr unsigned OPERATION_STATUS_SEC_SEALED=3,OPERATION_STATUS_SEC_UNSEALED=2,CONFIG_BQ27220_SEAL_SETTLE_MS=2000;
struct Harness {
 unsigned mosaico_bq_attempts=0,resets=0,seals=0,keys=0,forbidden=0;
 bool mosaico_bq_track_writes=false;int mosaico_bq_first_error=0;
 unsigned unknownKey=0;bool sealUnknown=false,staleInit=false,earlyAccept=false;
 unsigned sec=3,dc=3000,fcc=3000;
 int64_t time=0,resetAt=0;
 bool unknownAck=false,timeout=false,sealFail=false,unsealFail=false,readFail=false;
 bool badPostCaps=false,badPostMode=false,intent=true;
 constexpr int64_t esp_timer_get_time(){return time;}
 constexpr void delay(unsigned ms){time+=ms*1000;}
 constexpr int bq27220_read_u16_checked(bq27220_handle_t,unsigned reg,uint16_t* out){
  time+=100; if(readFail)return -1;
  if(reg==COMMAND_OPERATION_STATUS){
   bool init=!resets||!timeout;
   if(resets&&staleInit){int64_t age=time-resetAt;init=age<100000||age>=2000000;if(age<2000000&&init)earlyAccept=true;}
   *out=(sec<<1)|(init?0x20:0)|(resets&&badPostMode?0x400:0);
  }
  else if(reg==COMMAND_DESIGN_CAPACITY)*out=dc;
  else if(reg==COMMAND_FULL_CHARGE_CAPACITY)*out=resets&&badPostCaps?65:fcc;
  else return -1;
  return 0;
 }
 constexpr int bq27220_get_operation_status(bq27220_handle_t h,operation_status_t* s){
  uint16_t raw=0;bq27220_read_u16_checked(h,COMMAND_OPERATION_STATUS,&raw);
  s->SEC=(raw>>1)&3;return 0; // Actual vendor's lossy status contract.
 }
 constexpr void copy_bytes(uint8_t* to,const uint8_t* from,size_t n){for(size_t i=0;i<n;++i)to[i]=from[i];}
 constexpr int i2c_master_transmit(unsigned,const uint8_t* packet,size_t n,int){
  if(n!=3||packet[0]!=0){++forbidden;return -1;}
  const unsigned command=packet[1]|(packet[2]<<8);
  if(command==CONTROL_RESET){++resets;intent=intent&&bool(mosaico_bq_attempts&4);resetAt=time;return unknownAck?-1:0;}
  if(command==CONTROL_SEALED){++seals;if(!sealFail)sec=3;return sealFail||sealUnknown?-1:0;}
  if(command==UNSEALKEY1||command==UNSEALKEY2){++keys;if(keys==2&&!unsealFail)sec=2;return unknownKey==keys?-1:0;}
  ++forbidden;return -1;
 }
'''+write+r'''
 constexpr int bq27220_control(bq27220_handle_t h,unsigned command){
  const uint8_t bytes[2]={uint8_t(command),uint8_t(command>>8)};
  return mosaico_bq_write(h->i2c_device_handle,0,2,bytes);
 }
'''+security.replace('esp_err_t ', 'constexpr esp_err_t ')+ '\n'+helper.replace('bool mosaico_bq_boot_factory_reset(', 'constexpr bool mosaico_bq_boot_factory_reset(')+r'''
};
constexpr bool tests(){
 Harness cold;if(!cold.mosaico_bq_boot_factory_reset(7,65,true)||cold.resets!=1||cold.sec!=3||!cold.intent||cold.forbidden)return false;
 Harness stale;stale.staleInit=true;if(!stale.mosaico_bq_boot_factory_reset(7,65,true)||stale.earlyAccept||stale.time-stale.resetAt<4000000)return false;
 for(unsigned key:{1u,2u}){Harness k;k.unknownKey=key;if(k.mosaico_bq_boot_factory_reset(7,65,true)||k.keys!=key||k.resets||k.sec!=3||k.mosaico_bq_track_writes)return false;}
 Harness sealAck;sealAck.sealUnknown=true;if(sealAck.mosaico_bq_boot_factory_reset(7,65,true)||sealAck.sec!=3||sealAck.seals!=1||sealAck.resets!=1||sealAck.mosaico_bq_track_writes)return false;
 Harness por;por.sec=2;if(!por.mosaico_bq_boot_factory_reset(7,65,true)||por.resets!=1||por.keys||por.sec!=3)return false;
 for(unsigned cap:{65u,130u,3000u}){
  Harness runtime;if(!runtime.mosaico_bq_boot_factory_reset(7,cap,false)||runtime.resets||runtime.keys||runtime.seals)return false;
 }
 Harness c130;if(!c130.mosaico_bq_boot_factory_reset(7,130,true)||c130.resets||c130.keys)return false;
 Harness valid;valid.dc=valid.fcc=65;if(!valid.mosaico_bq_boot_factory_reset(7,65,true)||valid.resets||valid.keys)return false;
 Harness mixed;mixed.fcc=65;if(!mixed.mosaico_bq_boot_factory_reset(7,65,true)||mixed.resets||mixed.keys)return false;
 Harness ack;ack.unknownAck=true;if(ack.mosaico_bq_boot_factory_reset(7,65,true)||ack.resets!=1||ack.seals!=1||ack.sec!=3||!ack.intent)return false;
 Harness timeout;timeout.timeout=true;if(timeout.mosaico_bq_boot_factory_reset(7,65,true)||timeout.resets!=1||timeout.sec!=3||timeout.time-timeout.resetAt>6100000)return false;
 Harness seal;seal.sealFail=true;if(seal.mosaico_bq_boot_factory_reset(7,65,true)||seal.resets!=1||seal.seals!=1||seal.sec!=2)return false;
 Harness unseal;unseal.unsealFail=true;if(unseal.mosaico_bq_boot_factory_reset(7,65,true)||unseal.resets||unseal.keys!=2||unseal.sec!=3)return false;
 Harness failedRead;failedRead.readFail=true;if(failedRead.mosaico_bq_boot_factory_reset(7,65,true)||failedRead.resets||failedRead.keys)return false;
 Harness full;full.sec=1;if(full.mosaico_bq_boot_factory_reset(7,65,true)||full.resets||full.keys)return false;
 Harness post;post.badPostCaps=true;if(post.mosaico_bq_boot_factory_reset(7,65,true)||post.resets!=1||post.sec!=3)return false;
 Harness mode;mode.badPostMode=true;if(mode.mosaico_bq_boot_factory_reset(7,65,true)||mode.resets!=1||mode.sec!=2||mode.seals)return false;
 return true;
}
static_assert(tests());
'''
        result_struct=function((BASE/'mosaico_official_gauge.h').read_text(),'struct MosaicoOfficialGaugeResult')+';'
        code=code.replace('struct Harness {',result_struct+'''
static_assert(!MosaicoOfficialGaugeResult{true,false,false}.resetFailed);
static_assert(!MosaicoOfficialGaugeResult{false,false,false}.resetFailed);
static_assert(MosaicoOfficialGaugeResult{false,false,false,true}.resetFailed);
struct Harness {''')
        code=code.replace('#include <cstddef>', '#include <cstddef>\n#include <initializer_list>')
        # Return failing generated-source line for compact compiler diagnostics.
        start=code.index('constexpr bool tests()')
        body=code[start:].replace('constexpr bool tests()', 'constexpr int tests()').replace('return false;', 'return __LINE__;').replace('return true;', 'return 0;').replace('static_assert(tests());', 'static_assert(tests()==0);')
        code=code[:start]+body
        cc=compiler()
        self.assertIsNotNone(cc, 'target compiler required for helper execution')
        with tempfile.TemporaryDirectory(dir=official.ROOT) as folder:
            p=Path(folder)/'reset.cpp';p.write_text(code)
            result=subprocess.run([cc,'-std=c++20','-fsyntax-only',str(p)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_only_boot_opts_in_and_vendor_bytes_unchanged(self):
        hal=(BASE/'hal_mosaico.cpp').read_text()
        boot=function(hal,'Hal::GaugeBootReloadStatus Hal::gaugeBootReload(')
        self.assertEqual(hal.count('bootFactoryReset && capacity==65'),1)
        self.assertIn('bootFactoryReset=initialColdReset && esp_reset_reason()==ESP_RST_POWERON',boot)
        self.assertIn('gaugeBootReload(gaugeReason, sizeof(gaugeReason), true)',(official.ROOT/'main/hal/hal.cpp').read_text())
        self.assertIn('bool initialColdReset = false',(official.ROOT/'main/hal/hal.h').read_text())
        self.assertIn('gauge_boot_info.attempted = true',boot)
        self.assertIn('gauge_official_apply_locked(capacity,details,sizeof(details),capacity==130,bootFactoryReset && capacity==65)',boot)
        self.assertIn('bool bootFactoryReset = false',(BASE/'mosaico_official_gauge.h').read_text())
        self.assertIn('bool bootFactoryReset = false',function(hal,'bool gauge_official_apply_locked('))
        apply=function((BASE/'mosaico_official_gauge.cpp').read_text(),'MosaicoOfficialGaugeResult mosaico_official_gauge_apply(')
        failed=apply.split('if (!mosaico_bq_boot_factory_reset',1)[1].split('// A successful reset helper',1)[0]
        self.assertIn('return {false, (attempts & 1) != 0, (attempts & 2) != 0, true};',failed)
        self.assertNotIn('bq27220_create',failed)
        outer=function(hal,'bool gauge_official_apply_locked(')
        failed=outer.split('if (official.resetFailed)',1)[1].split('const bool applied',1)[0]
        self.assertIn('return false;',failed)
        self.assertIn('boot_factory_reset_failed_no_profile',failed)
        for command in ('gauge_exit_config','i2c_master_transmit','gauge_word'):
            self.assertNotIn(command,failed)
        self.assertIn('bool resetFailed = false;',(BASE/'mosaico_official_gauge.h').read_text())
        driver=(BASE/'mosaico_official_gauge_driver.c').read_text()
        helper=function(driver,'bool mosaico_bq_boot_factory_reset(')
        self.assertGreater(driver.index(helper),driver.index('#include "vendor/bq27220/bq27220.c"'))
        self.assertEqual(helper.count('bq27220_control(handle, CONTROL_RESET)'),1)
        for marker in ('SUBMITTED','VERIFIED','FAILED'):
            self.assertEqual(helper.count('\"COLD_FACTORY_RESET_V1_'+marker+'\"'),1)
        self.assertNotIn('CONTROL_OCV',helper)
        self.assertNotIn('bq27220_set_parameter',helper)
        self.assertNotIn('CONTROL_EXIT_CFG',helper)
        self.assertEqual(official.profiles()[1]['design_cap'],65)

if __name__=='__main__':unittest.main()
