"""Source guards for the S31 APP-only mitigation, not SDK/hardware proof."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HAL = (ROOT / 'main/hal/mosaico/hal_mosaico.cpp').read_text()


class LcdSameCoreTests(unittest.TestCase):
    def test_s31_only_lvgl_affinity(self):
        startup = HAL.split('void Hal::lvgl_init()', 1)[1]
        startup = startup.split('config.task_max_sleep_ms', 1)[0]
        self.assertRegex(startup, re.compile(
            r'#if CONFIG_IDF_TARGET_ESP32S31\s*'
            r'(?://[^\n]*\n\s*)*config\.task_affinity = 0;\s*'
            r'#else\s*config\.task_affinity = 1;\s*#endif'))

    def test_lcd_isr_still_initialized_synchronously_from_main(self):
        main = (ROOT / 'main/main.cpp').read_text().split(
            'extern "C" void app_main(void)', 1)[1]
        self.assertIn('GetHAL().init();', main)
        init = (ROOT / 'main/hal/hal.cpp').read_text().split(
            'void Hal::init()', 1)[1].split('Hal::Diagnostics', 1)[0]
        self.assertLess(init.index('display_init();'), init.index('lvgl_init();'))
        self.assertNotIn('xTaskCreate', init)
        display = HAL.split('void Hal::display_init()', 1)[1].split(
            'void Hal::touchpad_init()', 1)[0]
        self.assertIn('spi_bus_config_t spi{};', display)
        self.assertIn('spi_bus_initialize(SPI2_HOST, &spi, SPI_DMA_CH_AUTO)', display)
        self.assertNotIn('isr_cpu_id', display)  # Zero-init retains SDK AUTO.
        self.assertNotIn('xTaskCreate', display)

    def test_board_defaults_do_not_move_main_off_cpu0(self):
        # CPU0 is the SDK default; build-time overrides still need separate review.
        defaults = (ROOT / 'boards/mosaico/sdkconfig.defaults').read_text()
        self.assertNotRegex(defaults, r'CONFIG_ESP_MAIN_TASK_AFFINITY_(CPU1|NO_AFFINITY)=y')
        affinity = re.search(r'^CONFIG_ESP_MAIN_TASK_AFFINITY=(.+)$', defaults, re.M)
        if affinity:
            self.assertEqual(affinity[1], '0x0')


if __name__ == '__main__':
    unittest.main()
