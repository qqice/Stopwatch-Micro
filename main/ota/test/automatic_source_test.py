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
        power = body('bool automaticPowerSafe(', 'bool installPowerSafe(')
        expression = re.search(r'return (b.valid.*?);', power, re.S)[1]
        expression = ' '.join(expression.replace('&&', 'and').replace('||', 'or').replace('!(', 'not (').split())
        def allowed(valid=True, mv=3900, current=4, mounted=False, operation=6):
            b = types.SimpleNamespace(valid=valid, operationStatus=operation, voltageMv=mv, currentMa=current)
            return eval(expression, {'__builtins__': {}}, {'b': b, 'external': mounted or current > 3})
        self.assertTrue(allowed())
        self.assertTrue(allowed(current=-10, mounted=True))  # Enumeration policy, not measured VBUS.
        for args in [dict(valid=False), dict(mv=3899, mounted=True), dict(current=3),
                     dict(current=-1), dict(operation=2), dict(operation=6 | 0x400), dict(operation=7)]:
            self.assertFalse(allowed(**args), args)
        self.assertIn('GaugeBootReloadStatus::Critical', power)
        self.assertNotIn('reportedSoc', power)

    def test_due_valid_and_hour_cadence_without_power_gate(self):
        due = body('bool automaticCheckDue()', 'bool requestAutomatic(')
        self.assertLess(due.index('now < nextAutomaticCheck'), due.index('currentReady()'))
        self.assertNotIn('automaticPowerSafe()', due)
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

    def test_discovery_verified_before_dedup_and_never_erases(self):
        discovery = body('bool discoverManifest(', 'bool requestAutomatic(')
        self.assertLess(discovery.index('validateDescriptor('), discovery.index('currentImageHash()'))
        self.assertIn('std::strcmp(d.sha, currentHash)', discovery)
        self.assertIn('std::strcmp(d.sha, attempted)', discovery)
        self.assertNotIn('currentVersion', discovery)  # Same version, distinct hash is allowed.
        self.assertNotRegex(discovery, r'esp_ota_begin|nvs_set|nvs_commit')
        self.assertIn('publish(UiStage::Available)', discovery)
        begin = body('bool beginManifest(', 'bool writeChunk(')
        self.assertLess(begin.index('validateDescriptor('), begin.index('approved_manifest_changed'))
        self.assertLess(begin.index('approved_manifest_changed'), begin.index('esp_ota_begin('))
        validator = body('bool validateDescriptor(', 'void healthTimeout(')
        self.assertIn('MOSAICO-OTA-v2', validator)
        self.assertIn('MOSAICO-OTA-v1', validator)
        self.assertIn('seen == (v2 ? 511U : 255U)', validator)
        self.assertIn('mbedtls_pk_verify', validator)
        self.assertNotRegex(validator, r'esp_ota_begin|nvs_set|nvs_commit')

    def test_ui_nonblocking_queue_and_real_verification_split(self):
        copy = body('bool copyUiSnapshot(', 'bool approveUpdate(')
        self.assertIn('snapshotLock, std::try_to_lock', copy)
        self.assertNotRegex(copy, r'esp_|nvs_|guard\(lock')
        approve = body('bool approveUpdate(', 'void deferUpdate(')
        self.assertIn('approvedRequest.store(true)', approve)
        self.assertNotRegex(approve, r'esp_ota_|nvs_|requestLocked')
        take = body('bool takeRequest()', 'void fail(')
        self.assertIn('downloadPowerSafe()', take)
        self.assertIn('publish(UiStage::WaitingPower,', take)
        finish = body('bool finishDownload()', 'bool installVerified()')
        self.assertLess(finish.index('psa_hash_finish'), finish.index('esp_ota_end'))
        self.assertIn('image_version_mismatch', finish)
        self.assertIn('publish(UiStage::ReadyInstall)', finish)
        self.assertNotIn('esp_ota_set_boot_partition', finish)
        install = body('bool installVerified()', 'bool finish()')
        self.assertIn('readySince < 1500000', install)
        self.assertLess(install.index('recordAttempt(power)'), install.index('esp_ota_set_boot_partition('))
        self.assertIn('publish(UiStage::Installing)', install)
        self.assertIn('installSince < 500000', install)
        self.assertIn('publish(UiStage::BootChecking)', SOURCE)
        self.assertIn('publish(UiStage::Complete)', SOURCE)

    def test_approval_binds_displayed_hash_and_pending_deadline(self):
        approve = body('bool approveUpdate(', 'void deferUpdate(')
        self.assertIn('const char* expectedSha', approve)
        self.assertIn('!lowerHex(expectedSha)', approve)
        self.assertIn('std::strcmp(expectedSha, ui.sha256)', approve)
        condition = re.search(r'if \((!guard.owns_lock\(\).*?)\) return false;', approve, re.S)[1]
        condition = condition.replace('!guard.owns_lock()', 'False').replace('busy()', '(active or pending)')
        condition = condition.replace('approvedRequest.load()', 'pending').replace('!lowerHex(expectedSha)', 'not valid')
        condition = condition.replace('std::strcmp(expectedSha, ui.sha256)', 'different')
        condition = condition.replace('!ui.signatureVerified', 'not verified').replace('ui.imageVerified', 'image_verified')
        condition = condition.replace('ui.stage != UiStage::Available', 'stage != "Available"')
        condition = condition.replace('ui.stage != UiStage::WaitingPower', 'stage != "WaitingPower"')
        condition = ' '.join(condition.replace('||', 'or').replace('&&', 'and').split())
        def rejects(expected, shown, active=False, pending=False, verified=True, stage="Available", image_verified=False):
            valid = bool(re.fullmatch('[0-9a-f]{64}', expected))
            return eval(condition, {'__builtins__': {}}, dict(valid=valid, different=expected != shown,
                active=active, pending=pending, verified=verified, stage=stage, image_verified=image_verified))
        a, b = 'a' * 64, 'b' * 64
        self.assertFalse(rejects(a, a))
        self.assertTrue(rejects(a, b))  # A on screen cannot approve newer B.
        self.assertTrue(rejects('a' * 63, a))
        self.assertTrue(rejects(a, a, pending=True))
        self.assertTrue(rejects(a, a, verified=False))
        self.assertTrue(rejects(a, a, image_verified=True, stage="WaitingPower"))
        self.assertLess(approve.index('std::strcmp(expectedSha, ui.sha256)'), approve.index('std::memcpy(approvedHash'))
        self.assertLess(approve.index('requestedAtMs.store'), approve.index('approvedRequest.store(true)'))
        self.assertIn('bool busy() { return active.load() || approvedRequest.load() || checkQueued.load()', SOURCE)
        age = body('uint32_t requestAgeMs()', 'bool request()')
        self.assertIn('return busy()', age)
        take = body('bool takeRequest()', 'void fail(')
        self.assertIn('requestAgeMs() >= 120000', take)
        self.assertIn('reject("request_timeout")', take)
        self.assertIn('approvedRequest.exchange(false)', take)
        self.assertIn('requestLocked(autoInstall.load(), true)', take)
        self.assertIn('if (stage == UiStage::Failed || stage == UiStage::WaitingPower) approvedRequest.store(false)', SOURCE)
        manual = body('bool request()', 'bool automaticCheckDue()')
        self.assertLess(manual.index('busy()'), manual.index('std::memcpy(approvedHash'))
        self.assertNotRegex(take, r'nvs_set|nvs_commit|esp_ota_begin')

    def test_download_battery_guard_and_staged_selection(self):
        power = body('bool downloadPowerSafe()', 'struct InstallPowerEvidence')
        self.assertIn('b.voltageMv >= 3500', power)
        expression = re.search(r'return (b.valid.*?);', power, re.S)[1]
        expression = ' '.join(expression.replace('&&', 'and').replace('!(', 'not (').split())
        def allowed(mv, valid=True, operation=6):
            b = types.SimpleNamespace(valid=valid, operationStatus=operation, voltageMv=mv)
            return eval(expression, {'__builtins__': {}}, {'b': b})
        self.assertTrue(allowed(3500))
        for args in [(3499, True, 6), (4000, False, 6), (4000, True, 2), (4000, True, 7), (4000, True, 0x406)]:
            self.assertFalse(allowed(*args))
        self.assertNotIn('tud_mounted', power)
        self.assertNotIn('currentMa', power)
        finish = body('bool finishDownload()', 'bool installVerified()')
        self.assertIn('active.store(usbBypass || automaticMode)', finish)
        self.assertNotRegex(finish, r'esp_restart|recordAttempt|set_boot_partition')
        install = body('bool installVerified()', 'bool requestCheck()')
        self.assertIn('!installPowerSafe(manual)', install)
        self.assertIn('!installPowerSafe(manual, &power)', install)
        self.assertIn('const bool manual = !(usbBypass || automaticMode)', install)
        self.assertNotIn('esp_restart', install)
        self.assertIn('publish(UiStage::ReadyReboot)', install)
        self.assertIn('active.store(false)', install)
        for start, end in [('bool requestCheck()', 'bool takeCheckRequest()'),
                           ('bool approveInstall(', 'bool requestReboot()'),
                           ('bool requestReboot()', 'bool rebootWithFreshPower()')]:
            queue = body(start, end)
            self.assertIn('snapshotLock, std::try_to_lock', queue)
            self.assertNotRegex(queue, r'batteryTelemetry|esp_ota_|esp_restart|nvs_|requestJson')
        install_queue = body('bool approveInstall(', 'bool requestReboot()')
        self.assertIn('std::strcmp(expectedSha, ui.sha256)', install_queue)
        due = body('bool automaticCheckDue()', 'bool discoverManifest(')
        self.assertIn('imageReady || selected.load()', due)
        # A bounded TWT event-drain loop precedes run(); only the actual network
        # owner loop is relevant to offline INSTALL/REBOOT ordering.
        owner = (ROOT / 'main/host/network_quota.cpp').read_text().split('void NetworkQuota::run()', 1)[1].split('while (true) {', 1)[1]
        self.assertLess(owner.index('processLocalRequests()'), owner.index('esp_wifi_start()'))
        self.assertLess(owner.index('processLocalRequests()'), owner.index('GetTailnetQuota().start()'))

    def test_completed_boot_history_not_a_retained_candidate(self):
        check = body('bool requestCheck()', 'bool takeCheckRequest()')
        self.assertNotIn('|| ui.imageVerified', check)
        condition = re.search(r'if \((!guard.owns_lock\(\).*?)\) return false;', check, re.S)[1]
        condition = condition.replace('!guard.owns_lock()', 'False').replace('busy()', 'busy')
        condition = condition.replace('selected.load()', 'selected').replace('imageReady.load()', 'retained')
        condition = re.sub(r'ui.stage == UiStage::(\w+)', r'stage == "\1"', condition)
        condition = ' '.join(condition.replace('||', 'or').replace('&&', 'and').split())
        def rejects(stage, retained=False, selected=False):
            return eval(condition, {'__builtins__': {}}, dict(busy=False, stage=stage, retained=retained, selected=selected))
        # Both unsigned bootstrap and signed/verified journal boot end at Complete.
        for historical_verified in (False, True):
            self.assertFalse(rejects('Complete'))
            self.assertFalse(rejects('Idle'))
        self.assertFalse(rejects('WaitingPower'))
        self.assertTrue(rejects('WaitingPower', retained=True))
        self.assertTrue(rejects('ReadyInstall', retained=True))
        self.assertTrue(rejects('ReadyReboot', retained=True, selected=True))
        install = body('bool approveInstall(', 'bool requestReboot()')
        self.assertIn('!imageReady.load()', install)
        request = body('bool request()', 'bool automaticCheckDue()')
        self.assertIn('ui.signatureVerified = ui.imageVerified = false', request)
        self.assertIn('ui.sha256[0] = ui.signatureShort[0] = 0', request)
        self.assertLess(request.index('verified.store(false)'), request.index('requestLocked(true)'))
        for start, end in [('bool discoverManifest(', 'bool requestAutomatic('),
                           ('bool beginManifest(', 'bool writeChunk(')]:
            accept = body(start, end)
            self.assertIn('ui.imageVerified = false; verified.store(false)', accept)
        boot = body('void healthPoll(', 'bool rollbackTest()')
        self.assertIn('verified.store(sha[0] != 0)', boot)

    def test_only_manual_check_publishes_busy_checking(self):
        due = body('bool automaticCheckDue()', 'bool discoverManifest(')
        self.assertNotIn('publish(', due)
        self.assertNotIn('checking.store', due)
        manual = body('bool takeCheckRequest()', 'void finishCheck(')
        self.assertIn('checking.store(true)', manual)
        self.assertIn('publish(UiStage::Checking)', manual)

    def test_legacy_boot_facts_without_claimed_verification(self):
        boot = body('void healthPoll(', 'bool rollbackTest()')
        fallback = boot.split('if (!sha[0]) {', 1)[1].split('}', 1)[0]
        self.assertIn('descriptor->version', fallback)
        self.assertIn('candidate = slot(current)', fallback)
        self.assertIn('ui.signatureVerified = sha[0]; ui.imageVerified = sha[0]', boot)


if __name__ == '__main__':
    unittest.main(verbosity=2)
