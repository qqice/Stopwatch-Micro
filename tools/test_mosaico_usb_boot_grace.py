"""Normal boot need not wait for a host; fault boots retain ROM recovery time."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILERS = list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))


class UsbBootGraceTests(unittest.TestCase):
    def test_actual_reset_policy(self):
        if not COMPILERS: self.skipTest('cross compiler unavailable')
        source = (ROOT/'main/hal/mosaico/usb_console.c').read_text()
        helper = source.split('static bool boot_recovery_grace_required', 1)[1].split('void mosaico_console_init', 1)[0]
        code = '''using esp_reset_reason_t=int;
enum {ESP_RST_POWERON=1, ESP_RST_SW=3};
constexpr bool boot_recovery_grace_required''' + helper + '''
constexpr bool cases() {
 for(int reason=0;reason<256;++reason) {
  if(boot_recovery_grace_required(reason)!=(reason!=1 && reason!=3))return false;
 }
 return boot_recovery_grace_required(-1);
}
static_assert(cases(),"panic/watchdog/brownout/unknown retain grace");
'''
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)/'grace.cpp';src.write_text(code)
            result = subprocess.run([str(COMPILERS[0]), '-std=c++17', '-fsyntax-only', str(src)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_installed_before_conditional_grace(self):
        source = (ROOT/'main/hal/mosaico/usb_console.c').read_text()
        init = source.split('void mosaico_console_init(void)', 1)[1]
        self.assertLess(init.index('xTaskCreate(recovery_watch'), init.index('tinyusb_driver_install'))
        self.assertLess(init.index('tinyusb_console_init'), init.index('boot_recovery_grace_required(reset_reason)'))
        self.assertIn('if (boot_recovery_grace_required(reset_reason))\n        vTaskDelay(pdMS_TO_TICKS(3000));', init)
        self.assertIn('ulTaskNotifyTake(pdTRUE, portMAX_DELAY)', source)
        self.assertIn('bit_rate == 1200', source)


if __name__ == '__main__':
    unittest.main()
