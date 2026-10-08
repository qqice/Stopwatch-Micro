"""Source contract only; final target map and device acceptance are separate.

With IDF_PATH set, also exercise the installed ldgen parser (no target build).
"""
import os
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRAGMENT = ROOT / 'main/linker/mosaico_ble_rodata.lf'
SYMBOLS = {
    'r_sym_ble_xWK8Hh2AdoTzjH7mpE35',
    'r_sym_ble_0sNsCROob3I5TWDLCYZB',
    'r_sym_ble_vGKFjJHiB3SRmtFpLbPB',
}


class BleRodataContract(unittest.TestCase):
    def test_exact_const_sections_only(self):
        text = FRAGMENT.read_text()
        entries = re.findall(r'^    (\.\S+)$', text, re.MULTILINE)
        self.assertEqual(set(entries), {'.rodata.' + s for s in SYMBOLS})
        self.assertEqual(len(entries), 3)
        self.assertIn('archive: libble_app.a\n', text)
        self.assertIn('    mosaico_ble_rom_memcpy_rodata -> dram0_data\n', text)
        self.assertIn('    * (mosaico_ble_rom_memcpy_data)\n', text)

    def test_registration_is_board_and_target_guarded(self):
        cmake = (ROOT / 'main/CMakeLists.txt').read_text()
        self.assertRegex(cmake, r'if\(MOSAICO_BOARD AND CONFIG_IDF_TARGET_ESP32S31\)\s+'
                         r'list\(APPEND APP_LDFRAGMENTS '
                         r'"\$\{CMAKE_CURRENT_LIST_DIR\}/linker/mosaico_ble_rodata\.lf"\)\s+endif\(\)')
        self.assertEqual(cmake.count('LDFRAGMENTS ${APP_LDFRAGMENTS}'), 1)

    @unittest.skipUnless(os.environ.get('IDF_PATH'), 'set IDF_PATH for real SDK parser test')
    def test_installed_ldgen_literal_placement(self):
        sys.path.insert(0, str(pathlib.Path(os.environ['IDF_PATH']) / 'tools/ldgen'))
        from ldgen.fragments import Mapping, Sections, parse_fragment_file
        from pyparsing import ParseException

        # This fragment is unconditional: no Kconfig evaluator is needed.
        fragments = parse_fragment_file(str(FRAGMENT), None).fragments
        self.assertEqual(len(fragments), 3)
        sections, scheme, mapping = fragments
        self.assertEqual(sections.entries, {'.rodata.' + s for s in SYMBOLS})
        self.assertEqual(scheme.entries, {('mosaico_ble_rom_memcpy_rodata', 'dram0_data')})
        self.assertEqual(mapping.archive, 'libble_app.a')
        self.assertEqual(mapping.entries, {('*', None, 'mosaico_ble_rom_memcpy_data')})
        for entry in sections.entries:
            # Without '+' ldgen expands to the same literal, never a wildcard.
            self.assertEqual(set(Sections.get_section_data_from_entry(entry)), {entry})
        for obj in ('14', '47', '19'):
            with self.assertRaises(ParseException):
                Mapping.ENTRY.parse_string(f'{obj}:r_sym_ble_example (noflash_data)\n', parse_all=True)


if __name__ == '__main__':
    unittest.main()
