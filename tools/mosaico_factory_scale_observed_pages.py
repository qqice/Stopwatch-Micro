"""Strict cached131 page decoder; no transport, hardware proof or write route."""
import base64
import re
import zlib

ENCODING = 'le_addr2_width1_raw4_be_pad0_v1'
CONTEXT = ('generation', 'epoch', 'read_ms', 'cache_phase', 'stage', 'observed_crc')
FIXED = dict(version='1', cached='1', cached_only='1', no_i2c='1', no_nvs='1',
             total='131', authenticated='1', encoding=ENCODING)
KEYS = set(FIXED) | set(CONTEXT) | {'page', 'first', 'count', 'page_crc', 'data'}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def number(text, maximum):
    require(isinstance(text, str) and re.fullmatch(r'0|[1-9][0-9]*', text),
            'noncanonical page integer')
    value = int(text)
    require(value <= maximum, 'page integer overflow')
    return value


def decode(pages, expected):
    """Check all five pages against an independently trusted complete131 image.

    Caller must separately bracket the signed running image/journal/cache state.
    CRC and the supplied `authenticated` bit do not establish hardware trust.
    Expected records use index/address/width/raw (BE raw right-padded to 4 bytes),
    matching the existing finite-trial catalogue.
    """
    require(len(pages) == 5 and len(expected) == 131, 'complete five pages/131 required')
    fields, blobs, context = [], [], None
    for page_index, page in enumerate(pages):
        require(isinstance(page, dict) and set(page) == KEYS, 'page schema mismatch')
        require(all(isinstance(v, str) for v in page.values()), 'page values must be text')
        require(all(page[k] == v for k, v in FIXED.items()), 'not the readonly page protocol')
        require(number(page['page'], 4) == page_index, 'page missing/duplicate/out of order')
        first = number(page['first'], 130)
        count = number(page['count'], 32)
        require(first == page_index * 32 and count == min(32, 131 - first), 'page bounds')
        require(number(page['generation'], 2**32 - 1) > 0 and
                number(page['epoch'], 2**64 - 1) > 0 and
                number(page['read_ms'], 2**63 - 1) > 0, 'unobserved cache context')
        number(page['cache_phase'], 3)
        require(re.fullmatch(r'[A-Za-z0-9_-]{1,80}', page['stage']), 'invalid cache stage')
        require(re.fullmatch(r'[0-9a-f]{8}', page['observed_crc']) and
                re.fullmatch(r'[0-9a-f]{8}', page['page_crc']), 'noncanonical CRC')
        current = {k: page[k] for k in CONTEXT}
        require(context is None or current == context, 'mixed cache contexts')
        context = current
        require(len(page['data']) == ((count * 7 + 2) // 3) * 4 <= 300,
                'page base64 size')
        try:
            payload = base64.b64decode(page['data'], validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError('invalid page base64') from exc
        require(len(payload) == count * 7 <= 224, 'page payload size')
        require(zlib.crc32(payload) & 0xffffffff == int(page['page_crc'], 16), 'page CRC mismatch')
        for offset in range(count):
            index = first + offset
            record = payload[offset * 7:(offset + 1) * 7]
            address, width, raw = int.from_bytes(record[:2], 'little'), record[2], record[3:]
            require(width in (1, 2, 4) and not any(raw[width:]), 'invalid field width/padding')
            field = dict(index=index, address=f'{address:04x}', width=width, raw=raw.hex())
            wanted = expected[index]
            require(all(field[k] == wanted.get(k) for k in field), 'actual observed field mismatch')
            fields.append(field)
        blobs.append(payload)
    whole = b''.join(blobs)
    require(len(whole) == 917 and len(fields) == 131, 'incomplete observed image')
    require(zlib.crc32(whole) & 0xffffffff == int(context['observed_crc'], 16),
            'actual observed image CRC mismatch')
    return dict(fields=fields, context=context, observed_crc=context['observed_crc'])
