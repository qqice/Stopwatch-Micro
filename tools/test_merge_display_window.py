"""Bounded, device-free checks of the actual display fence and delta integration."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
OUT=ROOT/'.artifacts/private/mosaico/display-window-tests-001'
OUT.mkdir(parents=True,exist_ok=True)
HAL=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
DELTA=(ROOT/'main/ota/mosaico_delta.cpp').read_text()
BEGIN='// BEGIN MERGE DISPLAY FLASH WINDOW'
END='// END MERGE DISPLAY FLASH WINDOW'

class MergeWindowTests(unittest.TestCase):
    def test_hal_only_inserted_block(self):
        before=ROOT/'.artifacts/private/mosaico/display-window-before-001'
        raw=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_bytes()
        start=raw.index(BEGIN.encode()); end=raw.index(END.encode())+len(END)
        newline=b'\r\n' if b'\r\n' in raw else b'\n'
        self.assertEqual(raw[end:end+2*len(newline)],newline*2)
        self.assertEqual(raw[:start]+raw[end+2*len(newline):],(before/'hal_mosaico.cpp').read_bytes())
        self.assertEqual(hashlib.sha256((before/'hal_mosaico.cpp').read_bytes()).hexdigest(),(before/'SHA256.txt').read_text().split()[0])

    def test_actual_hal_mock_paths_constexpr(self):
        body=HAL.split(BEGIN,1)[1].split(END,1)[0]
        body=body.replace('bool Hal::','constexpr bool Hal::').replace('void Hal::','constexpr void Hal::')
        code=r"""
namespace std {enum memory_order {memory_order_acquire};}
struct Ready {bool value=true;constexpr bool load(std::memory_order) const {return value;}} port_ready;
bool display=true; int panel_io=1;
static constexpr int ESP_OK=0;
bool lockOk=true;int txResult=0,locks=0,unlocks=0,drains=0,sequence=0;
bool valid=true,held=false;
constexpr bool lvgl_port_lock(int ms) {valid=valid&&ms==100&&sequence==0;++locks;sequence=1;held=lockOk;return lockOk;}
constexpr void lvgl_port_unlock() {valid=valid&&held;held=false;++unlocks;sequence=3;}
constexpr int esp_lcd_panel_io_tx_param(int io,int cmd,const void* param,int size) {
 valid=valid&&held&&sequence==1&&io==panel_io&&cmd==-1&&param==nullptr&&size==0;
 ++drains;sequence=2;return txResult;
}
struct Hal {constexpr bool beginDisplayFlashWindow();constexpr void endDisplayFlashWindow();};
"""
        # Globals cannot be modified by constant evaluation. Put mocks and the
        # extracted method bodies inside one test object; no body substitution.
        code=code.replace('namespace std {enum memory_order {memory_order_acquire};}', 'namespace std {enum memory_order {memory_order_acquire};}\nstruct Test {')
        code=code.replace('struct Hal {constexpr bool beginDisplayFlashWindow();constexpr void endDisplayFlashWindow();};','')
        code+=body.replace('Hal::','')+r"""
constexpr bool check(int fault) {
 if(fault==1)port_ready.value=false;if(fault==2)display=false;if(fault==3)panel_io=0;
 if(fault==4)lockOk=false;if(fault==5)txResult=-1;
 const bool ok=beginDisplayFlashWindow();
 if(fault<4&&fault>0)return !ok&&!locks&&!unlocks&&!drains;
 if(fault==4)return !ok&&locks==1&&!unlocks&&!drains&&!held&&valid;
 if(fault==5)return !ok&&locks==1&&unlocks==1&&drains==1&&!held&&valid;
 if(!ok||!held||unlocks||drains!=1)return false;
 endDisplayFlashWindow();return valid&&!held&&unlocks==1;
}
};
static_assert(Test{}.check(0));static_assert(Test{}.check(1));static_assert(Test{}.check(2));
static_assert(Test{}.check(3));static_assert(Test{}.check(4));static_assert(Test{}.check(5));
"""
        source=OUT/'hal_mock.cpp';source.write_text(code)
        result=subprocess.run([str(COMPILER),'-std=c++20','-fsyntax-only',str(source)],capture_output=True,text=True)
        (OUT/'hal-mock.log').write_text(result.stdout+result.stderr)
        self.assertEqual(result.returncode,0,str(OUT/'hal-mock.log'))

    def test_callback_and_window_contract(self):
        self.assertEqual(DELTA.count('if(!d.displayHeld||d.cancelled||'),2)
        self.assertIn('if(!displayHeld)return false;',DELTA)
        self.assertIn('std::min<uint32_t>(256,plan.size-offset)',DELTA)
        self.assertIn('if(d.displayHeld) {d.displayHeld=false;GetHAL().endDisplayFlashWindow();}',DELTA)
        self.assertIn('if(!window){ok=false;d.cancelled=true;}',DELTA)
        self.assertIn('const bool finalized=esp_delta_ota_finalize(decoder)==ESP_OK;',DELTA)
        self.assertIn('if(ok)ok=finalized&&d.produced==target.size&&d.flush()&&d.written==target.size;',DELTA)
        self.assertNotIn('StorageAdmission',DELTA)
        sdk=Path('C:/esp/v6.1/esp-idf/components/esp_lcd/spi/esp_lcd_panel_io_spi.c').read_text()
        tx=sdk.split('static esp_err_t panel_io_spi_tx_param(',2)[-1]
        self.assertLess(tx.index('spi_device_get_trans_result'),tx.index('if (send_cmd)'))
        self.assertIn('bool send_cmd = (lcd_cmd != -1);',tx)

    def test_production_decoder_syntax(self):
        entries=json.loads((ROOT/'.artifacts/mosaico/ota-build/compile_commands.json').read_text())
        entry=next(e for e in entries if e['file'].replace('\\','/').endswith('/mosaico_ota.cpp'))
        command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
        command=command.replace(entry['file'],str(ROOT/'main/ota/mosaico_delta.cpp'))
        command=command.replace(' -fsyntax-only ', ' -I'+str(ROOT/'components/esp_delta_ota/include')+' -fsyntax-only ')
        result=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
        (OUT/'decoder-syntax.log').write_text(result.stdout+result.stderr)
        self.assertEqual(result.returncode,0,str(OUT/'decoder-syntax.log'))

if __name__=='__main__':unittest.main()
