"""Host-only updater admission checks. No serial/device access."""
import types,unittest
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

if __name__=='__main__':unittest.main()
