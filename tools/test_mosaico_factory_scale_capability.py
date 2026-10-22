import base64
import copy
import hashlib
import json
from pathlib import Path
import unittest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
import mosaico_factory_scale_capability as c
from ota_release import canonical_message


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.key=ec.generate_private_key(ec.SECP256R1());self.pub=self.key.public_key()

    def pair(self,version):
        image=bytearray(512);image[48:48+len(version)]=version.encode();image=bytes(image)
        m={'schema':2,'board':'esp-mosaico','chip':'esp32s31','project':'Stopwatch-Mosaico',
           'layout':'mosaico-dual-v2','version':version,'size':len(image),'sha256':hashlib.sha256(image).hexdigest()}
        m['signature']=base64.b64encode(self.key.sign(canonical_message(m['size'],m['sha256'],version,layout=m['layout']),ec.ECDSA(hashes.SHA256()))).decode()
        return image,m,c.endorse(m,image,self.key,self.pub,audited=True)

    def test_real_p256_positive_and_compact_crc_wire_size(self):
        a=self.pair('0.13.95-r9-mosaico-ota');b=self.pair('0.13.95-r10-mosaico-ota')
        proof=c.pack([(a[1],a[2]),(b[1],b[2])],self.pub)
        self.assertLessEqual(len(proof),1400)
        self.assertLess(len('uart 12345678 debug gauge-factory-scale images '+proof+'\n'),1536)
        wire=json.loads(base64.b64decode(proof))
        self.assertEqual([set(x) for x in wire],[{'image','signature'}]*2)
        for image,m,cap in (a,b):
            self.assertIsNone(c.verify_endorsement(m,cap,self.pub))
            self.pub.verify(base64.b64decode(cap['signature']),c.canonical(m),ec.ECDSA(hashes.SHA256()))

    def test_wrong_key_tamper_replay_or_no_audit_rejected(self):
        image,m,cap=self.pair('0.13.95-r9-mosaico-ota')
        changes=[{'contract':'0'*64},{'version':'0.13.95-r10-mosaico-ota'},{'size':m['size']+1},
                 {'size':True},{'sha256':'0'*64},{'signature':'AA=='},{'signature':'!bad'},
                 {'signature':base64.b64encode(bytes(70)).decode()},{'extra':True}]
        for patch in changes:
            bad=copy.deepcopy(cap);bad.update(patch)
            with self.subTest(patch=list(patch)), self.assertRaises((ValueError,InvalidSignature)):
                c.verify_endorsement(m,bad,self.pub)
        with self.assertRaises(InvalidSignature):c.verify_endorsement(m,cap,ec.generate_private_key(ec.SECP256R1()).public_key())
        with self.assertRaises(ValueError):c.endorse(m,image,self.key,self.pub,audited=False)
        with self.assertRaises(ValueError):c.endorse(m,image+b'X',self.key,self.pub,audited=True)
        wrong=bytearray(image);wrong[48]^=1
        with self.assertRaisesRegex(ValueError,'descriptor version'):c.descriptor_version(bytes(wrong),m['version'])
        with self.assertRaisesRegex(ValueError,'descriptor version'):c.descriptor_version(image,'0.13.95-r10-mosaico-ota')
        with self.assertRaises(ValueError):c.endorse(m,image,ec.generate_private_key(ec.SECP256R1()),self.pub,audited=True)
        with self.assertRaises(ValueError):c.pack([(m,cap),(m,cap)],self.pub)
        bad=copy.deepcopy(m);bad['sha256']='0'*64
        with self.assertRaises(InvalidSignature):c.verify_endorsement(bad,cap,self.pub)

    def test_explicit_rollover_pair_rejects_old_unsafe_images(self):
        self.assertEqual(c.VERSIONS,{'0.13.95-r9-mosaico-ota','0.13.95-r10-mosaico-ota'})
        for version in ('0.13.95-r1-mosaico-ota','0.13.95-r2-mosaico-ota','0.13.95-r3-mosaico-ota','0.13.95-r4-mosaico-ota','0.13.95-r5-mosaico-ota','0.13.95-r20-mosaico-ota','0.13.95-r10-mosaico-ota-extra'):
            with self.subTest(version=version),self.assertRaises(ValueError):self.pair(version)
        self.assertIn('main/apps/app_codex_micro/view/view_mosaico.cpp',c.AUDITED_INPUTS)

    def test_normal_manifest_signature_and_current_contract_binding(self):
        image,m,cap=self.pair('0.13.95-r9-mosaico-ota')
        tampered=copy.deepcopy(m);tampered['size']+=1
        with self.assertRaises(InvalidSignature):c.verify_image_manifest(tampered,self.pub)
        old=copy.deepcopy(m);old['version']='0.13.94-r10-mosaico-ota'
        with self.assertRaises(ValueError):c.canonical(old)
        self.assertEqual(c.DIGEST,'e13f6e771556ad0a507112532f3a2e63139ff6c7433a2ebc1cea022cf8df30fa')
        runtime=(Path(__file__).resolve().parents[1]/'main/hal/mosaico/mosaico_factory_scale_runtime.inc').read_text()
        self.assertIn(c.CONTRACT,runtime);self.assertIn(c.DIGEST,runtime)
        self.assertEqual(c.canonical(m).splitlines(),[b'MOSAICO-FACTORY-SCALE-CAP-v1',c.DIGEST.encode(),m['version'].encode(),str(len(image)).encode(),m['sha256'].encode()])


if __name__=='__main__':unittest.main()
