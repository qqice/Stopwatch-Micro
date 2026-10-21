"""Pinned project-local IDF SPI cleanup compatibility TU; never edits the SDK."""
from pathlib import Path
import argparse
import hashlib
import os
import re

SOURCE_SHA256 = '51bdac6264b48c8215e768fdd46a2e7708d44257177455d44665bf1a857654e1'
SOURCE_REL = Path('components/esp_driver_spi/src/gpspi/spi_master.c')


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('SPI compatibility anchor count != 1: ' + old)
    return source.replace(old, new, 1)


def patch_source(source):
    # One definition, five calls. Fail closed even if a future SDK has new callers.
    if len(re.findall(r'\buninstall_priv_desc\s*\(', source)) != 6:
        raise ValueError('unexpected uninstall_priv_desc definition/call count')
    source = once(source,
        'void uninstall_priv_desc(spi_trans_priv_t* trans_buf)',
        'void uninstall_priv_desc(spi_trans_priv_t* trans_buf, bool copy_rx)')
    source = once(source,
        '    if (trans_buf->buffer_to_rcv != orig_rx_buffer) {\n'
        '        memcpy(orig_rx_buffer, trans_buf->buffer_to_rcv, (trans_desc->rxlength + 7) / 8);',
        '    if (trans_buf->buffer_to_rcv != NULL && trans_buf->buffer_to_rcv != orig_rx_buffer) {\n'
        '        if (copy_rx) {\n'
        '            memcpy(orig_rx_buffer, trans_buf->buffer_to_rcv, (trans_desc->rxlength + 7) / 8);\n'
        '        }')
    source = once(source, 'uninstall_priv_desc(priv_desc);', 'uninstall_priv_desc(priv_desc, false);')
    source = once(source, 'clean_up:\n    uninstall_priv_desc(&trans_buf);',
                  'clean_up:\n    uninstall_priv_desc(&trans_buf, false);')
    source = once(source, '    uninstall_priv_desc(&trans_buf);',
                  '    uninstall_priv_desc(&trans_buf, true);')
    source = once(source, 'uninstall_priv_desc(&priv_polling_trans);',
                  'uninstall_priv_desc(&priv_polling_trans, false);')
    source = once(source, 'uninstall_priv_desc(&host->cur_trans_buf);',
                  'uninstall_priv_desc(&host->cur_trans_buf, true);')
    return source


def generate(idf, output):
    idf, output = Path(idf).resolve(strict=True), Path(output).resolve()
    # Resolve existing symlinks/junctions before checking. SDK is strictly read-only.
    if output == idf or idf in output.parents:
        raise ValueError('output must not be inside the SDK')
    destination = (output / 'spi_master.c').resolve()
    if destination.parent != output or destination == idf or idf in destination.parents:
        raise ValueError('unsafe output file path')
    original = idf / SOURCE_REL
    if destination.exists() and (os.path.samefile(destination, original) or destination.stat().st_nlink != 1):
        raise ValueError('unsafe output hardlink')
    raw = original.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('unverified spi_master.c SHA256')
    patched = patch_source(raw.decode('utf-8').replace('\r\n', '\n'))
    output.mkdir(parents=True, exist_ok=True)
    data = patched.encode('utf-8')
    if not destination.exists() or destination.read_bytes() != data:
        destination.write_bytes(data)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--idf', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        generate(args.idf, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
