"""Offline release-signer endorsement for the CLOSED GSJ3 recovery contract.

An endorsement is an explicit release-audit attestation, not a dynamic proof of
implementation correctness. Never signs on device or exports the private key.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from ota_release import canonical_message, validate_manifest

CONTRACT=('GSJ3|Factory3000RollbackToNominal65Virtual130|source=70a767be|target=b5b0f0d6|owned17|'
          'rollback3000=UNKNOWN_UNTRUSTED|applied_units=2|boot_restore_after_SDK_VALID|'
          'deadline_seconds=300|no_Failed_waiver')
DIGEST=hashlib.sha256(CONTRACT.encode('ascii')).hexdigest()
VERSIONS={'0.13.95-r6-mosaico-ota','0.13.95-r7-mosaico-ota'}
AUDITED_INPUTS=(
    'main/CMakeLists.txt','main/hal/hal.h','main/hal/mosaico/hal_mosaico.cpp',
    'main/hal/mosaico/mosaico_factory_scale_runtime.inc',
    'main/apps/app_codex_micro/view/view_mosaico.cpp',
    'main/hal/mosaico/mosaico_factory_scale_journal.cpp',
    'main/hal/mosaico/mosaico_factory_scale_journal.h',
    'main/hal/mosaico/mosaico_factory_scale_storage_model.h',
    'main/hal/mosaico/mosaico_factory_scale_journal_model.h',
    'main/hal/mosaico/mosaico_factory_scale_model.h',
    'main/hal/mosaico/mosaico_gauge_scale_runtime.inc',
    'main/hal/mosaico/mosaico_gauge_scale_trial.inc',
    'main/hal/mosaico/mosaico_native_recovery.inc',
    'main/hal/mosaico/mosaico_factory_audit.inc',
    'main/hal/mosaico/mosaico_maintenance_power.inc',
    'main/hal/mosaico/mosaico_maintenance_power_model.h',
    'main/hal/mosaico/mosaico_native_recovery_model.h',
    'main/ota/mosaico_ota.cpp','main/ota/mosaico_ota.h',
    'main/ota/mosaico_ota_maintenance_model.h',
    'main/hal/mosaico/mosaico_gauge_physical_model.h',
    'main/debug/serial_debug.cpp','main/debug/serial_debug.h',
    'main/debug/serial_debug_transport.h','main/main.cpp',
    'main/host/gauge_discharge_trace.cpp','main/host/mosaico_system_log.cpp')


def canonical(manifest):
    m=validate_manifest(manifest)
    if m['schema']!=2 or m['layout']!='mosaico-dual-v2' or m['version'] not in VERSIONS:
        raise ValueError('requires reviewed signed factory-profile firmware/version/layout')
    return ('MOSAICO-FACTORY-SCALE-CAP-v1\n'+DIGEST+'\n'+m['version']+'\n'+
            str(m['size'])+'\n'+m['sha256']+'\n').encode('ascii')


def public_key(header):
    text=Path(header).read_text(encoding='utf8')
    match=re.search(r'-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----',text,re.S)
    if not match:raise ValueError('embedded public key missing')
    key=serialization.load_pem_public_key(match.group().encode('ascii'))
    if not isinstance(key,ec.EllipticCurvePublicKey) or not isinstance(key.curve,ec.SECP256R1):
        raise ValueError('P256 public key required')
    return key


def verify_image_manifest(m,key):
    canonical(m)  # Exact normal schema, version, layout and numeric domains.
    signature=base64.b64decode(m['signature'],validate=True)
    key.verify(signature,canonical_message(m['size'],m['sha256'],m['version'],layout=m['layout']),ec.ECDSA(hashes.SHA256()))


def verify_endorsement(m,cap,key):
    verify_image_manifest(m,key)
    if (not isinstance(cap,dict) or set(cap)!={'contract','version','size','sha256','signature'} or
            any(cap[k]!=value for k,value in [('contract',DIGEST),('version',m['version']),('size',m['size']),('sha256',m['sha256'])]) or
            type(cap['size']) is not int or not isinstance(cap['signature'],str)):
        raise ValueError('capability binding mismatch')
    signature=base64.b64decode(cap['signature'],validate=True)
    if not 8<=len(signature)<=72:raise ValueError('P256 DER size invalid')
    key.verify(signature,canonical(m),ec.ECDSA(hashes.SHA256()))


def endorse(m,image,key,verified_public,*,audited=False):
    if audited is not True:raise ValueError('explicit completed release audit required')
    verify_image_manifest(m,verified_public)
    if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):
        raise ValueError('P256 private signing key required')
    if key.public_key().public_numbers()!=verified_public.public_numbers():
        raise ValueError('private signer does not match embedded verification key')
    if len(image)!=m['size'] or hashlib.sha256(image).hexdigest()!=m['sha256']:
        raise ValueError('complete BIN binding mismatch')
    descriptor_version(image,m['version'])
    cap={'contract':DIGEST,'version':m['version'],'size':m['size'],'sha256':m['sha256'],
         'signature':base64.b64encode(key.sign(canonical(m),ec.ECDSA(hashes.SHA256()))).decode('ascii')}
    verify_endorsement(m,cap,verified_public)
    return cap


def descriptor_version(image,expected):
    if len(image)<208 or image[48:80].split(b'\0',1)[0]!=expected.encode('ascii'):
        raise ValueError('BIN descriptor version does not match signed manifest')


def pack(pairs,key):
    if len(pairs)!=2:raise ValueError('exactly two audited images required')
    for m,c in pairs:verify_endorsement(m,c,key)
    if {m['version'] for m,c in pairs}!=VERSIONS:
        raise ValueError('two distinct reviewed release identities required')
    if len({m['sha256'] for m,c in pairs})!=2:
        raise ValueError('two distinct complete BIN hashes required')
    # Contract digest is implicit in the fixed signed canonical message, avoiding
    # repeated metadata exceeding UART's existing 1536-byte CRC-framed line.
    raw=json.dumps([{'image':m,'signature':c['signature']} for m,c in pairs],separators=(',',':')).encode('ascii')
    result=base64.b64encode(raw).decode('ascii')
    if len(result)>1400:raise ValueError('proof exceeds bounded device parser')
    return result


def exclusive_json(path,data):
    with Path(path).open('x',encoding='utf8') as out:json.dump(data,out,indent=2);out.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-header',type=Path,required=True)
    subs=parser.add_subparsers(dest='action',required=True)
    sign=subs.add_parser('endorse')
    for name in ('manifest','image','elf','freeze','key','output'):
        sign.add_argument('--'+name,type=Path,required=True)
    sign.add_argument('--confirm-audited-capability',action='store_true',required=True)
    proof=subs.add_parser('pack')
    proof.add_argument('--manifests',type=Path,nargs=2,required=True)
    proof.add_argument('--capabilities',type=Path,nargs=2,required=True)
    proof.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();pub=public_key(args.public_header)
    if args.action=='endorse':
        from mosaico_ota_release import validate_image
        m=json.loads(args.manifest.read_bytes());image=args.image.read_bytes();elf=args.elf.read_bytes()
        validate_image(image,layout='mosaico-dual-v2')
        if image[176:208]!=hashlib.sha256(elf).digest():raise ValueError('ELF binding mismatch')
        freeze=json.loads(args.freeze.read_bytes())
        for file,content in [('Stopwatch-Mosaico.bin',image),('Stopwatch-Mosaico.elf',elf)]:
            if freeze['files'][file]['sha256']!=hashlib.sha256(content).hexdigest():raise ValueError('freeze binding mismatch')
        if freeze['version']!=m['version']:raise ValueError('frozen firmware version mismatch')
        root=Path(__file__).resolve().parents[1]
        for file in AUDITED_INPUTS:
            if freeze['inputs_sha'].get(file)!=hashlib.sha256((root/file).read_bytes()).hexdigest():raise ValueError('audited source binding drift')
        key=serialization.load_pem_private_key(args.key.read_bytes(),password=None)
        exclusive_json(args.output,endorse(m,image,key,pub,audited=args.confirm_audited_capability))
    else:
        pairs=[(json.loads(m.read_bytes()),json.loads(c.read_bytes())) for m,c in zip(args.manifests,args.capabilities)]
        result=pack(pairs,pub)
        exclusive_json(args.output,{'contract':DIGEST,'encoded_proof':result,'encoded_bytes':len(result),'parameter_writes':False})


if __name__=='__main__':main()
