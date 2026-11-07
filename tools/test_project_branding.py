"""Product branding must not rename wire identities or signed OTA targets."""
import unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
class BrandingTests(unittest.TestCase):
 def test_display_name(self):
  text=(R/'main/system_config.h').read_text(encoding="utf-8")
  self.assertRegex(text,r'ProductName\[\]\s*=\s*"Mosaico Beacon"')
  self.assertRegex(text,r'BluetoothDeviceName\[\]\s*=\s*"Codex Micro"')
 def test_ble_and_console_default_stay_compatible(self):
  self.assertIn('DeviceName             = system_config::BluetoothDeviceName',(R/'main/hal/ble/codex_micro_ble.cpp').read_text(encoding="utf-8"))
  self.assertIn('settingsBase64(system_config::BluetoothDeviceName,name)',(R/'main/debug/serial_debug.cpp').read_text(encoding="utf-8"))
 def test_signed_project_and_build_identity_unchanged(self):
  self.assertIn('project(Stopwatch-Mosaico)',(R/'boards/mosaico/CMakeLists.txt').read_text(encoding="utf-8"))
  self.assertIn('textEquals(m, "project", "Stopwatch-Mosaico")',(R/'main/ota/mosaico_ota.cpp').read_text(encoding="utf-8"))
  self.assertIn('"project": "Stopwatch-Mosaico"',(R/'tools/ota_release.py').read_text(encoding="utf-8"))
 def test_public_titles(self):
  for name in ['README.md','README.zh-CN.md']:
   self.assertEqual((R/name).read_text(encoding='utf-8').splitlines()[0],'# Mosaico Beacon')
  self.assertIn('<title>Mosaico Beacon — UI Review</title>',(R/'web/index.html').read_text(encoding="utf-8"))
if __name__=='__main__':unittest.main()

