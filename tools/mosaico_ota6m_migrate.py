#!/usr/bin/env python3
"""Exact-unit, ROM-only 6MiB table migration. Default freezes a DRY PLAN only.

Writes candidate@0x800000, table@0x9000, then one non-preserved selector sector.
Never writes loader/app0/old-app1/NVS/NAND, resets, retries or erases whole flash.
Old app0 is a recovery application, NOT a fully layout-aware OTA fallback.
"""
import argparse
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import struct

import esptool
from mosaico_ota_migrate import partitions, sector_size, sha, md5
from mosaico_ota_release import validate_image
from mosaico_backup_regions import EXPECTED_MAC, PRIVATE, NOR_SIZE

SECTOR = 4096
CAP = 0x3e0000  # Candidate must remain suitable for legacy-size fallback installation.
COMMON = {'nvs': (1,2,0xa000,0x6000), 'otadata': (1,0,0x10000,0x2000),
          'phy_init': (1,1,0x12000,0x1000)}
OLD = dict(COMMON, ota_0=(0,16,0x20000,0x3e0000), ota_1=(0,17,0x400000,0x3f0000),
           ui_apps=(1,0x41,0x7f0000,0x320000), system=(1,0x83,0xb10000,0x2ee000))
NEW = dict(COMMON, ota_0=(0,16,0x20000,0x600000), ota_1=(0,17,0x800000,0x600000),
           reserve_0=(1,0x40,0x620000,0x1e0000), reserve_1=(1,0x40,0xe00000,0x200000))


def exact_table(raw, expected):
    if len(raw) != 0xc00:
        raise ValueError('table must be exactly 3KiB')
    # Do not allow a parser dictionary to hide duplicate names, unknown flags,
    # omitted MD5 entries or trailing non-table data.
    if any(raw[i*32:i*32+2] != b'\xaa\x50' or struct.unpack_from('<I', raw, i*32+28)[0] for i in range(7)):
        raise ValueError('table entries or flags differ')
    if raw[224:240] != b'\xeb\xeb' + b'\xff'*14 or raw[240:256] != hashlib.md5(raw[:224]).digest() or raw[256:] != b'\xff'*(0xc00-256):
        raise ValueError('table MD5 or terminator differs')
    try:
        parsed = partitions(raw)
    except Exception as exc:
        raise ValueError("invalid partition-table encoding") from exc
    if set(parsed) != set(expected) or any(parsed[n] != (*v, (False, False)) for n,v in expected.items()):
        raise ValueError('table is not the exact allowed layout')
    return parsed


def entry(seq, state):
    return struct.pack('<I20sII', seq, b'\xff'*20, state,
                       binascii.crc32(struct.pack('<I',seq),0xffffffff)&0xffffffff)


def selection(raw):
    """Preserve the highest valid ota0 entry; write NEW ota1 into the other sector."""
    if len(raw) != 8192: raise ValueError('otadata length')
    decoded = []
    for index in range(2):
        seq, _, state, crc = struct.unpack_from('<I20sII', raw, index*SECTOR)
        if seq == 0xffffffff and state == 0xffffffff and crc == 0xffffffff:
            continue
        if seq in (0, 0xffffffff) or crc != (binascii.crc32(struct.pack('<I',seq),0xffffffff)&0xffffffff):
            raise ValueError('nonempty selector has bad sequence/CRC')
        if state not in (0,1,2,3,4): raise ValueError('unknown selector state')
        # Aborted/invalid entries cannot win boot selection, but still bound new seq.
        decoded.append((seq,state,index))
    eligible = [v for v in decoded if v[1] not in (3,4)]
    if not eligible: raise ValueError('no eligible selector')
    selected = max(eligible)
    if selected[1] != 2 or (selected[0]-1)%2 != 0 or sum(v[0]==selected[0] for v in eligible)!=1:
        raise ValueError('highest selector is not unique VALID ota0')
    seq = max(v[0] for v in decoded) + 1
    if seq%2: seq += 1
    if seq >= 0xffffffff: raise ValueError('selector sequence exhausted')
    other = 1-selected[2]
    return {'preserve_index': selected[2], 'preserve_seq': selected[0], 'new_seq': seq,
            'offset': 0x10000+other*SECTOR, 'data': entry(seq,0)+b'\xff'*(SECTOR-32)}


def range_plan(image_length, selector_offset):
    if not 288 <= image_length <= CAP or selector_offset not in (0x10000,0x11000):
        raise ValueError('unsafe write bounds')
    return [(0x800000,sector_size(image_length)), (0x9000,SECTOR), (selector_offset,SECTOR)]


def complement(ranges):
    cursor = 0; out = []
    for start, length in sorted(ranges):
        if start < cursor or length <= 0 or start+length > NOR_SIZE: raise ValueError('bad intervals')
        if start > cursor: out.append((cursor,start-cursor))
        cursor = start+length
    if cursor < NOR_SIZE: out.append((cursor,NOR_SIZE-cursor))
    return out


def exclusive(path, data):
    with path.open('xb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    if path.read_bytes() != data: raise ValueError('host persistence mismatch')


def progress(bundle, state, stage, **values):
    state.update(stage=stage, **values)
    if stage.startswith("verified-") or stage in ("backups-verified", "writes-verified"):
        state["last_success"] = stage
    temporary = bundle/'progress.tmp'
    with temporary.open('w', encoding='utf-8') as f:
        json.dump(state,f,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(temporary,bundle/'progress.json')


def bounded_file(path, capacity):
    with Path(path).open("rb") as handle:
        data=handle.read(capacity+1)
    if len(data)>capacity: raise ValueError("input exceeds capacity")
    return data


def checked_image(path, digest):
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('full lowercase image SHA256 required')
    blob=bounded_file(path,CAP)
    if not 288 <= len(blob) <= CAP or sha(blob) != digest: raise ValueError('image SHA/cap mismatch')
    validate_image(blob)  # Legacy cap + unchanged S31/PSRAM-XIP/checksum validation.
    return blob


def freeze(image, image_sha, table, current, current_sha, old, old_sha, bundle, *, discard_factory_contents=False):
    bundle = Path(bundle).resolve()
    if bundle == PRIVATE.resolve() or not bundle.is_relative_to(PRIVATE.resolve()) or bundle.exists():
        raise ValueError('bundle must be new private subdirectory')
    blobs = {'candidate.bin':checked_image(image,image_sha), 'current-ota0.bin':checked_image(current,current_sha),
             'old-ota1.bin':checked_image(old,old_sha), 'new-table.bin':bounded_file(table,0xc00)}
    exact_table(blobs['new-table.bin'],NEW)
    bundle.mkdir(parents=True)
    for name,data in blobs.items(): exclusive(bundle/name,data)
    plan = {'schema':1,'mac':EXPECTED_MAC,'nor_bytes':NOR_SIZE,'dry_plan':True,
            'discard_factory_contents':discard_factory_contents,'user_authorized_factory_loss':discard_factory_contents,
            'inputs':{n:{'bytes':len(b),'sha256':sha(b)} for n,b in blobs.items()},
            'write_order':['candidate@0x800000','table@0x9000','nonpreserved selector sector'],
            'candidate_erase_length':sector_size(len(blobs['candidate.bin'])),
            'runtime_accepted':False,'fallback':'old ota0 recovery application; OTA disabled under new table'}
    exclusive(bundle/'plan.json',json.dumps(plan,indent=2).encode())
    return bundle


def load_inputs(bundle):
    plan=json.loads((bundle/'plan.json').read_text())
    if plan.get('mac') != EXPECTED_MAC or plan.get('nor_bytes') != NOR_SIZE or plan.get('schema') != 1:
        raise ValueError('plan identity mismatch')
    names=('candidate.bin','current-ota0.bin','old-ota1.bin','new-table.bin')
    if set(plan['inputs']) != set(names): raise ValueError('plan inputs mismatch')
    blobs={}
    for name in names:
        spec=plan['inputs'][name]
        blob=bounded_file(bundle/name,0xc00 if name=='new-table.bin' else CAP)
        if spec != {'bytes':len(blob),'sha256':sha(blob)}: raise ValueError('frozen input changed')
        if name.endswith('.bin') and name != 'new-table.bin':
            if not 288 <= len(blob) <= CAP: raise ValueError('frozen image capacity')
            validate_image(blob)
        blobs[name]=blob
    exact_table(blobs['new-table.bin'],NEW)
    return blobs


def checked_read(esp, offset, length):
    data=esp.read_flash(offset,length)
    if len(data)!=length or md5(data)!=esp.flash_md5sum(offset,length).lower():
        raise ValueError('ROM read/MD5 mismatch')
    return data


def backup_region(esp, offset, length, path, on_block=None):
    """Sparse logical reconstruction: FF64KiB only after MD5; otherwise verified 4KiB reads."""
    if offset%SECTOR or length%SECTOR or length<=0: raise ValueError('unaligned backup')
    digest=hashlib.sha256(); legacy=hashlib.md5()
    with path.open('xb') as f:
        done=0
        while done<length:
            count=min(0x10000,length-done)
            if count==0x10000 and esp.flash_md5sum(offset+done,count).lower()==md5(b'\xff'*count):
                data=b'\xff'*count
            else:
                data=b''.join(checked_read(esp,offset+done+i,SECTOR) for i in range(0,count,SECTOR))
            f.write(data); digest.update(data); legacy.update(data); done+=count
            if on_block: on_block(done,length)
        f.flush(); os.fsync(f.fileno())
    if path.stat().st_size!=length or sha(path.read_bytes())!=digest.hexdigest() or esp.flash_md5sum(offset,length).lower()!=legacy.hexdigest():
        raise ValueError('complete backup host/ROM mismatch')
    return {'file':path.name,'offset':offset,'length':length,'sha256':digest.hexdigest(),'md5':legacy.hexdigest()}


def preflight_rom(esp):
    if esp.IS_STUB or esp.IMAGE_CHIP_ID!=32 or esp.CHIP_NAME!='ESP32-S31' or ':'.join(f'{b:02x}' for b in esp.read_mac())!=EXPECTED_MAC:
        raise ValueError('wrong unit or not fresh S31 ROM')
    security=esp.get_security_info()
    if security['flags']!=0 or security['flash_crypt_cnt']!=0: raise ValueError('unexpected security state')
    esp.WRITE_FLASH_ATTEMPTS=1
    esptool.attach_flash(esp,flash_type='nor')
    if ((esp.flash_id()>>16)&255) not in (0x18,0x38): raise ValueError('NOR must be 16MiB')
    esp.flash_set_parameters(NOR_SIZE)


def perform(esp, bundle, blobs, state, *, discard_factory_contents=False):
    """Injected ROM boundary for host mocks; caller already identity/security checked."""
    progress(bundle,state,'read-old-table')
    table=checked_read(esp,0x9000,SECTOR)
    exact_table(table[:0xc00],OLD)
    selectors=checked_read(esp,0x10000,8192)
    select=selection(selectors)
    progress(bundle,state,'verify-old-apps',preserved_selector=select['preserve_index'],preserved_seq=select['preserve_seq'],new_seq=select['new_seq'])
    for name,offset in (('current-ota0.bin',0x20000),('old-ota1.bin',0x400000)):
        if esp.flash_md5sum(offset,len(blobs[name])).lower()!=md5(blobs[name]):
            raise ValueError('full frozen application differs from ROM')
    ranges=range_plan(len(blobs['candidate.bin']),select['offset'])
    untouched=[{'offset':o,'length':n,'md5':esp.flash_md5sum(o,n).lower()} for o,n in complement(ranges)]
    rollback=bundle/'rollback'; rollback.mkdir()
    backups=[]
    backup_ranges=[(0x9000,SECTOR),(0x10000,8192)]
    if not discard_factory_contents: backup_ranges.insert(0,(0x800000,ranges[0][1]))
    for offset,length in backup_ranges:
        progress(bundle,state,'backup',backup_offset=offset,backup_done=0,backup_length=length)
        backups.append(backup_region(esp,offset,length,rollback/f'{offset:08x}.bin',
            lambda done,total: progress(bundle,state,'backup',backup_done=done,backup_length=total)))
    # Revalidate table/selectors captured before backups; no write until all complete.
    if (rollback/'00009000.bin').read_bytes()!=table or (rollback/'00010000.bin').read_bytes()!=selectors:
        raise ValueError('metadata changed during backup')
    other_index=(select['offset']-0x10000)//SECTOR
    old_selector=selectors[other_index*SECTOR:(other_index+1)*SECTOR]
    exclusive(rollback/'rollback-other-selector.bin',old_selector)
    if esp.flash_md5sum(select['offset'],SECTOR).lower()!=md5(old_selector):
        raise ValueError('other selector changed before rollback freeze')
    selector_restore={'file':'rollback-other-selector.bin','offset':select['offset'],'length':SECTOR,
                      'sha256':sha(old_selector),'md5':md5(old_selector)}
    by_offset={item['offset']:item for item in backups}
    restore_order=[selector_restore,by_offset[0x9000]]
    if not discard_factory_contents: restore_order.append(by_offset[0x800000])
    exclusive(rollback/'backup.json',json.dumps({'complete':not discard_factory_contents,'metadata_backups_complete':True,
        'all_overwritten_ranges_backed_up':not discard_factory_contents,
        'factory_backup_skipped':discard_factory_contents,'user_authorized_loss':discard_factory_contents,
        'mac':EXPECTED_MAC,'regions':backups,
        'untouched':untouched,'restore_order':restore_order,'preserved_selector_offset':0x10000+select['preserve_index']*SECTOR},indent=2).encode())
    instructions=['Explicit recovery authorization required; ROM/no-stub only. No automatic restore or reset. '
                  'Do NOT erase all, write the preserved VALID0 sector, or restore the full 8KiB otadata backup. '
                  + ('Restore in this exact order: other-selector -> old table. Factory contents overwritten by candidate are NOT backed up and CANNOT be restored from this bundle; user explicitly authorized loss. '
                     if discard_factory_contents else 'Restore in this exact order: other-selector -> old table -> factory-data span. ') +
                  'Verify the listed ROM MD5 immediately after EACH write before proceeding. '
                  'Loader/app0/old-app1 remain untouched. Table switching is NOT power-fail atomic.']
    for index,item in enumerate(restore_order,1):
        instructions.append(f"{index}. offset=0x{item['offset']:x} length=0x{item['length']:x} file={item['file']} sha256={item['sha256']} ROM_MD5={item['md5']}")
    exclusive(rollback/'RESTORE.txt',('\n'.join(instructions)+'\n').encode())
    progress(bundle,state,'backups-verified',backups_complete=not discard_factory_contents,
             metadata_backups_complete=True,factory_backup_skipped=discard_factory_contents,user_authorized_loss=discard_factory_contents)
    artifacts=[('candidate',0x800000,blobs['candidate.bin'].ljust(ranges[0][1],b'\xff')),
               ('table',0x9000,blobs['new-table.bin']+table[0xc00:]),
               ('selector',select['offset'],select['data'])]
    for label,offset,data in artifacts:
        progress(bundle,state,'write-'+label,write_offset=offset,write_length=len(data))
        esptool.write_flash(esp,[(offset,data)],flash_freq='keep',flash_mode='keep',flash_size='keep',
            no_compress=True,no_progress=True,erase_all=False,force=False)
        if esp.flash_md5sum(offset,len(data)).lower()!=md5(data):
            raise ValueError('written artifact MD5 mismatch; STOP in ROM')
        progress(bundle,state,'verified-'+label)
    progress(bundle,state,'verify-complements')
    for item in untouched:
        if esp.flash_md5sum(item['offset'],item['length']).lower()!=item['md5']:
            raise ValueError('protected complement changed; STOP in ROM')
    progress(bundle,state,'writes-verified',writes_verified=True,complements_verified=True,runtime_accepted=False)


def execute(bundle,port, *, discard_factory_contents=False):
    bundle=Path(bundle).resolve()
    if bundle==PRIVATE.resolve() or not bundle.is_relative_to(PRIVATE.resolve()): raise ValueError('private bundle required')
    expected={'candidate.bin','current-ota0.bin','old-ota1.bin','new-table.bin','plan.json'}
    if not bundle.is_dir() or {p.name for p in bundle.iterdir()}!=expected:
        raise ValueError('requires pristine frozen bundle; no automatic resume')
    # Exclusive reservation also blocks retry after a crash before progress.json exists.
    exclusive(bundle/'.execution-started',b'Execution attempted; no automatic resume.\n')
    state={'complete':False,'runtime_accepted':False,'last_success':None}
    progress(bundle,state,'validate-frozen-inputs')
    esp=None
    try:
        blobs=load_inputs(bundle)
        plan=json.loads((bundle/'plan.json').read_text())
        planned_discard=plan.get('discard_factory_contents',False)
        if type(planned_discard) is not bool or type(discard_factory_contents) is not bool or planned_discard != discard_factory_contents:
            raise ValueError('factory-loss authorization must match frozen plan and explicit CLI flag')
        progress(bundle,state,'connect')
        esp=esptool.detect_chip(port,connect_mode='no-reset',connect_attempts=1)
        preflight_rom(esp)
        perform(esp,bundle,blobs,state,discard_factory_contents=discard_factory_contents)
    except BaseException as exc:
        failed_stage=state['stage']
        progress(bundle,state,'failed',failed_stage=failed_stage,error_type=type(exc).__name__,automatic_recovery=False)
        raise
    finally:
        if esp is not None: esp._port.close()
    # Deliberately remain in ROM; reset/runtime acceptance is a separate human-controlled stage.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    freeze_paths=('image','table','current-image','old-image')
    freeze_hashes=('sha256','current-sha256','old-sha256')
    for name in freeze_paths:
        parser.add_argument('--'+name,type=Path)
    for name in freeze_hashes:
        parser.add_argument('--'+name)
    parser.add_argument('--port')
    parser.add_argument('--discard-factory-contents',action='store_true',
                        help='explicitly accept irreversible loss of overwritten factory resources; omit their backup')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true',help='freeze new bundle, then execute')
    mode.add_argument('--execute-frozen',action='store_true',help='execute an existing pristine frozen bundle only')
    args=parser.parse_args()
    if (args.execute or args.execute_frozen) and not args.port:
        parser.error('execution requires --port')
    freeze_values=[getattr(args,name.replace('-','_')) for name in freeze_paths+freeze_hashes]
    if args.execute_frozen:
        if any(value is not None for value in freeze_values):
            parser.error('--execute-frozen forbids freeze inputs; frozen bundle is authoritative')
        execute(args.bundle,args.port,discard_factory_contents=args.discard_factory_contents)
        return
    if any(value is None for value in freeze_values):
        parser.error('freeze requires image/table/current-image/old-image and all three SHA256 values')
    bundle=freeze(args.image,args.sha256,args.table,args.current_image,args.current_sha256,args.old_image,args.old_sha256,args.bundle,discard_factory_contents=args.discard_factory_contents)
    if args.execute: execute(bundle,args.port,discard_factory_contents=args.discard_factory_contents)
    else: print('DRY_PLAN_ONLY '+str(bundle))


if __name__=='__main__': main()
