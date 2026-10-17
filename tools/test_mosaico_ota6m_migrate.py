"""HOST MOCK ONLY: exact-unit 6MiB migration safety, no device or firmware build."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import mosaico_ota6m_migrate as migration


def table_blob(parts):
    raw=b''.join(struct.pack('<2sBBII16sI',b'\xaa\x50',*spec,name.encode().ljust(16,b'\0'),0)
                 for name,spec in parts.items())
    raw+=b'\xeb\xeb'+b'\xff'*14+bytes.fromhex(migration.md5(raw))
    return raw.ljust(0xc00,b'\xff')


class MockROM:
    IS_STUB=False; IMAGE_CHIP_ID=32; CHIP_NAME='ESP32-S31'
    WRITE_FLASH_ATTEMPTS=1
    def __init__(self):
        self.flash=bytearray(b'\xff'*migration.NOR_SIZE)
        self.flash[0x9000:0x9c00]=table_blob(migration.OLD)
        self.flash[0x9c00:0xa000]=b'T'*1024
        self.flash[0x10000:0x10020]=migration.entry(87,2)
        self.flash[0x11000:0x11020]=migration.entry(86,2)
        self.flash[0x20000:0x20400]=b'A'*1024
        self.flash[0x400000:0x400400]=b'B'*1024
        self.flash[0x800000:0x801000]=b'F'*4096
        self.reads=[]; self.closed=False; self._port=self; self.writes=[]
        self.fail_write=None; self.corrupt_write=None; self.bad_read=False
    def read_mac(self): return bytes.fromhex(migration.EXPECTED_MAC.replace(':',''))
    def get_security_info(self): return {'flags':0,'flash_crypt_cnt':0}
    def flash_id(self): return 0x180000
    def flash_set_parameters(self,size): assert size==migration.NOR_SIZE
    def flash_md5sum(self,offset,length): return migration.md5(self.flash[offset:offset+length])
    def read_flash(self,offset,length):
        self.reads.append((offset,length))
        data=bytes(self.flash[offset:offset+length])
        return bytes([data[0]^1])+data[1:] if self.bad_read else data
    def close(self): self.closed=True
    def write(self,esp,ranges,**kw):
        assert esp is self and len(ranges)==1 and not kw['erase_all'] and not kw['force'] and kw['no_compress']
        assert self.WRITE_FLASH_ATTEMPTS==1
        offset,data=ranges[0]
        assert offset in (0x800000,0x9000,0x10000,0x11000)
        self.writes.append(offset)
        if self.fail_write==len(self.writes):
            self.flash[offset:offset+len(data)//2]=data[:len(data)//2]
            raise RuntimeError('mock interrupted partial write')
        self.flash[offset:offset+len(data)]=data
        if self.corrupt_write==len(self.writes): self.flash[offset]^=1


class SixMiBMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.bundle=self.root/'bundle'; self.bundle.mkdir()
        self.rom=MockROM(); self.initial=bytes(self.rom.flash)
        self.blobs={'candidate.bin':b'C'*5000,'current-ota0.bin':b'A'*1024,
                    'old-ota1.bin':b'B'*1024,'new-table.bin':table_blob(migration.NEW)}
        self.state={'complete':False}
        self.writepatch=patch.object(migration.esptool,'write_flash',side_effect=self.rom.write)
        self.writepatch.start(); self.addCleanup(self.writepatch.stop)
        self.attach=patch.object(migration.esptool,'attach_flash').start(); self.addCleanup(patch.stopall)

    def perform(self): migration.perform(self.rom,self.bundle,self.blobs,self.state)

    def test_exact_old_new_and_mixed_rejected(self):
        migration.exact_table(table_blob(migration.OLD),migration.OLD)
        migration.exact_table(table_blob(migration.NEW),migration.NEW)
        with self.assertRaises(ValueError): migration.exact_table(table_blob(migration.OLD),migration.NEW)
        mixed=dict(migration.NEW); mixed['ota_1']=migration.OLD['ota_1']
        with self.assertRaises(ValueError): migration.exact_table(table_blob(mixed),migration.NEW)
        raw=bytearray(table_blob(migration.NEW)); raw[240]^=1
        with self.assertRaises(ValueError): migration.exact_table(bytes(raw),migration.NEW)
        raw=bytearray(table_blob(migration.NEW)); raw[224:256]=b'\xff'*32
        with self.assertRaises(ValueError): migration.exact_table(bytes(raw),migration.NEW)

    def test_selection_preserves_highest_valid0_crc_parity(self):
        for index in (0,1):
            raw=bytearray(b'\xff'*8192)
            raw[index*4096:index*4096+32]=migration.entry(87,2)
            raw[(1-index)*4096:(1-index)*4096+32]=migration.entry(86,2)
            selected=migration.selection(raw)
            self.assertEqual(selected['preserve_index'],index)
            self.assertEqual(selected['new_seq'],88)
            self.assertEqual(selected['offset'],0x10000+(1-index)*4096)
            self.assertEqual((88-1)%2,1)
            self.assertEqual(selected['data'][:32],migration.entry(88,0))
            raw[index*4096+28]^=1
            with self.assertRaises(ValueError): migration.selection(raw)
        for seq,state in ((88,2),(87,0),(87,1),(0xfffffffd,2)):
            raw=migration.entry(seq,state).ljust(8192,b'\xff')
            if seq==0xfffffffd:
                # 0xfffffffe is still usable, next attempt would overflow.
                self.assertEqual(migration.selection(raw)['new_seq'],0xfffffffe)
            else:
                with self.assertRaises(ValueError): migration.selection(raw)
        raw=migration.entry(87,2).ljust(4096,b'\xff')+migration.entry(0xfffffffe,4).ljust(4096,b'\xff')
        with self.assertRaises(ValueError): migration.selection(raw)

    def test_wrong_mac_security_stub_size_rejected(self):
        with patch.object(self.rom,'read_mac',return_value=b'\0'*6):
            with self.assertRaises(ValueError): migration.preflight_rom(self.rom)
        with patch.object(self.rom,'get_security_info',return_value={'flags':1,'flash_crypt_cnt':0}):
            with self.assertRaises(ValueError): migration.preflight_rom(self.rom)
        with patch.object(self.rom,'flash_id',return_value=0x190000):
            with self.assertRaises(ValueError): migration.preflight_rom(self.rom)
        with patch.object(self.rom,'IS_STUB',True):
            with self.assertRaises(ValueError): migration.preflight_rom(self.rom)
        self.assertFalse(self.rom.writes)

    def test_range_plan_full_complement_and_caps(self):
        ranges=migration.range_plan(5000,0x11000)
        self.assertEqual(ranges,[(0x800000,8192),(0x9000,4096),(0x11000,4096)])
        cursor=0
        for offset,length in sorted(ranges+migration.complement(ranges)):
            self.assertEqual(offset,cursor); cursor+=length
        self.assertEqual(cursor,migration.NOR_SIZE)
        for length in (0,287,migration.CAP+1):
            with self.assertRaises(ValueError): migration.range_plan(length,0x11000)
        with self.assertRaises(ValueError): migration.range_plan(5000,0x12000)

    def test_success_backup_before_write_and_complements(self):
        original_write=self.rom.write
        def write(esp,ranges,**kw):
            manifest=json.loads((self.bundle/'rollback/backup.json').read_text())
            self.assertTrue(manifest['complete']); self.assertEqual(len(manifest['regions']),3)
            original_write(esp,ranges,**kw)
        with patch.object(migration.esptool,'write_flash',side_effect=write): self.perform()
        self.assertEqual(self.rom.writes,[0x800000,0x9000,0x11000])
        self.assertEqual(self.rom.flash[0x10000:0x11000],self.initial[0x10000:0x11000])
        self.assertEqual(self.rom.flash[0x20000:0x7f0000],self.initial[0x20000:0x7f0000])
        self.assertEqual(self.rom.flash[0x9c00:0xa000],b'T'*1024)
        rollback=self.bundle/'rollback'
        for offset,length in ((0x800000,8192),(0x9000,4096),(0x10000,8192)):
            self.assertEqual((rollback/f'{offset:08x}.bin').read_bytes(),self.initial[offset:offset+length])
        manifest=json.loads((rollback/'backup.json').read_text())
        restore=manifest['restore_order']
        self.assertEqual([v['offset'] for v in restore],[0x11000,0x9000,0x800000])
        self.assertEqual(restore[0]['length'],4096)
        self.assertEqual(restore[0]['file'],'rollback-other-selector.bin')
        original=self.initial[0x11000:0x12000]
        self.assertEqual((rollback/restore[0]['file']).read_bytes(),original)
        self.assertEqual(restore[0]['sha256'],migration.sha(original))
        self.assertEqual(restore[0]['md5'],migration.md5(original))
        instructions=(rollback/'RESTORE.txt').read_text()
        self.assertIn('other-selector -> old table -> factory-data span',instructions)
        self.assertIn('No automatic restore or reset',instructions)
        self.assertIn('MD5 immediately after EACH write',instructions)
        self.assertEqual(self.state['stage'],'writes-verified')
        self.assertTrue(self.state['complements_verified']); self.assertFalse(self.state['runtime_accepted'])

    def test_app_changed_or_read_or_backup_failure_no_write(self):
        self.rom.flash[0x400001]^=1
        with self.assertRaises(ValueError): self.perform()
        self.assertFalse(self.rom.writes)
        self.rom.flash[0x400001]^=1
        with patch.object(migration,'backup_region',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.perform()
        self.assertFalse(self.rom.writes)

    def test_candidate_verify_failure_never_writes_table(self):
        self.rom.corrupt_write=1
        with self.assertRaises(ValueError): self.perform()
        self.assertEqual(self.rom.writes,[0x800000])
        self.assertEqual(self.rom.flash[0x9000:0xa000],self.initial[0x9000:0xa000])

    def test_every_interruption_prefix_protects_apps_and_preserved_selector(self):
        for fail in (1,2,3):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                self.rom=MockROM(); self.rom.fail_write=fail
                with patch.object(migration.esptool,'write_flash',side_effect=self.rom.write):
                    with self.assertRaises(RuntimeError): migration.perform(self.rom,Path(directory),self.blobs,{})
                self.assertEqual(self.rom.writes,[0x800000,0x9000,0x11000][:fail])
                self.assertEqual(self.rom.flash[0x20000:0x7f0000],self.initial[0x20000:0x7f0000])
                self.assertEqual(self.rom.flash[0x10000:0x11000],self.initial[0x10000:0x11000])

    def test_ff_backup_only_md5_proven_and_false_ff_rejected(self):
        self.rom.flash[0x800000:0x810000]=b'\xff'*0x10000
        item=migration.backup_region(self.rom,0x800000,0x10000,self.bundle/'ff.bin')
        self.assertEqual(item['md5'],migration.md5(b'\xff'*0x10000))
        self.assertFalse(self.rom.reads)
        self.rom.flash[0x800000]=1
        original=self.rom.flash_md5sum; calls=0
        def false_first(offset,length):
            nonlocal calls
            calls+=1
            return migration.md5(b'\xff'*length) if calls==1 else original(offset,length)
        with patch.object(self.rom,'flash_md5sum',side_effect=false_first):
            with self.assertRaises(ValueError): migration.backup_region(self.rom,0x800000,0x10000,self.bundle/'false-ff.bin')
        self.rom.bad_read=True
        with self.assertRaises(ValueError): migration.checked_read(self.rom,0x800000,4096)

    def test_default_freeze_no_detect_and_reexecution_refused(self):
        files={}
        for name,blob in self.blobs.items():
            path=self.root/name; path.write_bytes(blob); files[name]=path
        output=self.root/'drybundle'
        argv=['mosaico_ota6m_migrate.py','--image',str(files['candidate.bin']),'--sha256',migration.sha(self.blobs['candidate.bin']),
              '--table',str(files['new-table.bin']),'--current-image',str(files['current-ota0.bin']),
              '--current-sha256',migration.sha(self.blobs['current-ota0.bin']),'--old-image',str(files['old-ota1.bin']),
              '--old-sha256',migration.sha(self.blobs['old-ota1.bin']),'--bundle',str(output)]
        with patch.object(migration,'PRIVATE',self.root), patch.object(migration,'validate_image'), patch('sys.argv',argv), patch.object(migration.esptool,'detect_chip') as detect, contextlib.redirect_stdout(io.StringIO()):
            migration.main(); detect.assert_not_called()
            with self.assertRaises(ValueError): migration.main()  # exclusive bundle
        (output/'progress.json').write_text('{}')
        with patch.object(migration,'PRIVATE',self.root), patch.object(migration.esptool,'detect_chip') as detect:
            with self.assertRaises(ValueError): migration.execute(output,'MOCK')
            detect.assert_not_called()

    def test_invalid_frozen_image_rejected_before_connect(self):
        for name,blob in self.blobs.items(): (self.bundle/name).write_bytes(blob)
        plan={'schema':1,'mac':migration.EXPECTED_MAC,'nor_bytes':migration.NOR_SIZE,
              'inputs':{n:{'bytes':len(b),'sha256':migration.sha(b)} for n,b in self.blobs.items()}}
        (self.bundle/'plan.json').write_text(json.dumps(plan))
        with patch.object(migration,'PRIVATE',self.root), patch.object(migration,'validate_image',side_effect=ValueError('invalid image')), patch.object(migration.esptool,'detect_chip') as detect:
            with self.assertRaises(ValueError): migration.execute(self.bundle,'MOCK')
            detect.assert_not_called()
        self.assertEqual(json.loads((self.bundle/'progress.json').read_text())['failed_stage'],'validate-frozen-inputs')
        with self.assertRaises(ValueError): migration.checked_image(self.bundle/'candidate.bin','1234')

    def test_execute_frozen_cli_uses_pristine_bundle_without_freeze(self):
        for name,blob in self.blobs.items(): (self.bundle/name).write_bytes(blob)
        plan={'schema':1,'mac':migration.EXPECTED_MAC,'nor_bytes':migration.NOR_SIZE,
              'inputs':{n:{'bytes':len(b),'sha256':migration.sha(b)} for n,b in self.blobs.items()}}
        (self.bundle/'plan.json').write_text(json.dumps(plan))
        argv=['mosaico_ota6m_migrate.py','--execute-frozen','--bundle',str(self.bundle),'--port','MOCK']
        with patch.object(migration,'PRIVATE',self.root), patch.object(migration,'validate_image'), patch.object(migration,'freeze') as freeze, patch('sys.argv',argv), patch.object(migration.esptool,'detect_chip',return_value=self.rom) as detect:
            migration.main()
            freeze.assert_not_called()
            detect.assert_called_once()
            with self.assertRaises(ValueError): migration.main()
            self.assertEqual(detect.call_count,1)
        self.assertEqual(self.rom.writes,[0x800000,0x9000,0x11000])
        self.assertTrue((self.bundle/'.execution-started').exists())

    def test_execute_frozen_tampering_and_started_marker_rejected_no_device(self):
        for name,blob in self.blobs.items(): (self.bundle/name).write_bytes(blob)
        plan={'schema':1,'mac':migration.EXPECTED_MAC,'nor_bytes':migration.NOR_SIZE,
              'inputs':{n:{'bytes':len(b),'sha256':migration.sha(b)} for n,b in self.blobs.items()}}
        (self.bundle/'plan.json').write_text(json.dumps(plan))
        (self.bundle/'candidate.bin').write_bytes(b'X'*len(self.blobs['candidate.bin']))
        argv=['mosaico_ota6m_migrate.py','--execute-frozen','--bundle',str(self.bundle),'--port','MOCK']
        with patch.object(migration,'PRIVATE',self.root), patch.object(migration,'validate_image'), patch('sys.argv',argv), patch.object(migration.esptool,'detect_chip') as detect:
            with self.assertRaises(ValueError): migration.main()
            detect.assert_not_called()
            self.assertEqual(json.loads((self.bundle/'progress.json').read_text())['failed_stage'],'validate-frozen-inputs')
            # Even if progress.json was lost, the exclusive execution marker blocks retry.
            (self.bundle/'progress.json').unlink()
            with self.assertRaises(ValueError): migration.main()
            detect.assert_not_called()

    def test_frozen_mode_requires_explicit_mode_and_no_freeze_inputs(self):
        for argv in (["tool",'--bundle',str(self.bundle),'--port','MOCK'],
                     ["tool",'--execute-frozen','--bundle',str(self.bundle)],
                     ["tool",'--execute-frozen','--bundle',str(self.bundle),'--port','MOCK','--image','other.bin']):
            with self.subTest(argv=argv), patch('sys.argv',argv), patch.object(migration.esptool,'detect_chip') as detect, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): migration.main()
                detect.assert_not_called()

    def test_execute_failure_stage_and_no_reset_no_retry(self):
        for name,blob in self.blobs.items(): (self.bundle/name).write_bytes(blob)
        plan={'schema':1,'mac':migration.EXPECTED_MAC,'nor_bytes':migration.NOR_SIZE,
              'inputs':{n:{'bytes':len(b),'sha256':migration.sha(b)} for n,b in self.blobs.items()}}
        (self.bundle/'plan.json').write_text(json.dumps(plan))
        self.rom.corrupt_write=1
        with patch.object(migration,'PRIVATE',self.root), patch.object(migration,'validate_image'), patch.object(migration.esptool,'detect_chip',return_value=self.rom) as detect:
            with self.assertRaises(ValueError): migration.execute(self.bundle,'MOCK')
            detect.assert_called_once_with('MOCK',connect_mode='no-reset',connect_attempts=1)
        state=json.loads((self.bundle/'progress.json').read_text())
        self.assertEqual(state['stage'],'failed'); self.assertEqual(state['failed_stage'],'write-candidate')
        self.assertTrue(self.rom.closed); self.assertEqual(self.rom.writes,[0x800000])


if __name__=='__main__': unittest.main()
