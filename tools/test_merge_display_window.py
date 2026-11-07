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
using int64_t=long long;using uint32_t=unsigned;
struct Test {
struct Ready {bool value=true;constexpr bool load(std::memory_order) const {return value;}} port_ready;
bool display=true; int panel_io=1;
static constexpr int ESP_OK=0;
using esp_err_t=int;
#define ESP_LOGW(...) ((void)0)
int fault=0,txResult=0,locks=0,unlocks=0,drains=0,yields=0;
int64_t now=0,deadline=250000;
bool valid=true,held=false;
constexpr int64_t esp_timer_get_time() {return now;}
constexpr void vTaskDelay(int ticks) {valid=valid&&!held&&ticks==1;++yields;now+=1000;}
constexpr bool lvgl_port_lock(uint32_t ms) {
 valid=valid&&!held&&ms>0&&ms<=100&&ms<=(deadline-now)/1000;++locks;
 if(fault==4||(fault==6&&locks<=2)){now+=ms*1000;return false;}
 held=true;if(fault==9)now=deadline;return true;
}
constexpr void lvgl_port_unlock() {valid=valid&&held;held=false;++unlocks;}
constexpr int esp_lcd_panel_io_tx_param(int io,int cmd,const void* param,int size) {
 valid=valid&&held&&now<deadline&&io==panel_io&&cmd==-1&&param==nullptr&&size==0;
 ++drains;if(fault==10)now=deadline;return txResult;
}
"""
        # Extract the actual production bodies; only mocks execute at compile time.
        code+=body.replace('Hal::','')+r"""
constexpr bool check(int f) {
 fault=f;
 if(fault==1)port_ready.value=false;if(fault==2)display=false;if(fault==3)panel_io=0;
 if(fault==5)txResult=-1;
 if(fault==7)now=deadline;
 if(fault==8)now=deadline-999;
 if(fault==11)now=deadline-1000;
 const bool ok=beginDisplayFlashWindow(deadline);
 if(fault<4&&fault>0)return !ok&&!locks&&!unlocks&&!drains;
 if(fault==4)return !ok&&locks==3&&yields==3&&!unlocks&&!drains&&!held&&valid;
 if(fault==5||fault==10)return !ok&&locks==1&&unlocks==1&&drains==1&&!held&&valid;
 if(fault==7||fault==8)return !ok&&!locks&&!unlocks&&!drains&&!held&&valid;
 if(fault==9)return !ok&&locks==1&&unlocks==1&&!drains&&!held&&valid;
 if(!ok||!held||unlocks||drains!=1)return false;
 if(fault==6&&!(locks==3&&yields==2))return false;
 endDisplayFlashWindow();return valid&&!held&&unlocks==1;
}
};
static_assert(Test{}.check(0));static_assert(Test{}.check(1));static_assert(Test{}.check(2));
static_assert(Test{}.check(3));static_assert(Test{}.check(4));static_assert(Test{}.check(5));
static_assert(Test{}.check(6));static_assert(Test{}.check(7));static_assert(Test{}.check(8));
static_assert(Test{}.check(9));static_assert(Test{}.check(10));static_assert(Test{}.check(11));
"""
        source=OUT/'hal_mock.cpp';source.write_text(code)
        result=subprocess.run([str(COMPILER),'-std=c++20','-fsyntax-only',str(source)],capture_output=True,text=True)
        (OUT/'hal-mock.log').write_text(result.stdout+result.stderr)
        self.assertEqual(result.returncode,0,str(OUT/'hal-mock.log'))

    def test_callback_and_window_contract(self):
        self.assertNotIn('displayHeld',DELTA)
        self.assertIn('if(cancelled||esp_timer_get_time()>=deadline)return false;',DELTA)
        self.assertIn('held(GetHAL().beginDisplayFlashWindow(deadline))',DELTA)
        self.assertIn('heap_caps_malloc(4096,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)',DELTA)
        self.assertIn('~Decoder() {if(sourcePage)heap_caps_free(sourcePage);}',DELTA)
        self.assertIn('std::min<size_t>(4096,d.baseSize-pageOffset)',DELTA)
        self.assertIn('if(!d.sourcePageLength||d.sourcePageOffset!=pageOffset)',DELTA)
        self.assertIn('std::min<uint32_t>(256,plan.size-offset)',DELTA)
        self.assertEqual(DELTA.count('DisplayFlashWindow window('),2)
        self.assertNotIn('DisplayFlashWindow',DELTA.split('bool reconstruct(',1)[1])
        self.assertIn('~DisplayFlashWindow() {if(held)GetHAL().endDisplayFlashWindow();}',DELTA)
        self.assertIn('const bool finalized=esp_delta_ota_finalize(decoder)==ESP_OK;',DELTA)
        self.assertIn('ok=finalized&&d.produced==target.size&&d.flush()&&d.written==target.size;',DELTA)
        self.assertEqual(DELTA.count('vTaskDelay(1);'),2)
        for reason in ('notready','locktimeout','drainerror'):
            self.assertIn('reason='+reason,HAL)
        self.assertIn('reconstruct_failed reason=%s offset=%u prod=%u written=%u',DELTA)
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
        # Historical evidence came from the prior checkout; preserve SDK flags.
        old='C:/Workspace/Stopwatch-Micro'
        def relocate(value):
            return value.replace(old,str(ROOT).replace('\\','/')).replace(old.replace('/','\\'),str(ROOT))
        command=relocate(command)
        # Response files contain a checkout-bound specs path too. Expand only in
        # the syntax command, leaving retained historical build evidence intact.
        command=re.sub(r'@"([^"\n]+)"',lambda m:relocate(Path(m[1]).read_text()).replace('\n',' '),command)
        directory=relocate(entry['directory'])
        result=subprocess.run(command,cwd=directory,capture_output=True,text=True)
        (OUT/'decoder-syntax.log').write_text(result.stdout+result.stderr)
        self.assertEqual(result.returncode,0,str(OUT/'decoder-syntax.log'))

if __name__=='__main__':unittest.main()
