"""Raw pre-gauge splash source safety gates plus compiled shared-model checks."""
import unittest
from pathlib import Path
from preview_mosaico_boot_splash import export
R=Path(__file__).resolve().parents[1]
HAL=(R/'main/hal/hal.cpp').read_text()
S=(R/'main/hal/mosaico/hal_mosaico.cpp').read_text()
class BootSplashTests(unittest.TestCase):
 def test_model_and_preview(self):
  self.assertTrue(export().is_file())
 def test_protected_startup_order(self):
  init=HAL.split('void Hal::init()',1)[1].split('Hal::Diagnostics',1)[0]
  tokens=['pmic_init();','display_init();','display_boot_splash();','BootTiming::GaugeStart','gaugeBootReload(', 'BootTiming::GaugeEnd','ioe_init();','touchpad_init();','lvgl_init();','audio_init();','button_init();']
  offsets=[init.index(t) for t in tokens];self.assertEqual(offsets,sorted(offsets))
  self.assertIn('#ifndef MOSAICO_BOARD\n    delay(50);\n    display_init();',init)
 def test_single_owned_dma_strip_and_verified_drain(self):
  splash=S.split('void Hal::display_boot_splash()',1)[1].split('void Hal::touchpad_init()',1)[0]
  self.assertEqual(splash.count('heap_caps_malloc('),1)
  self.assertIn('MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA',splash)
  self.assertIn('ESP_ERROR_CHECK(esp_lcd_panel_co5300_set_brightness(panel, 0))',splash)
  for forbidden in ['lvgl_port_', 'register_event_callbacks','xTaskCreate','panel_reset','gaugeAccess']:
   self.assertNotIn(forbidden,splash)
  enable=splash.split('void enable()',1)[1]
  self.assertLess(enable.index('disp_on_off(panel, true)'),enable.index('BootTimingMark(BootTiming::FirstFrame)'))
  self.assertLess(enable.index('set_brightness(panel, mosaico_boot_splash::Brightness)'),enable.index('BootTimingMark(BootTiming::FirstFrame)'))
  driver=(R/'boards/mosaico/managed_components/espressif__esp_lcd_co5300/esp_lcd_co5300_spi.c').read_text()
  brightness=driver.split('static esp_err_t co5300_spi_apply_brightness(void *driver_data, uint8_t brightness_percent)\n{',1)[1]
  self.assertIn('tx_param(',brightness)
  io=Path('C:/esp/v6.1/esp-idf/components/esp_lcd/spi/esp_lcd_panel_io_spi.c').read_text()
  tx=io.split('static esp_err_t panel_io_spi_tx_param(esp_lcd_panel_io_t *io, int lcd_cmd, const void *param, size_t param_size)\n{',1)[1].split('static esp_err_t panel_io_spi_rx_param',1)[0]
  self.assertLess(tx.index('spi_device_get_trans_result('),tx.index('spi_device_polling_transmit('))
 def test_lvgl_dark_full_redraw_handoff(self):
  init=S.split('void Hal::lvgl_init()',1)[1].split('void Hal::setTouchIdlePolling',1)[0]
  tokens=['set_brightness(panel, 0)','disp_on_off(panel, false)','pending_boot_display = true','lvgl_port_init(', 'lv_obj_invalidate(lv_screen_active())','lvglUnlock();']
  offsets=[init.index(t) for t in tokens];self.assertEqual(offsets,sorted(offsets))
  self.assertEqual(S.count('esp_lcd_panel_reset(panel)'),1)
if __name__=='__main__':unittest.main()
