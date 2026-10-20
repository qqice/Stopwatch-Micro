#!/usr/bin/env python3
"""Read-only, CRC-checked NAND system journal export. No clear/format/restart.

Each immutable page is read once into the board's RAM cache, then fetched in
128-byte idempotent chunks. A failed/incomplete page is never called exported.
"""
import argparse,base64,contextlib,hashlib,io,json,struct,time,zlib
from pathlib import Path
from serial_debug_test import DebugClient

PAGE_MAGIC=0x31474c4d
TYPES={1:'boot',2:'gauge',3:'gauge_boot',4:'network',5:'power',6:'ota',7:'heap',
       8:'warning_fingerprint',9:'loss',10:'gauge_boot_outcome',11:'panic',12:'tcp',13:'gauge_engine'}

def fields(result):
    return dict(x.split('=',1) for x in result.details.split() if '=' in x)

def crc(data):return zlib.crc32(data)&0xffffffff
def signed16(n):return n-65536 if n&32768 else n

def decode_page(data,expected):
    if len(data)!=2048:raise ValueError('page_length')
    magic,version,header,seq,boot,count=struct.unpack_from('<IHHQIH',data)
    if magic!=PAGE_MAGIC or version!=1 or header!=60 or seq!=expected or not 1<=count<=31:
        raise ValueError('page_header')
    if struct.unpack_from('<I',data,24)[0]!=crc(data[:24]) or data[22:24]!=b'\0\0' or any(data[28:60]):
        raise ValueError('header_crc_or_reserved')
    if struct.unpack_from('<I',data,2044)[0]!=crc(data[:2044]) or any(data[60+count*64:2044]):
        raise ValueError('page_crc_or_padding')
    rows=[]
    for i in range(count):
        typ,flags,ms,epoch,*a=struct.unpack_from('<HHQQ11I',data,60+i*64)
        r=dict(page_seq=seq,page_boot=boot,index=i,type=TYPES.get(typ,'unknown'),type_id=typ,
               flags=flags,monotonic_ms=ms,epoch=epoch,args=a)
        if typ in (2,3):
            r['gauge']=dict(valid_mask=a[0],phase=a[1]&255,outcome=(a[1]>>8)&255,mode=(a[1]>>16)&255,
                soc=a[2],remaining_mah=a[3],fcc_mah=a[4],design_mah=a[5],voltage_mv=a[6],
                current_ma=signed16(a[7]&65535),average_current_ma=signed16(a[7]>>16),
                raw_cc=a[8]&65535,cycles=a[8]>>16,operation=a[9]&65535,battery=a[9]>>16,
                gauging=a[10]&65535,response_echo=a[10]>>16,
                protocol_tx=bool(a[0]&(1<<16)),protocol_echo_io=bool(a[0]&(1<<17)),protocol_data_io=bool(a[0]&(1<<18)))
        if typ==13:
            raw=struct.pack('<10I',*a[1:]);v=a[0]&65535
            e=dict(valid_mask=v,phase=(a[0]>>16)&255,outcome=a[0]>>24,
                   cedv_valid=bool(v&1),smooth_valid=bool(v&2),identity_valid=bool(v&4),
                   definition_match=bool(v&8),raw73=raw[:23].hex(),raw76=raw[23:].hex(),units='raw_not_calibration')
            if v&8:
                if v&1:e.update(CEDVRemCap=struct.unpack_from('<H',raw,0)[0],EDVThreshold=struct.unpack_from('<H',raw,16)[0])
                if v&2:
                    for j,key in enumerate(('SmoothRemCap','MaxVPrev','MaxVNow','DropRate','TimeToEDV','DeltaRM','SmoothCurrent','PresentThreshold')):
                        e[key]=struct.unpack_from('<H',raw,23+j*2)[0]
                    e['MaxDeltaSeconds']=raw[39]
            r['gauge_engine']=e
        rows.append(r)
    return rows

def read_page(client,seq,timeout=40):
    hi,lo=seq>>32,seq&0xffffffff
    r=client.command(f'debug syslog read {hi} {lo}','syslog',8)
    if r.status!='PASS':raise RuntimeError('read_request_refused')
    chunks=[];page_crc=None
    for index in range(16):
        deadline=time.monotonic()+timeout
        while True:
            # Tolerate a bounded CDC response pause during periodic work;
            # timeout alone does not establish a lost or corrupt NAND page.
            try:r=client.command(f'debug syslog chunk {hi} {lo} {index}','syslog',12)
            except TimeoutError:
                if time.monotonic()>=deadline:raise
                continue # Explicit bounded idempotent retry, never firmware mutation.
            d=fields(r)
            if r.status=='PASS':
                try:
                    chunk=base64.b64decode(d['data'],validate=True)
                    valid=(int(d['seq'])==seq and int(d['chunk'])==index and int(d['bytes'])==len(chunk)==128
                           and int(d['total'])==2048 and int(d['chunk_crc'],16)==crc(chunk))
                    p=int(d['page_crc'],16)
                    if not valid or (page_crc is not None and page_crc!=p):raise ValueError('chunk_integrity')
                except (ValueError,KeyError):
                    if time.monotonic()>=deadline:raise
                    continue
                page_crc=p;chunks.append(chunk);break
            if d.get('read_state')=='3':raise RuntimeError('persistent_read_failed')
            if time.monotonic()>=deadline:raise TimeoutError('read_cache_deadline')
            time.sleep(.5)
    data=b''.join(chunks)
    if page_crc!=crc(data[:2044]):raise ValueError('assembled_crc')
    decode_page(data,seq)
    return data

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True);p.add_argument('--uart',action='store_true')
    p.add_argument('--output',type=Path,required=True,help='New private directory, never overwritten')
    p.add_argument('--latest',type=int,default=4,help='Newest N committed pages; frozen range at start')
    args=p.parse_args()
    if not 1<=args.latest<=8191:raise SystemExit('latest must be 1..8191')
    args.output.mkdir(parents=True,exist_ok=False)
    capture=io.StringIO();client=None;manifest={'complete':False,'pages':[]}
    try:
        client=DebugClient(args.port,uart=args.uart)
        # Host-only tolerance; never reset USB or change device power policy.
        client.serial.write_timeout=5
        with contextlib.redirect_stdout(capture):
            client.handshake()
            r=client.command('debug ota-status','ota-status',10)
            if r.status!='PASS' or fields(r).get('busy')!='0' or fields(r).get('state')!='2':raise RuntimeError('ota_not_idle_valid')
            r=client.command('debug syslog status','syslog',8);s=fields(r)
            if r.status!='PASS' or s.get('ready')!='1':raise RuntimeError('journal_not_ready')
            first,last=int(s['oldest_seq']),int(s['latest_seq'])
        manifest.update(status=s,first=max(first,last-args.latest+1),last=last)
        rows=[]
        for seq in range(manifest['first'],last+1) if last else ():
            with contextlib.redirect_stdout(capture):data=read_page(client,seq)
            name=f'page-{seq:020d}.bin'
            with (args.output/name).open('xb') as f:f.write(data)
            manifest['pages'].append(dict(seq=seq,file=name,sha256=hashlib.sha256(data).hexdigest()))
            rows.extend(decode_page(data,seq))
            print(f'PAGE_VERIFIED seq={seq} records={len(rows)}',flush=True)
        with (args.output/'records.jsonl').open('x',encoding='utf8') as f:
            for row in rows:f.write(json.dumps(row,separators=(',',':'))+'\n')
        manifest['complete']=True
    finally:
        if client:client.close()
        with (args.output/'serial.log').open('x',encoding='utf8') as f:f.write(capture.getvalue())
        with (args.output/'manifest.json').open('x',encoding='utf8') as f:json.dump(manifest,f,indent=2)
    print('SYSTEM_LOG_EXPORT_COMPLETE pages='+str(len(manifest['pages'])))

if __name__=='__main__':main()
