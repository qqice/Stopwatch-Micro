#!/usr/bin/env python3
"""Freeze, back up exact erase sectors, and migrate this S31 unit to dual OTA.
No full dump, full-slot erase, NVS writes, NAND operations or eFuse changes.
--execute is deliberately required; without it only frozen files/plan are created.
"""
import argparse, binascii, hashlib, json, os, struct, sys
from pathlib import Path
import esptool
from esptool.bin_image import LoadFirmwareImage
from mosaico_ota_release import validate_image
from mosaico_backup_regions import EXPECTED_MAC, PRIVATE, NOR_SIZE

ROOT=Path(__file__).resolve().parents[1]
PREFIX_SHA='cf58a33f53a27e66fce9456a6f28f964524247571399d5335b379738e4cd739a'
def sha(data): return hashlib.sha256(data).hexdigest()
def md5(data): return hashlib.md5(data).hexdigest()
def sector_size(n): return (n+4095)&~4095

def otadata_image():
    blob=bytearray(b'\xff'*8192)
    # seq1->ota0 VALID baseline; seq2->ota1 NEW candidate. The loader, NOT
    # migration, turns NEW into PENDING_VERIFY on its first boot.
    for off,seq,state in [(0,1,2),(4096,2,0)]:
        entry=struct.pack('<I20sII',seq,b'\xff'*20,state,binascii.crc32(struct.pack('<I',seq),0xffffffff)&0xffffffff)
        blob[off:off+32]=entry
    return bytes(blob)

def validate_bootloader(blob):
    im=LoadFirmwareImage('esp32s31',blob)
    if im.chip_id!=32 or im.checksum!=im.calculate_checksum() or not im.append_digest or im.stored_digest!=im.calc_digest or im.data_length+32!=len(blob):
        raise ValueError('invalid S31 bootloader')
    if not blob or 0x2000+sector_size(len(blob))>0x9000: raise ValueError('loader exceeds protected boundary')

def partitions(raw):
    sys.path.insert(0,'C:/esp/v6.1/esp-idf/components/partition_table')
    from gen_esp32part import PartitionTable
    t=PartitionTable.from_binary(raw);t.verify()
    return {p.name:(p.type,p.subtype,p.offset,p.size,(bool(p.encrypted),bool(p.readonly))) for p in t}

def freeze(build, source, baseline, bundle):
    if not bundle.resolve().is_relative_to(PRIVATE.resolve()) or bundle.exists(): raise ValueError('bundle must be new private directory')
    cfg=(build/'config/sdkconfig.h').read_text()
    for flag in ['CONFIG_IDF_TARGET_ESP32S31','CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE','CONFIG_SPIRAM_XIP_FROM_PSRAM']:
        if f'#define {flag} 1' not in cfg: raise ValueError('missing required config: '+flag)
    if '#define CONFIG_BOOTLOADER_APP_ANTI_ROLLBACK 1' in cfg or '#define CONFIG_SECURE_BOOT 1' in cfg or '#define CONFIG_SECURE_FLASH_ENC_ENABLED 1' in cfg:
        raise ValueError('unexpected immutable-security changes')
    snapshot=source.read_bytes();old_app=baseline.read_bytes();validate_image(old_app)
    if len(snapshot)<0x7f0000 or sha(snapshot[:0x9000])!=PREFIX_SHA or snapshot[0x20000:0x20000+len(old_app)]!=old_app:
        raise ValueError('unvalidated original loader/app snapshot')
    app=(build/'Stopwatch-Mosaico.bin').read_bytes();validate_image(app)
    boot=(build/'bootloader/bootloader.bin').read_bytes();validate_bootloader(boot)
    table=(build/'partition_table/partition-table.bin').read_bytes()
    old=partitions(snapshot[0x9000:0x9c00]);new=partitions(table)
    for n in ['nvs','otadata','phy_init','ui_apps','system']:
        if old[n]!=new[n]: raise ValueError('protected layout changes: '+n)
    if set(new)!=set(['nvs','otadata','phy_init','ota_0','ota_1','ui_apps','system']): raise ValueError('unexpected layout')
    if new['ota_0'][:4]!=(0,16,0x20000,0x3e0000) or new['ota_1'][:4]!=(0,17,0x400000,0x3f0000): raise ValueError('wrong OTA slots')
    if len(table)>4096: raise ValueError('table overflow')
    artifacts=[('candidate.bin',0x400000,app),('bootloader.bin',0x2000,boot),('partition-table.bin',0x9000,table),('otadata.bin',0x10000,otadata_image())]
    bundle.mkdir()
    writes=[]
    for name,offset,data in artifacts:
        with (bundle/name).open('xb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        writes.append({'file':name,'offset':offset,'bytes':len(data),'erase_length':sector_size(len(data)),'sha256':sha(data)})
    plan={'schema':1,'mac':EXPECTED_MAC,'source_sha256':sha(snapshot),'baseline_bytes':len(old_app),'baseline_sha256':sha(old_app),'writes':writes,'flash_bytes':NOR_SIZE,'preserve_baseline':True,'sector_alignment':4096,'ota_acceptance':{'offset':0x20000,'bytes':len(app),'erase_length':sector_size(len(app)),'sha256':sha(app)}}
    with (bundle/'migration-plan.json').open('x') as f:json.dump(plan,f,indent=2)
    print('FROZEN_MIGRATION_PLAN '+json.dumps(writes),flush=True)
    return plan

def complement(writes):
    intervals=sorted((p['offset'],p['offset']+p['erase_length']) for p in writes)
    end=0;out=[]
    for start,stop in intervals:
        if start<end: raise ValueError('overlap')
        if start>end:out.append((end,start-end))
        end=stop
    if end<NOR_SIZE:out.append((end,NOR_SIZE-end))
    return out

def execute(bundle, source, port):
    plan=json.loads((bundle/'migration-plan.json').read_text());snapshot=source.read_bytes()
    if plan['mac']!=EXPECTED_MAC or sha(snapshot)!=plan['source_sha256']: raise ValueError('source/plan changed')
    artifacts=[]
    allow={'candidate.bin':0x400000,'bootloader.bin':0x2000,'partition-table.bin':0x9000,'otadata.bin':0x10000}
    if len(plan['writes'])!=4 or {p['file'] for p in plan['writes']}!=set(allow): raise ValueError('unexpected writes')
    for p in plan['writes']:
        data=(bundle/p['file']).read_bytes()
        if p['offset']!=allow[p['file']] or len(data)!=p['bytes'] or sha(data)!=p['sha256'] or sector_size(len(data))!=p['erase_length']: raise ValueError('artifact changed')
        if p['offset']+p['erase_length']>len(snapshot): raise ValueError('source cannot cover writes')
        artifacts.append((p,data))
    if [p['file'] for p,d in artifacts]!=list(allow): raise ValueError('unsafe write ordering')
    validate_image(artifacts[0][1]);validate_bootloader(artifacts[1][1])
    if sha(snapshot[:0x9000])!=PREFIX_SHA:raise ValueError('source protected contract differs')
    if plan['baseline_bytes']<=0 or 0x20000+plan['baseline_bytes']>0x400000:raise ValueError('baseline overflow')
    baseline=snapshot[0x20000:0x20000+plan['baseline_bytes']]
    validate_image(baseline)
    if sha(baseline)!=plan['baseline_sha256']:raise ValueError('baseline plan mismatch')
    if len(artifacts[2][1])>4096 or len(artifacts[3][1])!=8192:raise ValueError('metadata overflow')
    old=partitions(snapshot[0x9000:0x9c00]);new=partitions(artifacts[2][1])
    if set(new)!=set(['nvs','otadata','phy_init','ota_0','ota_1','ui_apps','system']):raise ValueError('unexpected layout')
    for name in ['nvs','otadata','phy_init','ui_apps','system']:
        if old[name]!=new[name]:raise ValueError('protected layout changed')
    if new['ota_0'][:4]!=(0,16,0x20000,0x3e0000) or new['ota_1'][:4]!=(0,17,0x400000,0x3f0000):raise ValueError('wrong slots')
    if artifacts[3][1]!=otadata_image(): raise ValueError('wrong initial OTA selection')
    if plan.get('ota_acceptance')!={'offset':0x20000,'bytes':len(artifacts[0][1]),'erase_length':artifacts[0][0]['erase_length'],'sha256':sha(artifacts[0][1])}:raise ValueError('OTA acceptance backup not bound to candidate')
    # Exact backups occur before any flash_begin/write call and remain private.
    backup=bundle/'rollback';backup.mkdir()
    esp=esptool.detect_chip(port,connect_mode='no-reset',connect_attempts=3)
    try:
        if esp.IS_STUB or esp.IMAGE_CHIP_ID!=32 or esp.CHIP_NAME!='ESP32-S31' or ':'.join(f'{b:02x}' for b in esp.read_mac())!=EXPECTED_MAC: raise ValueError('wrong unit or not fresh ROM')
        sec=esp.get_security_info()
        if sec['flags']&1 or sec['flash_crypt_cnt'].bit_count()%2: raise ValueError('unexpected security state')
        esp.WRITE_FLASH_ATTEMPTS=1 # Fail closed; prohibit esptool default-reset retries mid-migration.
        esptool.attach_flash(esp,flash_type='nor')
        if (esp.flash_id()>>16)&255 not in (0x18,0x38):raise ValueError('wrong NOR size')
        esp.flash_set_parameters(NOR_SIZE)
        if esp.flash_md5sum(0,0x9000)!=md5(snapshot[:0x9000]) or esp.flash_md5sum(0x9000,0x1000)!=md5(snapshot[0x9000:0xa000]): raise ValueError('loader/table changed since snapshot')
        if esp.flash_md5sum(0x20000,plan['baseline_bytes'])!=md5(snapshot[0x20000:0x20000+plan['baseline_bytes']]):raise ValueError('baseline changed')
        backups=[]
        # The acceptance test will next OTA-write app0; back up exactly that
        # candidate-sized erase span now, without writing app0 during migration.
        future_app0={'offset':0x20000,'erase_length':artifacts[0][0]['erase_length']}
        for p,data in artifacts+[(future_app0,b'')]:
            offset,length=p['offset'],p['erase_length'];old=snapshot[offset:offset+length]
            if esp.flash_md5sum(offset,length)!=md5(old):raise ValueError('backup/device mismatch')
            name=f'offset-{offset:08x}.bin'
            with (backup/name).open('xb') as f:f.write(old);f.flush();os.fsync(f.fileno())
            if (backup/name).stat().st_size!=length or sha((backup/name).read_bytes())!=sha(old):raise ValueError('backup write integrity')
            backups.append({'file':name,'offset':offset,'length':length,'sha256':sha(old),'md5':md5(old)})
        untouched=[{'offset':o,'length':n,'md5':esp.flash_md5sum(o,n)} for o,n in complement(plan['writes'])]
        with (backup/'backup-regions.json').open('x') as f:json.dump({'complete':True,'mac':EXPECTED_MAC,'regions':backups,'untouched':untouched},f,indent=2);f.flush();os.fsync(f.fileno())
        with (backup/'RESTORE.txt').open('x') as f:
            f.write('Requires explicit recovery authorization and verified same physical unit. Use no-reset/no-reset; do NOT erase-all. Restore candidate area, loader, table, otadata at exact offsets below. Bootloader/table restoration is not power-cut atomic.\n')
            for p in backups:f.write(f"0x{p['offset']:x} {p['file']} length=0x{p['length']:x} sha256={p['sha256']}\n")
        print('EXACT_SECTOR_BACKUPS_VERIFIED migration_ranges=4 planned_OTA_acceptance_range=1',flush=True)
        for p,data in artifacts:
            print('WRITE_BEGIN '+p['file']+' offset='+hex(p['offset'])+' erase_length='+hex(p['erase_length']),flush=True)
            esptool.write_flash(esp,[(p['offset'],data)],flash_freq='keep',flash_mode='keep',flash_size='keep',no_compress=True,no_progress=True,erase_all=False,force=False)
            if esp.flash_md5sum(p['offset'],len(data))!=md5(data):raise ValueError('written artifact MD5 mismatch; STOP and retain ROM recovery')
            print('WRITE_VERIFIED '+p['file'],flush=True)
        for p in untouched:
            if esp.flash_md5sum(p['offset'],p['length'])!=p['md5']:raise ValueError('untouched-region mismatch; STOP and retain ROM recovery')
        with (bundle/'write-result.json').open('x') as f:json.dump({'written_verified':True,'untouched_verified':True,'app0_untouched':True,'runtime_accepted':False},f,indent=2)
        print('MIGRATION_WRITES_VERIFIED app0_and_all_complement_regions_unchanged=1',flush=True)
        esp.hard_reset()
    finally:esp._port.close()

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--build-dir',type=Path);a.add_argument('--source',type=Path,required=True);a.add_argument('--baseline',type=Path)
    a.add_argument('--bundle',type=Path,required=True);a.add_argument('--port');a.add_argument('--execute',action='store_true')
    p=a.parse_args()
    if p.execute:
        if not p.port or not p.bundle.resolve().is_relative_to(PRIVATE.resolve()):raise ValueError('execute requires private bundle and explicit port')
        execute(p.bundle,p.source,p.port)
    else:
        if not p.build_dir or not p.baseline:raise ValueError('freeze requires build and baseline')
        freeze(p.build_dir,p.source,p.baseline,p.bundle)
if __name__=='__main__':main()
