import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import mosaico_settings as settings
from serial_debug_test import Result, recovery_retry_safe

class SettingsConsoleTests(unittest.TestCase):
    def credentials(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            path.write_bytes(raw)
            return settings.load_credentials(str(path))

    def test_credentials_schema_and_unicode(self):
        raw = '{"ssid":"测试 AP","password":"12345678"}'.encode()
        self.assertEqual(json.loads(base64.b64decode(self.credentials(raw))), json.loads(raw))
        for raw in (b'{"ssid":"x","password":"12345678","token":"SECRET"}',
                    b'{"ssid":"x","ssid":"y","password":"12345678"}',
                    b'{"ssid":"x","password":"short"}',
                    b'{"ssid":"\\u0000x","password":""}',
                    b'{"ssid":"x","password":"'+b'z'*64+b'"}', b'x'*577):
            with self.subTest(raw=raw), self.assertRaises((ValueError, UnicodeError)):
                self.credentials(raw)

    def test_bom_and_uart_preamble(self):
        self.credentials(b'\xef\xbb\xbf{"ssid":"x","password":""}')
        with patch.object(settings, 'DebugClient') as factory, patch.object(settings, 'execute', return_value={'status':'PASS'}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(settings.main(['--port','FAKE','--uart','wifi-list']),0)
            factory.assert_called_once_with('FAKE',uart=True,wake_preamble=True)

    def test_ranges_and_required_port(self):
        args = settings.parser().parse_args(['--port','FAKE','set','charge_timeout','600'])
        self.assertEqual(settings.build_command(args),'debug settings set charge_timeout 600 180')
        args.value=601
        with self.assertRaises(ValueError): settings.build_command(args)
        args=settings.parser().parse_args(['--port','FAKE','wifi-restart','CONFIRM'])
        self.assertEqual(settings.build_command(args),'debug settings wifi restart CONFIRM')

    def test_secret_redaction_and_no_retry(self):
        secret='BASE64SECRET'
        class Fake:
            calls=0
            def command(self, command, expected):
                self.calls+=1
                print(command)
                raise TimeoutError(command)
        fake=Fake(); captured=io.StringIO()
        with contextlib.redirect_stdout(captured):
            with self.assertRaises(RuntimeError) as error:
                settings.execute(fake,'debug settings wifi save '+secret)
        self.assertNotIn(secret,str(error.exception)); self.assertNotIn(secret,captured.getvalue())
        self.assertEqual(fake.calls,1)
        for command in ('debug settings wifi save '+secret,'debug settings wifi forget eA==','debug settings wifi restart CONFIRM'):
            self.assertFalse(recovery_retry_safe(command))

    def test_busy_output_is_safe(self):
        class Fake:
            def command(self, command, expected):
                print('DBG UART echo debug settings wifi save SECRET')
                return Result('settings','FAIL','reason=busy no_changes=1 password=SECRET')
        result=settings.execute(Fake(),'debug settings wifi save SECRET')
        self.assertEqual(result,{'status':'FAIL','details':{'no_changes':'1'},'rows':[]})

    def test_list_and_fixed_ble(self):
        class Fake:
            def command(self, command, expected):
                print('DBG WIFI_PROFILE index=0 ssid_b64=eA==')
                print('DBG SETTINGS ble_name_b64=eA== ble_name_mutable=0 wifi_count=1 wifi_scan_error=-5')
                return Result('settings','PASS','pending=0 error=0 scan_error=-5')
        output=settings.execute(Fake(),'debug settings wifi list')
        self.assertEqual(output['rows'][0],{'index':0,'ssid':'x'})
        self.assertEqual(output['rows'][1]['ble_name_mutable'],'0')
        self.assertEqual(output['rows'][1]['wifi_scan_error'],'-5')
        self.assertEqual(output['details']['scan_error'],'-5')

    def test_firmware_owner_and_scrub_contract(self):
        source=(Path(__file__).resolve().parents[1]/'main/debug/serial_debug.cpp').read_text()
        branch=source[source.index('if (command && !std::strcmp(command, "settings"))'):source.index('if (command && std::strcmp(command, "display-settings")')]
        for api in ('requestWifiForget','requestWifiRestart','wifiSettingsSnapshot'):
            self.assertIn(api,branch)
        self.assertNotIn('esp_restart',branch)
        self.assertEqual(branch.count('TouchSleep::active()'),2)
        self.assertEqual(branch.count('MosaicoOta::healthPending()'),2)
        self.assertIn('MosaicoWifi::validCredentials(ssid,password)',source)
        self.assertIn('settingsScrubJson(root)',source)
        self.assertIn('MosaicoWifi::scrub(password,sizeof(password))',source)
        self.assertIn('ble_name_mutable=0',branch)
        self.assertIn('cJSON_GetArraySize(root)==2',source)

if __name__=='__main__': unittest.main()
