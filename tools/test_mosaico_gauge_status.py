"""Closed Control(0x0056), mode decoding and actual HAL boundary tests; no SDK/device."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class GaugeStatusTests(unittest.TestCase):
    def compile(self,code):
        self.assertTrue(COMPILERS,'configured compiler required')
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'status.cpp'; source.write_text(code)
            result=subprocess.run([str(COMPILERS[0]),'-std=c++20','-Wall','-Wextra','-Werror','-fsyntax-only','-I',str(ROOT/'main/hal/mosaico'),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico'/f'{self._testMethodName}.log'
            log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_protocol_mock_and_modes(self):
        self.compile(r'''
#include "mosaico_gauge_status_model.h"
using namespace mosaico_gauge_status;
struct Bus {
 int fault=0,writes=0,reads=0;unsigned delay=0;bool wrong=false;
 constexpr bool transmit(const uint8_t* p,size_t n) {
  ++writes;if(n!=3||p[0]!=0||p[1]!=0x56||p[2]!=0)wrong=true;
  return fault!=1;
 }
 constexpr void delayMs(uint32_t ms){if(ms!=20)wrong=true;delay+=ms;}
 constexpr bool read(uint8_t reg,uint8_t* p,size_t n) {
  ++reads;if(delay!=20||reg!=(reads==1?0x3e:0x40)||n!=2)wrong=true;
  if(reg==0x3e){if(fault==2)return false;p[0]=fault==4?0x55:0x56;p[1]=fault==5?1:0;}
  else {if(fault==3)return false;p[0]=0x40;p[1]=0xa5;}
  return true;
 }
};
constexpr bool cases(){
 for(int fault=0;fault<=5;++fault){Bus bus;bus.fault=fault;uint16_t raw=0xffff,echo=0xffff;uint32_t flags=0xffffffff;
  bool ok=readGaugingStatus(bus,raw,echo,flags);
  if(ok!=(fault==0)||bus.wrong||bus.writes!=1||bus.reads!=(fault==1?0:2))return false;
  uint32_t expected=fault==1?0:uint32_t(TxAck)|(fault==2?0U:uint32_t(EchoRead))|(fault==3?0U:uint32_t(DataRead));
  if(flags!=expected||raw!=((fault==1||fault==3)?0:0xa540))return false;
  if(echo!=((fault==1||fault==2)?0:fault==4?0x0055:fault==5?0x0156:0x0056))return false;
 }
 uint8_t bytes[]={0x56,0,0x34,0x12};uint16_t raw=9;
 for(size_t n=0;n<8;++n){if(decodeResponse(bytes,n,raw)!=(n==4)||raw!=(n==4?0x1234:0))return false;}
 if(decodeResponse(nullptr,4,raw)||raw)return false;
 for(unsigned high=0;high<256;++high){
  if(decodeMode(uint16_t(high<<8),0)!=Charging || decodeMode(uint16_t(high<<8)|0x40,0)!=Relax ||
     decodeMode(uint16_t(high<<8)|0x40,1)!=Discharge || decodeMode(uint16_t(high<<8),1)!=Unknown)return false;
 }
 Snapshot s{};s.gaugingStatus=0xa500;s.batteryStatus=1;s.mode=decodeMode(s.gaugingStatus,s.batteryStatus);
 return s.mode==Unknown&&s.gaugingStatus==0xa500&&s.batteryStatus==1&&s.validMask==0&&sizeof(s)==80&&offsetof(Snapshot,units)==48;
}
static_assert(cases());
''')

    def test_actual_snapshot_partial_validity(self):
        diagnostic='constexpr void gauge_diagnostic_locked('+SOURCE.split('void gauge_diagnostic_locked(',1)[1].split('void sample_battery_locked(',1)[0]
        self.compile(r'''
#include "mosaico_gauge_status_model.h"
#include "mosaico_gauge_physical_model.h"
struct Hal {using GaugeDiagnosticSnapshot=mosaico_gauge_status::Snapshot;};
struct Fake {
 bool gauge=true;int fail=-1;
 constexpr uint64_t esp_timer_get_time(){return 1234;}
 constexpr auto scale_unit_context_locked(){return mosaico_gauge_physical::UnitContext{1,2,3,mosaico_gauge_physical::Phase::Original,true};}
 constexpr bool gauge_word(uint8_t reg,uint16_t& out){
  if(reg==fail)return false;
  out=reg==0x0c?0xfffc:reg==0x14?0xffff:reg==0x0a?1:reg;return true;
 }
 struct GaugeStatusReadTransport {
  constexpr bool transmit(const uint8_t*,size_t){return true;}
  constexpr void delayMs(uint32_t){}
  constexpr bool read(uint8_t reg,uint8_t* p,size_t){p[0]=reg==0x3e?0x56:0x40;p[1]=reg==0x3e?0:0x80;return true;}
 };
'''+diagnostic+r'''
};
constexpr bool cases(){
 using namespace mosaico_gauge_status;
 constexpr uint8_t regs[]={0x2c,0x10,0x12,0x3c,0x08,0x0c,0x14,0x22,0x2a,0x3a,0x0a};
 for(int fail=-1;fail<11;++fail){Fake f;if(fail>=0)f.fail=regs[fail];Snapshot out{};
  out.validMask=0xffffffff;f.gauge_diagnostic_locked(out,BootBefore,2);
  uint32_t expected=AllRaw|ModeValid|TxAck|EchoRead|DataRead;if(fail>=0)expected&=~(1U<<fail);
  if(fail==10)expected&=~ModeValid;
  if(out.validMask!=expected||out.sampledUs!=1234||out.phase!=BootBefore||out.outcome!=2)return false;
  if(fail!=5 && out.currentMa!=-4)return false;
  if(fail!=6 && out.averageCurrentMa!=-1)return false;
  if(fail!=10 && out.mode!=Discharge)return false;
  if(out.rawEcho16!=0x0056)return false;
 }
 Fake absent;absent.gauge=false;Snapshot out{};out.validMask=AllRaw;
 absent.gauge_diagnostic_locked(out,BootAfter,3);
 return out.validMask==0&&out.phase==BootAfter&&out.outcome==3;
}
static_assert(cases());
''')

    def test_actual_hal_closed_lock_order_and_boot(self):
        adapter=SOURCE.split('struct GaugeStatusReadTransport {',1)[1].split('void gauge_diagnostic_locked',1)[0]
        self.assertEqual(adapter.count('i2c_master_transmit('),1)
        self.assertNotIn('gauge_control(',adapter)
        self.assertIn('gauge_delay_ms(ms)',adapter)
        diagnostic=SOURCE.split('void gauge_diagnostic_locked(',1)[1].split('void sample_battery_locked(',1)[0]
        self.assertEqual(diagnostic.count('readGaugingStatus('),1)
        for forbidden in ('gauge_control(', 'gauge_mac_read(', 'nvs_', 'gauge_access_word(', 'sample_battery_locked('):
            self.assertNotIn(forbidden,diagnostic)
        api=SOURCE.split('bool Hal::gaugeDiagnosticSample(',1)[1].split('void Hal::gaugeBootDiagnostic(',1)[0]
        self.assertLess(api.index('transaction(gauge_transaction_mutex)'),api.index('battery(battery_mutex)'))
        self.assertLess(api.index('battery(battery_mutex)'),api.index('gauge_diagnostic_locked('))
        audit=SOURCE.split('bool Hal::gaugeModeAudit(',1)[1].split('Hal::GaugeBootReloadInfo Hal::gaugeBootReloadInfo()',1)[0]
        self.assertIn('return ok && (sample.validMask & mosaico_gauge_status::ModeValid)',audit)
        for field in ('echo=%04x','tx=%u','echoio=%u','dataio=%u'):self.assertIn(field,audit)
        getter=SOURCE.split('void Hal::gaugeBootDiagnostic(',1)[1].split('bool Hal::gaugeModeAudit(',1)[0]
        self.assertNotIn('gauge_diagnostic_locked(',getter)
        self.assertNotIn('gauge_word(',getter)
        ui=SOURCE.split('void sample_battery_locked(',1)[1].split('enum class GaugeCommand',1)[0]
        self.assertNotIn('readGaugingStatus(',ui)
        self.assertNotIn('gauge_diagnostic_locked(',ui)
        boot=SOURCE.split('Hal::GaugeBootReloadStatus Hal::gaugeBootReload(',1)[1].split('void Hal::',1)[0]
        self.assertIn('(operation & 0x20)',boot)
        self.assertIn('!gauge_boot_info.attempted',boot)
        self.assertLess(boot.index('gauge_diagnostic_locked(gauge_boot_before'),boot.index('gauge_store_reload(latch'))
        self.assertIn('gauge_boot_info.status==Status::Deferred || gauge_boot_after.phase',boot)
        self.assertEqual(boot.count('gauge_diagnostic_locked(gauge_boot_after'),1)
        self.assertEqual(boot.count('gauge_diagnostic_locked(gauge_boot_before'),1)

if __name__=='__main__':unittest.main()
