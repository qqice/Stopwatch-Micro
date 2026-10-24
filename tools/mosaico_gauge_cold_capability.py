"""Offline GCC1 session bytes only. No device, SDK/flash claim, OTA or network.

Default is verification/dry-run and NEVER reads a private key. --sign plus an
explicit private-key path is necessary to publish a new, non-overwriting file.
CRC checks canonical source packing, not authenticity or current device state.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import zlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from ota_release import canonical_message, validate_manifest, _unique_object
from mosaico_factory_scale_capability import public_key, descriptor_version
from mosaico_gauge_contrast_capability import DIGEST as GCT_DIGEST

CONTRACT = ('GCC1|ColdC65Once|GCT1source=70a767be|C=91336fc0|owned3|full131_backup|closed_ACK_only|'
    'sameboot_handoff_grace_ms=30000|armed_restore_trigger_ms=600000|armed_hard_ms=720000|'
    'newboot_capture_end_ms=60000|newboot_restore_hard_ms=120000|sdk_valid_two_signed_full_images|'
    'first_readonly|durable_claim_and_raw_before_restore|unknown_stop_no_retries|no_SOC_OCV_gain_EDV_writes|'
    '0091_exit_only|units_unknown|no_off_wallclock_guarantee|legacy_Failed_preserved')
CONTRACT_SHA = hashlib.sha256(CONTRACT.encode('ascii')).digest()
VERSIONS = frozenset(('0.13.95-r25-mosaico-ota', '0.13.95-r26-mosaico-ota'))
SESSION_DOMAIN = b'MOSAICO-GAUGE-COLD-CAP-v1\n'
SOURCE_DOMAIN = b'MOSAICO-GAUGE-COLD-SOURCE-v1\n'
BINDING_SIZE, HEADER_SIZE, PAYLOAD_MAX, PAIR_RAW_MAX = 188, 56, 1536, 1050
HEADER = struct.Struct('<4s6H32s8s')
BINDING_KEYS = frozenset(('mac', 'device', 'firmware', 'build', 'recovery', 'source_crc', 'target_crc',
    'original_gct_generation', 'original_gct_epoch', 'nonce', 'slot_hashes', 'contract_digest',
    'source131_receipt_digest', 'capture_budget_ms'))
PUBLIC131_LAYOUT = (
    (0x9180,2),(0x9182,2),(0x9184,4),(0x9188,4),(0x91b4,1),(0x91b5,1),(0x91b6,1),(0x91b7,1),(0x91b8,2),(0x91ba,2),(0x91bc,2),(0x91be,2),(0x91c0,2),(0x91c2,2),(0x91c4,2),(0x91c6,2),(0x91c8,2),(0x91ca,2),(0x91cc,2),(0x91ce,2),(0x91d0,2),(0x91d2,2),(0x91d4,2),(0x91d6,2),(0x91d8,2),(0x91da,1),(0x91db,2),(0x91dd,1),(0x91de,1),(0x91df,1),(0x91f5,2),(0x91f7,2),(0x91f9,2),(0x91fb,2),(0x91fd,2),(0x9201,2),(0x9206,2),(0x9208,2),(0x920b,1),(0x920c,1),(0x920d,1),(0x920e,2),(0x9210,2),(0x9212,2),(0x9217,2),(0x9219,1),(0x921a,2),(0x921c,2),(0x921e,1),(0x921f,1),(0x9228,2),(0x922a,2),(0x922c,2),(0x922e,2),(0x9230,1),(0x9231,1),(0x9232,2),(0x9234,1),(0x9235,2),(0x9237,2),(0x9239,1),(0x923a,2),(0x923c,1),(0x9240,2),(0x9242,1),(0x9243,2),(0x9251,2),(0x925b,1),(0x9264,2),(0x9268,1),(0x9269,2),(0x926b,2),(0x926d,2),(0x926f,1),(0x9270,1),(0x9271,1),(0x9272,2),(0x9274,2),(0x9276,2),(0x927b,1),(0x927c,1),(0x927d,1),(0x927f,2),(0x9281,1),(0x9282,2),(0x9284,2),(0x9286,1),(0x9287,1),(0x9288,2),(0x928a,2),(0x928c,1),(0x928d,1),(0x928e,2),(0x9290,2),(0x9292,1),(0x9293,1),(0x9294,2),(0x9296,2),(0x9298,1),(0x9299,1),(0x929a,1),(0x929b,2),(0x929d,2),(0x929f,2),(0x92a3,2),(0x92a5,2),(0x92a7,2),(0x92a9,2),(0x92ab,2),(0x92ad,2),(0x92af,2),(0x92b1,1),(0x92b2,1),(0x92b3,1),(0x92b4,2),(0x92b6,1),(0x92b7,2),(0x92b9,1),(0x92ba,2),(0x92bc,1),(0x92bd,2),(0x92bf,2),(0x92c1,2),(0x92c3,2),(0x92c5,2),(0x92c7,2),(0x92c9,2),(0x92cb,2),(0x92cd,2),(0x92cf,2),(0x92d1,2))
assert len(PUBLIC131_LAYOUT) == 131 and sum(w for _, w in PUBLIC131_LAYOUT) == 222


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, 'integer domain')
    return value


def hexbytes(value, length, nonzero=False):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(2 * length) + '}', value), 'canonical lowercase hex required')
    out = bytes.fromhex(value)
    require(not nonzero or any(out), 'nonzero binding required')
    return out


def validate_binding(b):
    require(isinstance(b, dict) and set(b) == BINDING_KEYS, 'complete explicit Binding fields required')
    mac = hexbytes(b['mac'], 6, True)
    require(not mac[0] & 1 and b['device'] == '0220' and b['firmware'] == '0003' and
        type(b['build']) is int and b['build'] == 4 and type(b['recovery']) is int and b['recovery'] == 1,
        'production unicast/known gauge/SlotHashes binding required')
    require(b['source_crc'] == '70a767be' and b['target_crc'] == '91336fc0', 'fixed source/C profile only')
    integer(b['original_gct_generation'], 1, 0xffffffff - 4)  # Full GCT apply+restore headroom.
    integer(b['original_gct_epoch'], 1, 0xffffffffffffffff - 4)
    integer(b['capture_budget_ms'], 1, 60000)
    hexbytes(b['nonce'], 16, True)
    require(isinstance(b['slot_hashes'], list) and len(b['slot_hashes']) == 2, 'two ordered BIN hashes required')
    slots = [hexbytes(s, 32, True) for s in b['slot_hashes']]
    require(slots[0] != slots[1] and hexbytes(b['contract_digest'], 32) == bytes(32), 'distinct SlotHashes and zero unused contract field')
    hexbytes(b['source131_receipt_digest'], 32, True)
    return b


def encode_binding(binding):
    b = validate_binding(binding)
    body = (bytes.fromhex(b['mac']) + struct.pack('<HHBBIIIQ', 0x0220, 3, 4, 1, 0x70a767be, 0x91336fc0,
        b['original_gct_generation'], b['original_gct_epoch']) + bytes.fromhex(b['nonce']) +
        b''.join(bytes.fromhex(h) for h in b['slot_hashes']) + bytes.fromhex(b['contract_digest']) +
        bytes.fromhex(b['source131_receipt_digest']) + struct.pack('<I', b['capture_budget_ms']))
    require(len(body) == 180, 'Binding body size')
    return b'GCB1' + struct.pack('<HH', 1, BINDING_SIZE) + body


def decode_binding(wire):
    require(type(wire) is bytes and len(wire) == BINDING_SIZE and wire[:8] == b'GCB1' + struct.pack('<HH', 1, BINDING_SIZE), 'Binding header/length')
    raw = wire[8:]
    device, firmware, build, recovery, source, target, generation, epoch = struct.unpack_from('<HHBBIIIQ', raw, 6)
    b = dict(mac=raw[:6].hex(), device=f'{device:04x}', firmware=f'{firmware:04x}', build=build, recovery=recovery,
        source_crc=f'{source:08x}', target_crc=f'{target:08x}', original_gct_generation=generation, original_gct_epoch=epoch,
        nonce=raw[32:48].hex(), slot_hashes=[raw[48:80].hex(), raw[80:112].hex()], contract_digest=raw[112:144].hex(),
        source131_receipt_digest=raw[144:176].hex(), capture_budget_ms=struct.unpack_from('<I', raw, 176)[0])
    require(encode_binding(b) == wire, 'noncanonical Binding alias')
    return b


def source_receipt(raw222, mac, device, firmware, build, original_generation, original_epoch):
    """Canonical CRC checked, NOT collision-proof authentication/current matching."""
    require(type(raw222) is bytes and len(raw222) == 222 and type(mac) is bytes and len(mac) == 6 and any(mac) and not mac[0] & 1, 'raw222/MAC shape')
    require(type(device) is int and device == 0x0220 and type(firmware) is int and firmware == 3 and type(build) is int and build == 4, 'known gauge identity required')
    integer(original_generation, 1, 0xffffffff)
    integer(original_epoch, 1, 0xffffffffffffffff)
    canonical, offset = bytearray(), 0
    for address, width in PUBLIC131_LAYOUT:
        canonical += struct.pack('<HB', address, width) + raw222[offset:offset + width] + bytes(4 - width)
        offset += width
    require(zlib.crc32(canonical) & 0xffffffff == 0x70a767be, 'canonical source131 CRC mismatch')
    return hashlib.sha256(SOURCE_DOMAIN + mac + struct.pack('<HHBIQ', device, firmware, build, original_generation, original_epoch) + raw222).digest()


def p256_public(key):
    require(isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP256R1), 'P256 public key required')
    return key


def der(signature):
    require(type(signature) is bytes and 8 <= len(signature) <= 72, 'P256 DER length')
    r, s = utils.decode_dss_signature(signature)
    require(r > 0 and s > 0 and utils.encode_dss_signature(r, s) == signature, 'canonical DER required')
    return signature


def decode_pair_file(text):
    require(isinstance(text, str), 'base64 pair text required')
    text = text.strip()
    require(0 < len(text) <= 1400, 'bounded old pair proof required')
    raw = base64.b64decode(text, validate=True)
    require(base64.b64encode(raw).decode('ascii') == text, 'noncanonical pair base64')
    return raw  # Never reserialize/reformat the decoded old pair bytes.


def validate_pair(pair_raw, key, slot_images):
    p256_public(key)
    require(type(pair_raw) is bytes and 0 < len(pair_raw) <= PAIR_RAW_MAX and b'\0' not in pair_raw, 'pair raw length/NUL')
    pairs = json.loads(pair_raw.decode('ascii'), object_pairs_hook=_unique_object)
    require(isinstance(pairs, list) and len(pairs) == 2 and all(isinstance(p, dict) and set(p) == {'image', 'signature'} and
        isinstance(p['image'], dict) and isinstance(p['signature'], str) for p in pairs), 'exact ordered old pair schema')
    require(isinstance(slot_images, (list, tuple)) and len(slot_images) == 2 and all(type(b) is bytes for b in slot_images), 'two explicit local BIN byte strings required')
    require({p['image'].get('version') for p in pairs} == VERSIONS, 'exact production R25/R26 required; draft/older forbidden')
    for pair, image in zip(pairs, slot_images):
        m = validate_manifest(pair['image'])
        require(m['schema'] == 2 and m['layout'] == 'mosaico-dual-v2', 'normal signed v2 manifest required')
        key.verify(base64.b64decode(m['signature'], validate=True), canonical_message(m['size'], m['sha256'], m['version'], layout=m['layout']), ec.ECDSA(hashes.SHA256()))
        signature = base64.b64decode(pair['signature'], validate=True)
        require(base64.b64encode(signature).decode('ascii') == pair['signature'], 'canonical GCT endorsement base64')
        message = ('MOSAICO-GAUGE-CONTRAST-CAP-v1\n' + GCT_DIGEST + '\n' + m['version'] + '\n' + str(m['size']) + '\n' + m['sha256'] + '\n').encode('ascii')
        key.verify(der(signature), message, ec.ECDSA(hashes.SHA256()))
        require(len(image) == m['size'] and hashlib.sha256(image).hexdigest() == m['sha256'], 'provided local BIN size/SHA mismatch')
        descriptor_version(image, m['version'])
    require(pairs[0]['image']['sha256'] != pairs[1]['image']['sha256'], 'distinct recovery BINs required')
    return pairs


def endorse_pair(manifests, images, private, verified_public, *, audited=False):
    """Offline explicit release-audit endorsement, NOT SDK/hardware evidence.

    Preserve caller slot order. This does not change the old GCT roster or its
    signed contract/domain; it only endorses the exact new cold-capable pair.
    The caller supplies in-memory key objects; no file/key/device IO occurs.
    """
    require(audited is True, 'completed explicit cold release audit required')
    require(isinstance(private, ec.EllipticCurvePrivateKey) and isinstance(private.curve, ec.SECP256R1), 'P256 private key required')
    require(private.public_key().public_numbers() == p256_public(verified_public).public_numbers(), 'signer differs from verified public key')
    require(isinstance(manifests, (list, tuple)) and len(manifests) == 2, 'two explicit normal manifests required')
    require(isinstance(images, (list, tuple)) and len(images) == 2 and all(type(image) is bytes for image in images), 'two ordered full BIN byte strings required')
    normal = [dict(validate_manifest(manifest)) for manifest in manifests]
    require(all(m['schema'] == 2 and m['layout'] == 'mosaico-dual-v2' for m in normal) and
        {m.get('version') for m in normal} == VERSIONS, 'exact normal signed R25/R26 v2 pair required')
    require(normal[0]['sha256'] != normal[1]['sha256'], 'distinct ordered recovery BIN hashes required')
    # Validate BOTH complete normal releases before producing any endorsement.
    for manifest, image in zip(normal, images):
        verified_public.verify(base64.b64decode(manifest['signature'], validate=True),
            canonical_message(manifest['size'], manifest['sha256'], manifest['version'], layout=manifest['layout']),
            ec.ECDSA(hashes.SHA256()))
        require(len(image) == manifest['size'] and hashlib.sha256(image).hexdigest() == manifest['sha256'], 'provided ordered full BIN size/SHA mismatch')
        descriptor_version(image, manifest['version'])
    pairs = []
    for manifest in normal:
        message = ('MOSAICO-GAUGE-CONTRAST-CAP-v1\n' + GCT_DIGEST + '\n' + manifest['version'] + '\n' +
            str(manifest['size']) + '\n' + manifest['sha256'] + '\n').encode('ascii')
        signature = der(private.sign(message, ec.ECDSA(hashes.SHA256())))
        pairs.append(dict(image=manifest, signature=base64.b64encode(signature).decode('ascii')))
    raw_pair = json.dumps(pairs, separators=(',', ':'), allow_nan=False).encode('ascii')
    validate_pair(raw_pair, verified_public, images)
    return raw_pair


def session_message(binding_wire, pair_raw):
    decode_binding(binding_wire)
    require(type(pair_raw) is bytes and 0 < len(pair_raw) <= PAIR_RAW_MAX, 'pair raw bounds')
    return SESSION_DOMAIN + CONTRACT_SHA + hashlib.sha256(binding_wire).digest() + hashlib.sha256(pair_raw).digest()


def pack_payload(binding_wire, pair_raw, signature):
    session_message(binding_wire, pair_raw)
    der(signature)
    size = HEADER_SIZE + len(binding_wire) + len(pair_raw) + len(signature)
    require(size <= PAYLOAD_MAX, 'NAND payload exceeds1536')
    return HEADER.pack(b'GCP1', 1, HEADER_SIZE, size, BINDING_SIZE, len(pair_raw), len(signature), CONTRACT_SHA, bytes(8)) + binding_wire + pair_raw + signature


def decode_payload(payload):
    require(type(payload) is bytes and HEADER_SIZE <= len(payload) <= PAYLOAD_MAX, 'payload bounds')
    magic, version, header, total, binding_size, pair_size, sig_size, contract, reserved = HEADER.unpack_from(payload)
    require((magic, version, header, binding_size, contract, reserved) == (b'GCP1', 1, HEADER_SIZE, BINDING_SIZE, CONTRACT_SHA, bytes(8)) and
        0 < pair_size <= PAIR_RAW_MAX and 8 <= sig_size <= 72 and total == len(payload) == HEADER_SIZE + binding_size + pair_size + sig_size,
        'exact canonical payload header/EOF required')
    binding_wire = payload[HEADER_SIZE:HEADER_SIZE + BINDING_SIZE]
    pair_raw = payload[HEADER_SIZE + BINDING_SIZE:HEADER_SIZE + BINDING_SIZE + pair_size]
    return decode_binding(binding_wire), binding_wire, pair_raw, der(payload[-sig_size:])


def verify_payload(payload, key, slot_images, expected_binding=None):
    binding, binding_wire, pair_raw, signature = decode_payload(payload)
    if expected_binding is not None:
        require(encode_binding(expected_binding) == binding_wire, 'expected session Binding mismatch')
    pairs = validate_pair(pair_raw, key, slot_images)
    require(binding['slot_hashes'] == [p['image']['sha256'] for p in pairs], 'Binding hashes/order differ from pair')
    key.verify(signature, session_message(binding_wire, pair_raw), ec.ECDSA(hashes.SHA256()))
    return dict(contract_sha256=CONTRACT_SHA.hex(), binding_sha256=hashlib.sha256(binding_wire).hexdigest(),
        pair_raw_sha256=hashlib.sha256(pair_raw).hexdigest(), payload_sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload), provided_BIN_sha_size_descriptor_verified=True, actual_SDK_verified=False,
        actual_flash_verified=False, current_source_hardware_match=False, nonce_uniqueness_verified=False)


def sign_payload(binding, pair_raw, slot_images, private, verified_public, *, approved=False):
    require(approved is True, 'explicit session signing approval required')
    require(isinstance(private, ec.EllipticCurvePrivateKey) and isinstance(private.curve, ec.SECP256R1), 'P256 private key required')
    require(private.public_key().public_numbers() == p256_public(verified_public).public_numbers(), 'signer differs from verified public key')
    pairs = validate_pair(pair_raw, verified_public, slot_images)
    wire = encode_binding(binding)
    require(binding['slot_hashes'] == [p['image']['sha256'] for p in pairs], 'ordered actual input BIN hashes differ')
    signature = private.sign(session_message(wire, pair_raw), ec.ECDSA(hashes.SHA256()))
    payload = pack_payload(wire, pair_raw, signature)
    verify_payload(payload, verified_public, slot_images, binding)
    return payload


def atomic_output(path, data):
    """Same-directory fsynced temp, atomic hard-link publication; NEVER replace."""
    path = Path(path)
    require(path.parent.is_dir() and not path.exists(), 'new output under existing directory required')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic create-if-absent. No overwrite race/fallback.
        require(path.read_bytes() == data, 'published payload readback mismatch')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_limited(path, maximum):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    require(len(data) <= maximum, 'input exceeds bounded length')
    return data


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binding', type=Path, required=True)
    p.add_argument('--pair-proof', type=Path, required=True)
    p.add_argument('--public-key-header', type=Path, required=True)
    p.add_argument('--slot0-image', type=Path, required=True)
    p.add_argument('--slot1-image', type=Path, required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--source-raw222', type=Path)
    source.add_argument('--trusted-source-receipt-digest')
    p.add_argument('--sign', action='store_true')
    p.add_argument('--private-key', type=Path)
    p.add_argument('--output', type=Path)
    args = p.parse_args(argv)
    require(not args.sign or (args.private_key is not None and args.output is not None), '--sign requires explicit key and new output paths')
    if args.sign:
        require(args.output.parent.is_dir() and not args.output.exists(), 'new output required before private-key access')
    binding = json.loads(read_limited(args.binding, 8192), object_pairs_hook=_unique_object)
    wire = encode_binding(binding)
    raw = decode_pair_file(read_limited(args.pair_proof, 4096).decode('ascii'))
    key = public_key(args.public_key_header)
    images = [read_limited(path, 0x600000) for path in (args.slot0_image, args.slot1_image)]
    pairs = validate_pair(raw, key, images)
    require(binding['slot_hashes'] == [pair['image']['sha256'] for pair in pairs], 'ordered Binding BIN hashes differ')
    if args.source_raw222 is not None:
        receipt = source_receipt(read_limited(args.source_raw222, 222), bytes.fromhex(binding['mac']), 0x0220, 3, 4,
            binding['original_gct_generation'], binding['original_gct_epoch']).hex()
    else:
        hexbytes(args.trusted_source_receipt_digest, 32, True)
        receipt = args.trusted_source_receipt_digest
    require(receipt == binding['source131_receipt_digest'], 'source receipt does not bind these exact inputs')
    summary = dict(dry_run=not args.sign, private_key_read=False, contract_sha256=CONTRACT_SHA.hex(),
        contract_bytes=len(CONTRACT.encode('ascii')), binding_sha256=hashlib.sha256(wire).hexdigest(),
        pair_raw_sha256=hashlib.sha256(raw).hexdigest(), maximum_payload_bytes=HEADER_SIZE + BINDING_SIZE + len(raw) + 72,
        source_raw_canonical_crc_checked=args.source_raw222 is not None, source_receipt_trusted_input=args.source_raw222 is None,
        actual_SDK_verified=False, actual_flash_verified=False, current_source_hardware_match=False, ports_opened=0)
    if args.sign:
        private = serialization.load_pem_private_key(read_limited(args.private_key, 16384), password=None)
        payload = sign_payload(binding, raw, images, private, key, approved=True)
        atomic_output(args.output, payload)
        summary.update(verify_payload(payload, key, images, binding), private_key_read=True, output=str(args.output))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
