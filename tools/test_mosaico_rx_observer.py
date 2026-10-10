#!/usr/bin/env python3
"""Compile/run production header parser and ring; no IDF, device, or packet body logging."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HARNESS = r'''
#include "mosaico_rx_observer.h"
#include <cassert>
#include <cstring>
#include <vector>
using namespace mosaico_rx_observer;
using namespace mosaico_rx_observer::detail;
struct Input { std::vector<uint8_t> b; unsigned limit=0, furthest=0; };
// Emulate fragmented pbuf chains: every byte is a separate segment.
bool read(void* ctx,uint16_t off,uint8_t* out,uint16_t n) {
    Input& i=*static_cast<Input*>(ctx);
    assert(unsigned(off)+n<=i.limit); i.furthest=unsigned(off)+n>i.furthest?unsigned(off)+n:i.furthest;
    if (unsigned(off)+n>i.b.size()) return false;
    for(unsigned j=0;j<n;++j) { out[j]=i.b[off+j]; }
    return true;
}
void put16(std::vector<uint8_t>& b,unsigned o,unsigned v) { b[o]=uint8_t(v>>8);b[o+1]=uint8_t(v); }
Input packet(unsigned proto,unsigned ih,unsigned th,unsigned body) {
    Input i; i.b.resize(ih+th+body,0x5a);i.limit=ih+th;
    i.b[0]=uint8_t(0x40+ih/4);put16(i.b,2,i.b.size());put16(i.b,6,0);i.b[9]=uint8_t(proto);
    i.b[12]=10;i.b[13]=0;i.b[14]=0;i.b[15]=2;i.b[16]=10;i.b[17]=0;i.b[18]=0;i.b[19]=1;
    put16(i.b,10,0);uint32_t s=sum(i.b.data(),ih);while(s>>16)s=(s&65535)+(s>>16);put16(i.b,10,(~s)&65535);
    if(proto==6) { i.b[ih+12]=uint8_t(th/4<<4);i.b[ih+13]=0x12;put16(i.b,ih+16,0);
        s=sum(i.b.data()+12,8)+6+th+body;s=sum(i.b.data()+ih,th,s);while(s>>16)s=(s&65535)+(s>>16);put16(i.b,ih+16,(~s)&65535); }
    if(proto==17)put16(i.b,ih+4,th+body);
    return i;
}
int main() {
    Record r;
    auto i=packet(6,60,60,0);assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Tcp);
    assert(r.ipChecksum==Checksum::Valid&&r.tcpChecksum==Checksum::Valid&&r.tcpFlags==0x12);
    assert(i.furthest==120);
    i=packet(6,20,20,300);r={};assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Tcp);
    assert(r.transportPayloadLength==300&&r.tcpChecksum==Checksum::Unknown&&i.furthest==40);
    for(unsigned flag: {0x04u,0x10u,0x11u,0x18u}) { i.b[33]=uint8_t(flag);r={};assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Tcp&&r.tcpFlags==flag); }
    put16(i.b,6,0x2000);assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Fragment);
    put16(i.b,6,1);assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Fragment);
    put16(i.b,6,0);i.b[32]=0x40;assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Malformed);
    i.b[0]=0x44;assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Malformed);
    i=packet(6,20,20,0);assert(parse(read,&i,i.b.size()-1,0x0a000001,r)==Parsed::Malformed);
    assert(parse(read,&i,i.b.size(),0x0a000003,r)==Parsed::Ignore);
    const unsigned lengths[]={0,148,92,64,32};
    for(unsigned type=1;type<=4;++type) {
        i=packet(17,20,8,lengths[type]);i.limit=type==2?40:36;
        i.b[28]=uint8_t(type);i.b[29]=i.b[30]=i.b[31]=0;r={};
        assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::Wireguard&&r.wgType==type);
        assert(i.furthest==i.limit);
        i.b.pop_back();put16(i.b,2,i.b.size());put16(i.b,24,i.b.size()-20);
        assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::OtherUdp);
    }
    i=packet(17,20,8,4);i.limit=32;i.b[28]=4;i.b[29]=i.b[30]=i.b[31]=0;
    assert(parse(read,&i,i.b.size(),0x0a000001,r)==Parsed::OtherUdp);
    assert(alive(0xfffffff0u,0x20u)&&alive(0x1fu,0x20u)&&!alive(0x20u,0x20u));
    MetadataRing ring;for(unsigned n=0;n<128;++n){r.seq=n;assert(ring.push(r));}assert(!ring.push(r));
    Record out[32];assert(ring.pop(out,100)==32&&out[0].seq==0&&out[31].seq==31);
    for(unsigned n=0;n<32;++n) { assert(ring.push(r)); }
    assert(!ring.push(r));assert(ring.pop(nullptr,32)==0);
}
'''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compiler', default=os.environ.get('CXX', 'c++'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    compiler = shutil.which(args.compiler)
    if not compiler:
        raise SystemExit('Native C++ compiler unavailable; run on host with --compiler clang++')
    # Static invariants complement executable pure parser/ring checks.
    cpp = (root/'main/debug/mosaico_rx_observer.cpp').read_text()
    hook = cpp[cpp.index('extern "C" int mosaico_rx_ip4_input'):]
    assert 'return 0;' in hook and not any(x in hook for x in ['malloc(', 'new ', 'printf(', 'pbuf_free(', 'esp_wifi_get_tsf_time('])
    assert 'esp_wifi_sta_twt_config(' not in cpp
    assert 'if (e->dir == ESP_NETIF_TX) ++state.txEvents;' in cpp
    assert 'else if (e->dir == ESP_NETIF_RX) ++state.rxEvents;' in cpp
    with tempfile.TemporaryDirectory(prefix='rx-observer-test-') as d:
        source = Path(d)/'test.cpp'; source.write_text(HARNESS)
        exe = Path(d)/('test.exe' if os.name == 'nt' else 'test')
        subprocess.run([compiler,'-std=c++11','-Wall','-Wextra','-Werror','-I'+str(root/'main/debug'),str(source),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print('PASS production parser, chain boundaries, header-only reads, checksum, fragments, WG minimums, TTL wrap, ring drops')

if __name__ == '__main__': main()
