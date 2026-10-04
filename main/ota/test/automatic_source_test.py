"""Read-only HOST/SOURCE tests; no device, flash write, signing, or network call."""
import hashlib
import pathlib
import re
import types
import unittest
from esptool.bin_image import LoadFirmwareImage

ROOT = pathlib.Path(__file__).resolve().parents[3]
SOURCE = (ROOT / 'main/ota/mosaico_ota.cpp').read_text()


def body(start, end):
    return SOURCE.split(start, 1)[1].split(end, 1)[0]


class AutomaticOtaSourceTests(unittest.TestCase):
    def test_actual_power_expression_boundaries(self):
        power = body('bool automaticPowerSafe()', 'bool exactSlot(')
        expression = re.search(r'return (b.valid.*?);', power, re.S)[1]
        expression = ' '.join(expression.replace('&&', 'and').replace('||', 'or').replace('!(', 'not (').split())
        def allowed(valid=True, mv=3900, current=4, mounted=False, operation=6):
            b = types.SimpleNamespace(valid=valid, operationStatus=operation, voltageMv=mv, currentMa=current)
            return eval(expression, {'__builtins__': {}}, {'b': b, 'tud_mounted': lambda: mounted})
        self.assertTrue(allowed())
        self.assertTrue(allowed(current=-10, mounted=True))  # Enumeration policy, not measured VBUS.
        for args in [dict(valid=False), dict(mv=3899, mounted=True), dict(current=3),
                     dict(current=-1), dict(operation=2), dict(operation=6 | 0x400), dict(operation=7)]:
            self.assertFalse(allowed(**args), args)
        self.assertIn('GaugeBootReloadStatus::Critical', power)
        self.assertNotIn('reportedSoc', power)

    def test_due_only_valid_power_and_hour_cadence(self):
        due = body('bool automaticCheckDue()', 'bool requestAutomatic(')
        self.assertLess(due.index('now < nextAutomaticCheck'), due.index('currentReady()'))
        self.assertLess(due.index('currentReady()'), due.index('automaticPowerSafe()'))
        self.assertLess(due.index('automaticPowerSafe()'), due.index('nextAutomaticCheck ='))
        self.assertIn('nextAutomaticCheck = now + 3600000000LL;', due)
        self.assertNotRegex(due, r'wake|wifi_|nvs_set|nvs_commit')

    def test_sdk_image_length_and_whole_bin_hash_model(self):
        sdk = pathlib.Path('C:/esp/v6.1/esp-idf/components/bootloader_support/src/esp_image_format.c').read_text()
        appended = sdk.split('static esp_err_t process_appended_hash_and_sig(', 2)[-1].split('uint32_t sig_block_len', 1)[0]
        self.assertIn('if (data->image.hash_appended)', appended)
        self.assertIn('data->image_len += HASH_LEN;', appended)
        data = (ROOT / '.artifacts/mosaico/ota-build/Stopwatch-Mosaico.bin').read_bytes()
        image = LoadFirmwareImage('esp32s31', data)
        self.assertTrue(image.append_digest)
        length = image.data_length + 32
        self.assertEqual(length, len(data))
        stream = hashlib.sha256()
        for offset in range(0, length, 4096):
            stream.update(data[offset:min(offset + 4096, length)])
        self.assertEqual(stream.hexdigest(), hashlib.sha256(data).hexdigest())
        self.assertNotEqual(stream.hexdigest(), hashlib.sha256(data[:-32]).hexdigest())
        actual = body('bool currentImageHash()', 'bool readAttempt(')
        self.assertIn('ESP_IMAGE_VERIFY_SILENT', actual)
        self.assertIn('offset < metadata.image_len', actual)
        self.assertIn('esp_partition_read(running, offset, buffer, length)', actual)
        self.assertIn('heap_caps_malloc(4096', actual)
        self.assertIn('if (currentHashChecked) return currentHashValid;', actual)
        self.assertNotIn('metadata.image_digest', actual.split('// Hash the ENTIRE release .bin', 1)[0])

    def test_readonly_dedup_and_manual_override(self):
        automatic = body('bool requestAutomatic(', 'bool takeRequest()')
        self.assertIn('std::strcmp(automaticHash, currentHash)', automatic)
        self.assertIn('std::strcmp(automaticHash, attempted)', automatic)
        self.assertLess(automatic.index('currentImageHash()'), automatic.index('requestLocked(true)'))
        self.assertLess(automatic.index('readAttempt(attempted)'), automatic.index('requestLocked(true)'))
        read = body('bool readAttempt(', 'bool recordAttempt()')
        self.assertIn('nvs_open("mosaico_ota", NVS_READONLY', read)
        self.assertIn('length == 65 && lowerHex(attempt)', read)
        self.assertNotRegex(read + automatic, r'nvs_set|nvs_commit|esp_ota_begin')
        manual = body('bool request()', 'bool automaticCheckDue()')
        self.assertIn('requestLocked(false)', manual)
        self.assertNotIn('readAttempt', manual)

    def test_signature_bound_power_gates_and_attempt_commit_before_selector(self):
        begin = body('bool beginManifest(', 'bool writeChunk(')
        self.assertLess(begin.index('if (rc != 0)'), begin.index('automatic_manifest_changed'))
        self.assertIn('std::strcmp(verified, automaticHash)', begin)
        self.assertLess(begin.index('automaticPowerSafe()'), begin.index('esp_ota_begin('))
        finish = body('bool finish()', 'void status(')
        self.assertLess(finish.index('automaticPowerSafe()'), finish.index('recordAttempt()'))
        self.assertLess(finish.index('recordAttempt()'), finish.index('esp_ota_set_boot_partition('))
        self.assertIn('return reject("attempt_commit_failed")', finish)
        write = body('bool recordAttempt()', 'bool requestLocked(')
        self.assertIn('hashHex(expectedHash, attempted)', write)
        self.assertIn('nvs_set_str(handle, "attempt", attempted) == ESP_OK && nvs_commit(handle) == ESP_OK', write)
        self.assertNotRegex(write, r'nvs_erase|gauge|wifi|tail')
        busy = body('const auto progress = [&]() {', '    };')
        self.assertIn('auto_state=%s', busy)
        self.assertNotRegex(busy, r'esp_\w+\(|nvs_|automaticPowerSafe|currentImageHash')


if __name__ == '__main__':
    unittest.main(verbosity=2)
