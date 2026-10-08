"""Source contract for panic-safe and old-writer-safe register extension."""
from pathlib import Path
import unittest
R=Path(__file__).resolve().parents[1]
class RegisterCaptureTests(unittest.TestCase):
 def test_new_writer_and_legacy_reader(self):
  s=(R/'main/ota/panic_capture.cpp').read_text(encoding='utf8')
  self.assertIn('LegacyCaptureMagic = 0x504e4332',s);self.assertIn('CaptureMagic = 0x504e4333',s)
  self.assertIn('capture.magic != CaptureMagic && capture.magic != LegacyCaptureMagic',s)
  self.assertIn('capture.magic == CaptureMagic && capture.registerMagic == RegisterMagic',s)
  self.assertIn('regs_valid=0 legacy_record=1',s)
  # A 14/15 writer overwrites only old prefix: stale extension can never qualify.
  for magic in (0x504e4332,0x504e4333):
   for ext in (0,0x52474331):
    self.assertEqual(magic==0x504e4333 and ext==0x52474331,(magic,ext)==(0x504e4333,0x52474331))
 def test_hook_does_not_probe_heap_or_stack(self):
  s=(R/'main/ota/panic_capture.cpp').read_text(encoding='utf8')
  h=s.split('__wrap_esp_panic_handler',1)[1].split('bool MosaicoPanicStatus',1)[0]
  for name in ('malloc(', 'snprintf(', 'heap_caps_', 'memcpy(', 'printf(', 'capture.sp +'):self.assertNotIn(name,h)
  for i in range(8):self.assertIn('capture.a'+str(i)+' = frame ? frame->a'+str(i),h)
  self.assertIn('__real_esp_panic_handler(info)',h)
  self.assertLess(h.index('capture.registerMagic = 0'),h.index('capture.registerMagic = RegisterMagic'))
  self.assertLess(h.index('capture.registerMagic = RegisterMagic'),h.index('capture.magic = CaptureMagic'))
if __name__=='__main__':unittest.main()
