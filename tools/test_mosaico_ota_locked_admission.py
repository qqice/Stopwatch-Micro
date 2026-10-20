"""Host-only updater admission checks. No serial/device access."""
import types,unittest
from unittest.mock import patch
import mosaico_ota_update as updater

class LockedAdmissionTests(unittest.TestCase):
    def client(self,status='PASS',details='locked=1',expected='power'):
        class Client:
            calls=[]
            def command(self,text,name,*args):
                self.calls.append((text,name))
                assert name==expected
                return types.SimpleNamespace(status=status,details=details)
        return Client()
    def test_legacy_wakes_display(self):
        c=self.client(expected='display-wake')
        self.assertTrue(updater.prepare_display(c))
        self.assertEqual(c.calls,[('debug display-wake','display-wake')])
    def test_locked_mode_reads_only(self):
        c=self.client()
        self.assertTrue(updater.prepare_display(c,True))
        self.assertEqual(c.calls,[('debug power','power')])
    def test_awake_is_not_silently_accepted_or_changed(self):
        c=self.client(details='locked=0')
        self.assertFalse(updater.prepare_display(c,True))
        self.assertEqual(c.calls,[('debug power','power')])
    def test_failed_or_missing_observation_fails_closed(self):
        self.assertFalse(updater.prepare_display(self.client(status='FAIL'),True))
        self.assertFalse(updater.prepare_display(self.client(details=''),True))
    def test_cdc_uart_and_meter_inventory_are_separated(self):
        def port(vid,pid,serial='board'):
            return types.SimpleNamespace(vid=vid,pid=pid,serial_number=serial)
        self.assertTrue(updater.transport_matches(port(0x303a,0x4001)))
        self.assertFalse(updater.transport_matches(port(0x303a,0x4001),True))
        for ids in ((0x1366,0x0105),(0x0403,0x6011)):
            self.assertTrue(updater.transport_matches(port(*ids),True,'board'))
            self.assertFalse(updater.transport_matches(port(*ids),True,'different'))
        self.assertFalse(updater.transport_matches(port(0x10c4,0xea60),True))
        self.assertFalse(updater.transport_matches(port(None,None),True))
    def test_uart_client_uses_crc_and_bounded_wake_preamble(self):
        with patch.object(updater,'DebugClient') as client:
            updater.open_client('COM8',True)
            client.assert_called_once_with('COM8',uart=True,wake_preamble=True)
        with patch.object(updater,'DebugClient') as client:
            updater.open_client('COM12')
            client.assert_called_once_with('COM12',uart=False,wake_preamble=False)

if __name__=='__main__':unittest.main()
