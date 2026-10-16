"""Actual source-extracted temperature semantics and nonblocking trace sampling."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
class TraceSamplingTests(unittest.TestCase):
    def compile(self,code):
        if not COMPILERS:self.skipTest('configured cross compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'trace.cpp';p.write_text(code)
            r=subprocess.run([str(COMPILERS[0]),'-std=c++20','-Wall','-Wextra','-Werror','-fsyntax-only',str(p)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
    def telemetry(self):
        h=(ROOT/'main/hal/hal.h').read_text()
        return 'struct BatteryTelemetry {'+h.split('struct BatteryTelemetry {',1)[1].split('    struct MosaicoClockDiagnostics',1)[0]
    def test_actual_temperature_read_does_not_change_old_validity(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        sample='constexpr void sample_battery_locked('+source.split('void sample_battery_locked(',1)[1].split('\nenum class GaugeCommand',1)[0]
        model=(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').read_text()
        exitfn='constexpr bool configExitAccepted('+model.split('bool configExitAccepted(',1)[1].split('inline bool capacityPolicy',1)[0]
        self.compile('#include <cstdint>\nstruct Hal{'+self.telemetry()+'};\nnamespace mosaico_gauge{constexpr uint16_t MaxReasonableFcc=78;'+exitfn+'}\n'+r'''
struct Fake {
 bool gauge=true,battery_valid=false;int64_t battery_sample_us=0;int fail=-1,reads=0;
 Hal::BatteryTelemetry battery_telemetry{};uint8_t battery_soc=0;uint16_t battery_mv=0;int16_t battery_raw_ma=0;
 constexpr int64_t esp_timer_get_time()const{return 10000000;}
 constexpr bool gauge_word(uint8_t reg,uint16_t& out){
  ++reads;if(reg==fail)return false;
  switch(reg){case 0x2c:out=4;break;case 0x08:out=3803;break;case 0x0c:case 0x14:out=70;break;
   case 0x10:out=3;break;case 0x12:case 0x3c:out=65;break;case 0x3a:out=0xa6;break;
   case 0x0a:out=0x5028;break;case 0x2e:out=100;break;case 0x2a:out=11;break;case 0x06:out=2982;break;default:return false;}
  return true;
 }
'''+sample+r'''
};
constexpr bool cases(){
 Fake good;good.sample_battery_locked(true);
 if(!good.battery_telemetry.valid||!good.battery_telemetry.capacityValid||!good.battery_telemetry.nominalConfigured||!good.battery_telemetry.temperatureValid||good.battery_telemetry.temperatureDeciK!=2982||good.reads!=12)return false;
 Fake temp;temp.fail=0x06;temp.sample_battery_locked(true);
 if(!temp.battery_telemetry.valid||!temp.battery_telemetry.capacityValid||!temp.battery_telemetry.nominalConfigured||temp.battery_telemetry.temperatureValid||temp.battery_telemetry.temperatureDeciK)return false;
 Fake core;core.fail=0x08;core.sample_battery_locked(true);if(core.battery_telemetry.valid||!core.battery_telemetry.temperatureValid)return false;
 Fake cap;cap.fail=0x12;cap.sample_battery_locked(true);if(!cap.battery_telemetry.valid||cap.battery_telemetry.capacityValid||!cap.battery_telemetry.temperatureValid)return false;
 return true;
}
static_assert(cases(),"actual read06 validity independent of preexisting core/capacity semantics");
''')
    def test_actual_try_sample_never_waits_and_preserves_output_on_skip(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        method='constexpr bool gaugeTraceTrySample('+source.split('bool Hal::gaugeTraceTrySample(',1)[1].split('\nbool Hal::gaugeSetNominalCapacity(',1)[0]
        method=method.replace('std::','mock::').replace('MosaicoOta::busy()','otaBusy()').replace('MosaicoOta::healthPending()','otaHealth()')
        self.compile('#include <cstdint>\nstruct Hal{'+self.telemetry()+'};\n'+r'''
namespace mock{
 struct mutex{bool available=true;int attempts=0,held=0;};using recursive_mutex=mutex;
 struct Try{};constexpr Try try_to_lock{};
 template<class T>struct unique_lock{T& m;bool owns;constexpr unique_lock(T& value,Try):m(value),owns(value.available){++m.attempts;if(owns)++m.held;}constexpr ~unique_lock(){if(owns)--m.held;}constexpr bool owns_lock()const{return owns;}};
}
struct Fake{
 using BatteryTelemetry=Hal::BatteryTelemetry;
 int mode=0,busyCalls=0,samples=0;bool gauge=true;
 mock::recursive_mutex gauge_transaction_mutex;mock::mutex battery_mutex;
 BatteryTelemetry battery_telemetry{};
 constexpr bool otaBusy(){++busyCalls;return mode==1||(mode==6&&busyCalls>=2);}
 constexpr bool otaHealth(){return mode==2;}
 constexpr void sample_battery_locked(bool fresh){if(fresh)++samples;battery_telemetry.reportedSoc=4;battery_telemetry.temperatureValid=true;}
'''+method+r'''
};
constexpr bool cases(){
 for(int mode=0;mode<=6;++mode){Fake f;f.mode=mode;
  if(mode==3)f.gauge_transaction_mutex.available=false;
  if(mode==4)f.battery_mutex.available=false;
  if(mode==5)f.gauge=false;
  Hal::BatteryTelemetry out{};out.reportedSoc=99;bool ok=f.gaugeTraceTrySample(out);
  if(ok!=(mode==0)||f.gauge_transaction_mutex.held||f.battery_mutex.held)return false;
  if(ok){if(f.samples!=1||out.reportedSoc!=4||!out.temperatureValid)return false;}
  else if(f.samples||out.reportedSoc!=99)return false;
  if((mode==1||mode==2)&&(f.gauge_transaction_mutex.attempts||f.battery_mutex.attempts))return false;
 }
 return true;
}
static_assert(cases(),"actual try-lock/OTA gates skip without sample or output mutation");
''')
if __name__=='__main__':unittest.main()
