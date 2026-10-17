"""Offline exact-layout bridge and signed-release contract tests; no firmware build/IO."""
import base64
import csv
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from ota_release import (DEFAULT_LAYOUT, MAX_IMAGE_SIZE, METADATA, ImmutableReleaseStore,
                         canonical_message, image_capacity, validate_manifest)
import mosaico_ota_release as signer

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')


def csv_cpp(name):
    subtypes = {'nvs': 2, 'ota': 0, 'phy': 1, 'ota_0': 16, 'ota_1': 17}
    result = []
    for row in csv.reader(line for line in (ROOT / 'boards/mosaico' / name).read_text().splitlines()
                          if line.strip() and not line.lstrip().startswith('#')):
        subtype = subtypes.get(row[2])
        if subtype is None: subtype = int(row[2], 0)
        result.append('{%d,%d,%s,%s}' % (0 if row[1] == 'app' else 1, subtype, row[3], row[4]))
    return ','.join(result)


class LayoutBridgeTests(unittest.TestCase):
    def test_actual_constexpr_geometry_and_fail_closed(self):
        code = r'''
#include "main/ota/mosaico_ota_layout.h"
using namespace MosaicoOtaLayout;
constexpr Partition oldTable[] = {OLD_TABLE};
constexpr Partition newTable[] = {NEW_TABLE};
static_assert(identify(oldTable,7)==Id::DualV1);
static_assert(identify(newTable,7)==Id::DualV2);
constexpr bool test() {
 for (unsigned mask=0;mask<128;++mask) {
  Partition mixed[7]{};
  for (unsigned i=0;i<7;++i) mixed[i]=(mask&(1U<<i))?oldTable[i]:newTable[i];
  bool old=matches(mixed,7,V1), newer=matches(mixed,7,V2);
  if (identify(mixed,7)!=(old?Id::DualV1:newer?Id::DualV2:Id::Unknown)) return false;
 }
 for (Id id : {Id::DualV1, Id::DualV2}) {
  const Partition* table=id==Id::DualV1?oldTable:newTable;
  Partition p[8]{}; for(unsigned i=0;i<7;++i)p[i]=table[i];
  if(identify(p,6)!=Id::Unknown||identify(p,8)!=Id::Unknown) return false;
  for(unsigned i=0;i<7;++i) {
   for(unsigned field=0;field<4;++field) {
    p[i]=table[i];
    if(field==0)++p[i].type; if(field==1)++p[i].subtype;
    if(field==2)++p[i].address; if(field==3)++p[i].size;
    if(identify(p,7)!=Id::Unknown) return false;
   } p[i]=table[i];
  }
  p[6]=p[5]; if(identify(p,7)!=Id::Unknown) return false;
  for(unsigned i=0;i<7;++i) {
   if(table[i].type!=0) continue;
   if(!imageFits(id,table[i],imageCap(id))||imageFits(id,table[i],imageCap(id)+1)||imageFits(id,table[i],0)) return false;
   if(imageFits(id==Id::DualV1?Id::DualV2:Id::DualV1,table[i],1)) return false;
  }
 }
 return imageCap(Id::Unknown)==0 && !exactSlot(Id::Unknown,oldTable[3]);
}
static_assert(test());
'''.replace('OLD_TABLE', csv_cpp('partitions.ota.csv')).replace('NEW_TABLE', csv_cpp('partitions.ota6m.proposal.csv'))
        code = '#include <initializer_list>\n' + code
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'layout.cpp'
            source.write_text(code)
            result = subprocess.run([str(COMPILER), '-std=c++17', '-fsyntax-only', '-I' + str(ROOT), str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runtime_integration(self):
        cpp = (ROOT / 'main/ota/mosaico_ota.cpp').read_text()
        self.assertIn('ESP_PARTITION_TYPE_ANY, ESP_PARTITION_SUBTYPE_ANY', cpp)
        self.assertIn('count == 7 || !p || p->encrypted', cpp)
        self.assertIn('MosaicoOtaLayout::identify(parts, count)', cpp)
        self.assertIn('textEquals(m, "layout", MosaicoOtaLayout::name(layout))', cpp)
        self.assertIn('size->valuedouble <= MosaicoOtaLayout::imageCap(layout)', cpp)
        self.assertIn('MosaicoOtaLayout::imageFits(currentLayout(), partitionGeometry(target), expectedSize.load())', cpp)
        self.assertNotIn('mosaico-dual-v1', cpp)  # No hard-coded signed layout escape.
        self.assertEqual(cpp.count('MosaicoOtaLayout::name(layout),'), 2)  # Both schema preimages.

    def test_defaults_and_manifest_size_limits(self):
        self.assertEqual(DEFAULT_LAYOUT, 'mosaico-dual-v1')
        self.assertEqual(MAX_IMAGE_SIZE, 0x3e0000)
        self.assertEqual(METADATA['layout'], DEFAULT_LAYOUT)
        sha = 'a' * 64
        self.assertEqual(canonical_message(1, sha),
            f'MOSAICO-OTA-v1\nStopwatch-Mosaico\nesp32s31\nmosaico-dual-v1\n1\n{sha}\n'.encode())
        self.assertEqual(canonical_message(1, sha, 'v'),
            f'MOSAICO-OTA-v2\nStopwatch-Mosaico\nesp32s31\nmosaico-dual-v1\nv\n1\n{sha}\n'.encode())
        for layout, cap in ((DEFAULT_LAYOUT, 0x3e0000), ('mosaico-dual-v2', 0x600000)):
            self.assertEqual(image_capacity(layout), cap)
            for schema in (1, 2):
                manifest = dict(METADATA, layout=layout, schema=schema, size=cap, sha256=sha,
                    signature=base64.b64encode(bytes.fromhex('3006020101020101')).decode())
                if schema == 2: manifest['version'] = 'v'
                validate_manifest(manifest)
                for size in (0, cap + 1, True):
                    with self.assertRaises(ValueError): validate_manifest(dict(manifest, size=size))
        for bad in ('', 'mosaico-dual-v3', None, [], True):
            with self.assertRaises(ValueError): image_capacity(bad)

    def test_signer_layout_binding_both_schemas_and_store(self):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keypath = root / 'ephemeral.pem'
            keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            image = bytearray(288)
            image[48:80] = b'bridge-test'.ljust(32, b'\0')
            path = root / 'image.bin'; path.write_bytes(image)
            for layout in (DEFAULT_LAYOUT, 'mosaico-dual-v2'):
                for schema1 in (False, True):
                    output = root / f'{layout}-{schema1}'
                    with patch.object(signer, 'validate_image') as validate:
                        manifest = signer.sign_release(path, keypath, output, schema1=schema1, layout=layout)
                        validate.assert_called_once_with(bytes(image), layout=layout)
                    self.assertEqual(manifest['layout'], layout)
                    self.assertEqual(manifest['schema'], 1 if schema1 else 2)
                    self.assertEqual(json.loads(ImmutableReleaseStore(output).response('/v1/ota/manifest')), manifest)
                    sig = base64.b64decode(manifest['signature'])
                    version = manifest.get('version')
                    key.public_key().verify(sig, canonical_message(len(image), manifest['sha256'], version, layout=layout), ec.ECDSA(hashes.SHA256()))
                    other = 'mosaico-dual-v2' if layout == DEFAULT_LAYOUT else DEFAULT_LAYOUT
                    with self.assertRaises(InvalidSignature):
                        key.public_key().verify(sig, canonical_message(len(image), manifest['sha256'], version, layout=other), ec.ECDSA(hashes.SHA256()))
            # Serving large new-layout images must not truncate at the legacy cap.
            image = b'\0' * (MAX_IMAGE_SIZE + 1)
            manifest = dict(METADATA, layout='mosaico-dual-v2', size=len(image), sha256=hashlib.sha256(image).hexdigest(),
                signature=base64.b64encode(bytes.fromhex('3006020101020101')).decode())
            (root / 'firmware.bin').write_bytes(image)
            (root / 'manifest.json').write_text(json.dumps(manifest))
            ImmutableReleaseStore(root)
            manifest['layout'] = DEFAULT_LAYOUT
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError): ImmutableReleaseStore(root)

    def test_cli_default_and_explicit_layout(self):
        import contextlib
        import io
        for extra, layout in (([], DEFAULT_LAYOUT), (['--layout', 'mosaico-dual-v2'], 'mosaico-dual-v2')):
            argv = ['mosaico_ota_release.py', '--image', 'image.bin', '--key', 'key.pem', '--output-dir', 'out'] + extra
            with patch('sys.argv', argv), patch.object(signer, 'sign_release', return_value={'size': 1, 'sha256': 'a'*64}) as sign, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(signer.main(), 0)
            self.assertEqual(sign.call_args.kwargs, {'schema1': False, 'layout': layout})

    def test_image_validator_uses_selected_cap(self):
        def load(_chip, image):
            return types.SimpleNamespace(chip_id=32, checksum=1, calculate_checksum=lambda: 1,
                append_digest=True, stored_digest=b'x', calc_digest=b'x', data_length=len(image)-32,
                segments=[types.SimpleNamespace(addr=0x50000000)])
        fake = types.ModuleType('esptool.bin_image'); fake.LoadFirmwareImage = load
        with patch.dict('sys.modules', {'esptool': types.ModuleType('esptool'), 'esptool.bin_image': fake}):
            for layout in (DEFAULT_LAYOUT, 'mosaico-dual-v2'):
                cap = image_capacity(layout)
                image = bytearray(cap)
                image[0] = 0xe9; struct.pack_into('<H', image, 12, 32)
                struct.pack_into('<I', image, 32, 0xabcd5432)
                image[80:112] = b'Stopwatch-Mosaico'.ljust(32, b'\0')
                signer.validate_image(bytes(image), layout=layout)
                with self.assertRaisesRegex(ValueError, 'capacity'): signer.validate_image(bytes(image)+b'x', layout=layout)
                if layout != DEFAULT_LAYOUT:
                    with self.assertRaisesRegex(ValueError, 'capacity'): signer.validate_image(bytes(image))


if __name__ == '__main__':
    unittest.main()
