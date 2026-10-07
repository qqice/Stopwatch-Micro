"""Offline configured-output sleep selection policy/readback; no devices/builds."""
from pathlib import Path
import subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
C=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
def function(text,signature):
    start=text.index(signature);opening=text.index('{',start);end=opening+1;depth=1
    while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]
class SleepIoTests(unittest.TestCase):
    def test_actual_model_readback_and_unknown_modes(self):
        if not C:self.skipTest('cross compiler unavailable')
        code=r'''
#include "main/hal/mosaico/mosaico_sleep_io_model.h"
using namespace mosaico_sleep_io;
struct Rig {
 Model m;std::array<Read,64> io{};std::array<unsigned,64> reads{};uint64_t writes=0;
 int badBefore=-1,badAfter=-1,badDisable=-1,mutate=-1;
 constexpr Rig(bool swap=false) {
  m.state.enabled=true;
  const int pins[Count]={8,45,56,60,swap?44:42,50,swap?42:44,36,51,35,9};
  const uint16_t signals[Count]={256,256,256,256,256,62,53,55,54,57,56};
  for(unsigned i=0;i<Count;++i) {
   m.add(static_cast<Role>(i),pins[i],signals[i],i>=5,-100);
   io[pins[i]]={0,{1,signals[i],2,false,i<5,i>=5,false,false,false,false},true};
  }
 }
 constexpr void apply() {
  for(unsigned i=0;i<Count;++i)m.apply(static_cast<Role>(i),[this](int pin) {
   ++reads[pin];if(pin==badBefore || (pin==badAfter && reads[pin]==2))return Read{-31,{},false};return io[pin];
  },[this](int pin) {
   writes|=bit(pin);if(pin==badDisable)return -23;io[pin].sleepSelected=false;
   if(pin==mutate)io[pin].normal.drive=3;return 0;
  },-100);
  for(unsigned i=0;i<Count;++i)m.observe(i,io[m.state.pins[i].pin],-100);
 }
};
constexpr bool test() {
 if(isolationSafe(true,false,false)||isolationSafe(false,true,false)||isolationSafe(true,true,false))return false;
 if(!isolationSafe(false,false,false)||!isolationSafe(true,false,true)||!isolationSafe(false,true,true))return false;
 Rig good;good.apply();if(!good.m.ready() || good.writes!=good.m.state.target || good.m.state.beforeSelected!=good.m.state.target || good.m.state.afterSelected || good.m.state.normalSame!=good.m.state.target)return false;
 if(good.m.state.target & (bit(57)|bit(58)|bit(59)|bit(7)|bit(0)|bit(1)))return false;
 // This proves configuration-role plumbing only, not a full physical board-1.2.1 port.
 Rig swapped(true);swapped.apply();if(!swapped.m.ready()||swapped.m.state.panel!=panelAllowed)return false;
 for(int protectedPin : {0,1,7,17,20,57,58,59,63,64,-1}) {
  Model reject;if(reject.add(Role::Clock,protectedPin,53,true,-100)||!reject.state.schemaError||reject.state.target)return false;
 }
 Model duplicate;duplicate.add(Role::Reset,42,256,false,-100);
 if(duplicate.add(Role::Clock,42,53,true,-100)||!duplicate.state.schemaError)return false;
 Rig inputOnly;inputOnly.io[60].normal.output=false;inputOnly.apply();if(inputOnly.m.ready() || inputOnly.writes&bit(60) || !(inputOnly.m.state.unsafe&bit(60)))return false;
 Rig wrongMux;wrongMux.io[44].normal.function=2;wrongMux.apply();if(wrongMux.m.ready() || wrongMux.writes&bit(44))return false;
 Rig wrongSig;wrongSig.io[50].normal.signal=99;wrongSig.apply();if(wrongSig.m.ready() || wrongSig.writes&bit(50))return false;
 Rig floatSPI;floatSPI.io[36].normal.peripheralOE=false;floatSPI.apply();if(floatSPI.m.ready() || floatSPI.writes&bit(36))return false;
 Rig openDrain;openDrain.io[60].normal.openDrain=true;openDrain.apply();if(openDrain.writes&bit(60))return false;
 Rig readError;readError.badBefore=50;readError.apply();if(readError.m.ready() || readError.writes&bit(50) || !(readError.m.state.failed&bit(50)) || readError.m.state.pins[5].beforeRc!=-31)return false;
 Rig afterError;afterError.badAfter=9;afterError.apply();if(afterError.m.ready() || afterError.m.state.pins[10].afterRc!=-31)return false;
 Rig disableError;disableError.badDisable=36;disableError.apply();if(disableError.m.ready() || !(disableError.m.state.applyErrors&bit(36)) || disableError.m.state.pins[7].applyRc!=-23)return false;
 Rig mutation;mutation.mutate=51;mutation.apply();if(mutation.m.ready() || !(mutation.m.state.unsafe&bit(51)) || mutation.m.state.normalSame&bit(51))return false;
 // A live read-only report can revoke readiness, without another disable call.
 const auto writes=good.writes;good.io[60].sleepSelected=true;good.m.observe(3,good.io[60],-100);
 if(good.m.ready() || writes!=good.writes || !(good.m.state.currentSelected&bit(60)))return false;
 good.m.observe(3,Read{-44,{},false},-100);return good.m.state.pins[3].current==-1 && good.m.state.pins[3].currentRc==-44;
}
static_assert(test(),"only configured known outputs, exact signal/mux validation, unchanged normal config, sticky readback fail closed");
'''
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'io.cpp';p.write_text(code)
            q=subprocess.run([str(C[-1]),'-std=c++17','-fsyntax-only','-I'+str(R),str(p)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico/sleep-io-model.log';log.write_text(q.stdout+q.stderr)
            self.assertEqual(q.returncode,0,str(log))
    def test_source_only_sleep_selection_and_registered_readonly_report(self):
        hal=(R/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        helper=function(hal,'void apply_sleep_io(')
        reader=function(hal,'mosaico_sleep_io::Read read_sleep_io(')
        getter=function(hal,'Hal::SleepIoInfo Hal::sleepIoRetentionInfo()')
        self.assertIn('gpio_sleep_sel_dis(',helper);self.assertIn('gpio_get_io_config(',reader)
        for text in (helper,reader,getter):
            for forbidden in ('gpio_set_level','gpio_set_direction','gpio_config(','gpio_hold','gpio_func_sel','gpio_matrix','gpio_reset','esp_sleep_enable_gpio_switch'):
                self.assertNotIn(forbidden,text)
        self.assertIn('info=sleep_io_model.state; }',getter)
        self.assertGreater(getter.index('StandbySleep::cancelForActivity()'),getter.index('info=sleep_io_model.state; }'))
        self.assertIn('sleep_io_ready.store(sleep_io_model.state.ready,std::memory_order_release)',hal)
        for token in ('unused.pin_bit_mask','rail.pin_bit_mask','config.reset_gpio_num','io.cs_gpio_num','spi.sclk_io_num','spi.data0_io_num','spi.data1_io_num','spi.data2_io_num','spi.data3_io_num'):
            self.assertIn(token,hal)
        self.assertIn('#elif CONFIG_ESP_SLEEP_GPIO_RESET_WORKAROUND || CONFIG_PM_SLP_DISABLE_GPIO\n    return false;',hal)
        standby=(R/'main/host/standby_sleep.cpp').read_text()
        self.assertIn('(!(CONFIG_ESP_SLEEP_GPIO_RESET_WORKAROUND || CONFIG_PM_SLP_DISABLE_GPIO) || CONFIG_MOSAICO_SLEEP_IO_RETENTION)',standby)
        for signature in ('bool allow(','bool request(','bool enableAutomatic('):
            self.assertIn('GetHAL().sleepIoRetentionReady()',function(standby,signature))
        serial=(R/'main/debug/serial_debug.cpp').read_text()
        report=serial.split('!std::strcmp(command,"sleep-io")',1)[1].split('!std::strcmp(command,"standby-sleep")',1)[0]
        self.assertIn('report_only_no_arguments',report);self.assertIn('char details[1400]',report)
        self.assertIn('GetHAL().sleepIoRetentionInfo()',report);self.assertNotIn('gpio_sleep_sel_dis',report)
        self.assertIn('"sleep-io"',(R/'main/debug/serial_debug_transport.h').read_text())
        kconfig=(R/'main/Kconfig.projbuild').read_text().split('config MOSAICO_SLEEP_IO_RETENTION',1)[1]
        self.assertIn('default n',kconfig)
if __name__=='__main__':unittest.main()
