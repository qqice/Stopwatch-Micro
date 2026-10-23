"""Offline endorsement of the separate closed-three-field GCT1 contract.

No device transport or gauge write path. Sign only after the release audit.
"""
import base64
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from ota_release import canonical_message, validate_manifest
from mosaico_factory_scale_capability import public_key, descriptor_version

CONTRACT=('GCT1|Factory3000Contrast|source=70a767be|C=91336fc0|B=675dd812|owned3|'
          'C112a_DCFCC65|B102a_DCFCC65|fullsource131|never_publish_physical_units|'
          'boot_restore_after_SDK_VALID|deadline_seconds=720|host_deadline_seconds=660|no_Failed_waiver')
DIGEST=hashlib.sha256(CONTRACT.encode('ascii')).hexdigest()
VERSIONS={'0.13.95-r15-mosaico-ota','0.13.95-r16-mosaico-ota'}

def canonical(manifest):
    m=validate_manifest(manifest)
    if m['schema']!=2 or m['layout']!='mosaico-dual-v2' or m['version'] not in VERSIONS:
        raise ValueError('requires exact GCT1 release version/layout')
    return ('MOSAICO-GAUGE-CONTRAST-CAP-v1\n'+DIGEST+'\n'+m['version']+'\n'+
            str(m['size'])+'\n'+m['sha256']+'\n').encode('ascii')

def verify_manifest(m,key):
    canonical(m)
    key.verify(base64.b64decode(m['signature'],validate=True),
        canonical_message(m['size'],m['sha256'],m['version'],layout=m['layout']),ec.ECDSA(hashes.SHA256()))

def verify_endorsement(m,cap,key):
    verify_manifest(m,key)
    if (not isinstance(cap,dict) or set(cap)!={'contract','version','size','sha256','signature'} or
        type(cap['size']) is not int or any(cap[k]!=v for k,v in (
            ('contract',DIGEST),('version',m['version']),('size',m['size']),('sha256',m['sha256'])))):
        raise ValueError('GCT1 capability binding mismatch')
    signature=base64.b64decode(cap['signature'],validate=True)
    if not 8<=len(signature)<=72:raise ValueError('DER signature length')
    key.verify(signature,canonical(m),ec.ECDSA(hashes.SHA256()))

def endorse(m,image,key,verified_public,*,audited=False):
    if audited is not True:raise ValueError('completed release audit required')
    verify_manifest(m,verified_public)
    if key.public_key().public_numbers()!=verified_public.public_numbers():
        raise ValueError('signer differs from embedded key')
    if len(image)!=m['size'] or hashlib.sha256(image).hexdigest()!=m['sha256']:
        raise ValueError('full BIN hash/size differs')
    descriptor_version(image,m['version'])
    cap={'contract':DIGEST,'version':m['version'],'size':m['size'],'sha256':m['sha256'],
         'signature':base64.b64encode(key.sign(canonical(m),ec.ECDSA(hashes.SHA256()))).decode('ascii')}
    verify_endorsement(m,cap,verified_public)
    return cap

def wire_pair(manifests,caps,key):
    if len(manifests)!=2 or len(caps)!=2 or {m['version'] for m in manifests}!=VERSIONS:
        raise ValueError('two distinct reviewed GCT1 images required')
    for m,c in zip(manifests,caps):verify_endorsement(m,c,key)
    return [{'image':m,'signature':c['signature']} for m,c in zip(manifests,caps)]
