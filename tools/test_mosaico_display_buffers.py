"""Offline paired DMA/LVGL bounds; hardware cadence is not inferred from this."""
import unittest,re
from pathlib import Path
S=(Path(__file__).resolve().parents[1]/'main/hal/mosaico/hal_mosaico.cpp').read_text()
class DisplayBufferTests(unittest.TestCase):
 def test_even_partial_strip_and_paired_transfer_capacity(self):
  r=int(re.search(r'constexpr int Resolution = (\d+)',S)[1]);rows=int(re.search(r'constexpr int DisplayBufferRows = (\d+)',S)[1])
  self.assertEqual(r%rows,0);self.assertEqual(rows%2,0)
  self.assertEqual(r//rows,4)
  self.assertIn('spi.max_transfer_sz = Resolution * DisplayBufferRows * 4;',S)
  self.assertIn('disp_config.buffer_size = Resolution * DisplayBufferRows;',S)
  self.assertIn('disp_config.double_buffer = true;',S)
  self.assertIn('disp_config.flags.buff_spiram = true;',S)
  self.assertEqual(2*r*(rows-40)*2,150*1024)
  self.assertNotIn('disp_config.flags.full_refresh = true',S)
if __name__=='__main__':unittest.main()
