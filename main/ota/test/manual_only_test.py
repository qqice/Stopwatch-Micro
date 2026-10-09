"""Execute extracted C++ policy at compile time; no firmware build or device IO."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = (ROOT / 'main/ota/mosaico_ota.cpp').read_text(encoding='utf8')


class ManualOnlyTests(unittest.TestCase):
    def test_actual_function_constant_execution_without_dependencies(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
            '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers, 'existing embedded compiler required')
        start = SOURCE.index('bool automaticCheckDue()')
        actual = SOURCE[start:SOURCE.index('\n}', start) + 2]
        # No SDK/state stubs: any clock, admission, timer, UI, busy or state
        # reference fails compilation. Execute the exact body, not a model.
        self.assertEqual(' '.join(re.sub(r'//[^\n]*', '', actual).split()),
                         'bool automaticCheckDue() { return false; }')
        code = 'constexpr ' + actual + '''
constexpr bool repeat() {
    for (int i = 0; i < 10000; ++i)
        if (automaticCheckDue()) return false;
    return true;
}
static_assert(repeat(), "background discovery must always be false");
'''
        log = ROOT / '.artifacts/mosaico/ota-manual-only-harness.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'manual_only.cpp'
            source.write_text(code, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17',
                                     '-fsyntax-only', str(source)],
                                    capture_output=True, text=True)
        log.write_text(result.stdout + result.stderr +
                       f'\nreturncode={result.returncode}\n', encoding='utf8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manual_queues_bypass_and_owner_wiring_retained(self):
        owner = (ROOT / 'main/host/network_quota.cpp').read_text(encoding='utf8')
        self.assertIn('MosaicoOta::takeCheckRequest() || MosaicoOta::automaticCheckDue()', owner)
        queue = SOURCE.split('bool requestCheck()', 1)[1].split('bool takeCheckRequest()', 1)[0]
        take = SOURCE.split('bool takeCheckRequest()', 1)[1].split('void finishCheck(', 1)[0]
        self.assertIn('checkQueued.store(true)', queue)
        self.assertIn('checkQueued.exchange(false)', take)
        self.assertIn('checking.store(true)', take)
        self.assertIn('publish(UiStage::Checking)', take)
        serial = (ROOT / 'main/debug/serial_debug.cpp').read_text(encoding='utf8')
        for call in ('requestCheck()', 'approveUpdate(sha)', 'approveInstall(sha)',
                     'requestReboot()', 'request()'):
            self.assertIn('MosaicoOta::' + call, serial)
        self.assertIn('CONFIRM_EXTERNAL_POWER', serial)
        self.assertIn('usbBypass = accepted;', SOURCE)
        status = SOURCE.split('void status(', 1)[1].split('void healthPoll(', 1)[0]
        self.assertIn('automatic_check=0', status)
        self.assertLess(status.index('automatic_check=0'), status.index('if (busy()) return;'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
