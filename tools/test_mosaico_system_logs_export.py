import base64,struct,types,unittest,zlib
from unittest.mock import patch
import mosaico_system_logs as m

def page(seq=5):
    p=bytearray(2048)
    struct.pack_into('<IHHQIH',p,0,m.PAGE_MAGIC,1,60,seq,238,1)
    struct.pack_into('<I',p,24,m.crc(p[:24]))
    struct.pack_into('<HHQQ11I',p,60,2,1,45000,1792410000,
                     8191,3<<16,96,62,65,65,3900,((-27)&65535)|(((-28)&65535)<<16),65470,0x402900b6,0x8040)
    struct.pack_into('<I',p,2044,m.crc(p[:2044]))
    return bytes(p)

class ExportTests(unittest.TestCase):
    def test_roundtrip_and_fail_closed(self):
        p=page();r=m.decode_page(p,5)[0]
        self.assertEqual(r['gauge']['current_ma'],-27)
        self.assertEqual(r['gauge']['average_current_ma'],-28)
        self.assertEqual(r['gauge']['soc'],96)
        self.assertEqual(r['page_boot'],238)
        for at in (0,4,8,16,20,22,24,30,60,2043,2044):
            damaged=bytearray(p);damaged[at]^=1
            with self.assertRaises(ValueError):m.decode_page(damaged,5)
        with self.assertRaises(ValueError):m.decode_page(p,6)
        with self.assertRaises(ValueError):m.decode_page(p[:-1],5)

    def test_chunk_retry_is_idempotent_and_crc_checked(self):
        p=page()
        class Client:
            calls=[];bad=True
            def command(self,text,expected,timeout):
                self.calls.append(text)
                if ' read ' in text:return types.SimpleNamespace(status='PASS',details='action=read_queued')
                i=int(text.split()[-1]);chunk=p[i*128:(i+1)*128]
                if self.bad:
                    self.bad=False
                    return types.SimpleNamespace(status='PASS',details='seq=5 chunk=0 bytes=128 total=2048 page_crc=0 chunk_crc=0 data=AA==')
                return types.SimpleNamespace(status='PASS',details=f'seq=5 chunk={i} bytes=128 total=2048 page_crc={m.crc(p[:2044]):08x} chunk_crc={m.crc(chunk):08x} data='+base64.b64encode(chunk).decode())
        c=Client();self.assertEqual(m.read_page(c,5),p)
        self.assertEqual(c.calls[1],c.calls[2])
        self.assertTrue(all('debug syslog read ' in x or 'debug syslog chunk ' in x for x in c.calls))

    def test_failed_read_does_not_hang(self):
        class Client:
            def command(self,text,expected,timeout):
                return types.SimpleNamespace(status='PASS',details='queued=1') if ' read ' in text else types.SimpleNamespace(status='BUSY',details='read_state=3 error=257')
        with self.assertRaises(RuntimeError):m.read_page(Client(),5)

if __name__=='__main__':unittest.main()
