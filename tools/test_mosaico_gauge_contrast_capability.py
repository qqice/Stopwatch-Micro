import base64, copy, hashlib, json, unittest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature
from ota_release import canonical_message
import mosaico_gauge_contrast_capability as c

class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.key=ec.generate_private_key(ec.SECP256R1());self.pub=self.key.public_key()
    def pair(self,version):
        image=bytearray(512);image[48:48+len(version)]=version.encode();image=bytes(image)
        m={'schema':2,'board':'esp-mosaico','chip':'esp32s31','project':'Stopwatch-Mosaico',
           'layout':'mosaico-dual-v2','version':version,'size':len(image),'sha256':hashlib.sha256(image).hexdigest()}
        m['signature']=base64.b64encode(self.key.sign(canonical_message(m['size'],m['sha256'],version,layout=m['layout']),ec.ECDSA(hashes.SHA256()))).decode()
        return image,m,c.endorse(m,image,self.key,self.pub,audited=True)
    def test_positive_pair(self):
        a=self.pair('0.13.95-r15-mosaico-ota');b=self.pair('0.13.95-r16-mosaico-ota')
        wire=c.wire_pair([a[1],b[1]],[a[2],b[2]],self.pub)
        self.assertLessEqual(len(base64.b64encode(json.dumps(wire,separators=(',',':')).encode())),1400)
        self.assertEqual(c.DIGEST,hashlib.sha256(c.CONTRACT.encode()).hexdigest())
    def test_tamper_wrong_contract_or_key(self):
        image,m,cap=self.pair('0.13.95-r15-mosaico-ota')
        for patch in ({'contract':'0'*64},{'size':True},{'version':'0.13.95-r16-mosaico-ota'},
                      {'sha256':'0'*64},{'signature':'AA=='},{'extra':1}):
            bad=copy.deepcopy(cap);bad.update(patch)
            with self.assertRaises((ValueError,InvalidSignature)):c.verify_endorsement(m,bad,self.pub)
        with self.assertRaises(InvalidSignature):c.verify_endorsement(m,cap,ec.generate_private_key(ec.SECP256R1()).public_key())
    def test_no_audit_image_mismatch_or_duplicate(self):
        image,m,cap=self.pair('0.13.95-r15-mosaico-ota')
        with self.assertRaises(ValueError):c.endorse(m,image,self.key,self.pub)
        with self.assertRaises(ValueError):c.endorse(m,image+b'x',self.key,self.pub,audited=True)
        with self.assertRaises(ValueError):c.wire_pair([m,m],[cap,cap],self.pub)
        for version in ('0.13.95-r11-mosaico-ota','0.13.95-r12-mosaico-ota','0.13.95-r13-mosaico-ota','0.13.95-r14-mosaico-ota','0.13.95-r17-mosaico-ota'):
            wrong=copy.deepcopy(m);wrong['version']=version
            with self.assertRaises(ValueError):c.canonical(wrong)
    def test_sdk_free_and_contract_wire_alignment(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        path=root/'main/hal/mosaico/mosaico_gauge_contrast_runtime.inc'
        if path.exists():
            text=path.read_text(encoding='utf8')
            self.assertIn(c.DIGEST,text)
            self.assertIn('MOSAICO-GAUGE-CONTRAST-CAP-v1',text)
        old=(root/'tools/mosaico_factory_scale_capability.py').read_text(encoding='utf8')
        self.assertNotIn('0.13.95-r15-mosaico-ota',old)

if __name__=='__main__':unittest.main()
