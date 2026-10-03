"""Offline tests for the application-only Mosaico write boundary."""
import os, struct, sys, unittest
from pathlib import Path
from validate_mosaico_image import validate
IDF = Path(os.environ.get("IDF_PATH", "C:/esp/v6.1/esp-idf"))
@unittest.skipUnless((IDF/"components/partition_table/gen_esp32part.py").exists(), "local IDF partition parser required")
class MosaicoGuardTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0,str(IDF/"components/partition_table"))
        from gen_esp32part import PartitionTable
        self.table = PartitionTable.from_csv("nvs,data,nvs,0xa000,0x6000,\nfactory,app,factory,0x20000,0x7d0000,\n").to_binary()
        self.prefix = bytearray(b"\xff"*0xa000)
        self.prefix[0x9000:0x9000+len(self.table)] = self.table
        self.image = bytearray(288)
        self.image[0]=0xe9;self.image[1]=1
        struct.pack_into("<H",self.image,12,32)
        struct.pack_into("<II",self.image,24,0x50000020,256)
        self.image[80:96]=b"Stopwatch-Mosaico"
    def test_app_only_verified_layout_and_xip(self):
        self.assertEqual(validate(bytes(self.prefix),bytes(self.image),self.table,IDF),(0x20000,0x7d0000))
    def test_bootloader_binding_rejects_other_unit(self):
        import hashlib
        expected=hashlib.sha256(self.prefix[:0x9000]).hexdigest()
        validate(bytes(self.prefix),bytes(self.image),self.table,IDF,expected)
        self.prefix[100] ^= 1
        with self.assertRaises(ValueError):validate(bytes(self.prefix),bytes(self.image),self.table,IDF,expected)
    def test_wrong_chip_or_flash_mapping_is_rejected(self):
        struct.pack_into("<H",self.image,12,9)
        with self.assertRaises(ValueError):validate(bytes(self.prefix),bytes(self.image),self.table,IDF)
        struct.pack_into("<H",self.image,12,32);struct.pack_into("<I",self.image,24,0x40000020)
        with self.assertRaises(ValueError):validate(bytes(self.prefix),bytes(self.image),self.table,IDF)
    def test_truncated_and_wrong_project_are_rejected(self):
        with self.assertRaises(ValueError):validate(bytes(self.prefix[:0x9000]),bytes(self.image),self.table,IDF)
        self.image[80]=ord('X')
        with self.assertRaises(ValueError):validate(bytes(self.prefix),bytes(self.image),self.table,IDF)
    def test_changed_factory_layout_is_rejected(self):
        from gen_esp32part import PartitionTable
        other=PartitionTable.from_csv("nvs,data,nvs,0xa000,0x6000,\nfactory,app,factory,0x20000,0x680000,\n").to_binary()
        with self.assertRaises(ValueError):validate(bytes(self.prefix),bytes(self.image),other,IDF)
if __name__=='__main__':unittest.main()
