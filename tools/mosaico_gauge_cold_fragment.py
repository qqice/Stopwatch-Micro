"""Offline GCP1 -> bounded GCS1 transport fragment. Never opens a device/key.

The receiving firmware must already hold the EXACT original pair proof and
must authenticate the rebuilt GCP1 plus actual images and current source.
This converter is not a hardware admission or an experiment controller.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct

import mosaico_gauge_cold_capability as cap


def build_fragment(payload, public_key, images, exact_pair, expected_binding=None):
    verification = cap.verify_payload(payload, public_key, images, expected_binding)
    _, binding_wire, pair_raw, signature = cap.decode_payload(payload)
    cap.require(type(exact_pair) is bytes and pair_raw == exact_pair,
                'receiver pair proof must be byte-identical, not reserialized JSON')
    fragment = b'GCS1' + binding_wire + struct.pack('<H', len(signature)) + signature
    cap.require(202 <= len(fragment) <= 266, 'fragment bounds')
    text = base64.b64encode(fragment)
    cap.require(len(text) <= 356, 'fragment exceeds local transport bound')
    return text, verification | dict(fragment_sha256=hashlib.sha256(fragment).hexdigest(),
        fragment_bytes=len(fragment), base64_bytes=len(text), exact_receiver_pair=True,
        private_key_read=False, ports_opened=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--receiver-pair-proof', type=Path, required=True)
    parser.add_argument('--public-key-header', type=Path, required=True)
    parser.add_argument('--slot0-image', type=Path, required=True)
    parser.add_argument('--slot1-image', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.output is not None:
        cap.require(args.output.parent.is_dir() and not args.output.exists(), 'new output required')
    payload = cap.read_limited(args.payload, cap.PAYLOAD_MAX)
    pair = cap.decode_pair_file(cap.read_limited(args.receiver_pair_proof, 4096).decode('ascii'))
    images = [cap.read_limited(path, 0x600000) for path in (args.slot0_image, args.slot1_image)]
    text, report = build_fragment(payload, cap.public_key(args.public_key_header), images, pair)
    if args.output is not None:
        cap.atomic_output(args.output, text + b'\n')
    print(json.dumps(report | dict(dry_run=args.output is None,
        output=str(args.output) if args.output is not None else None), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
