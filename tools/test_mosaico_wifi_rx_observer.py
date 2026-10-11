#!/usr/bin/env python3
"""Bounded production header tests; optional remote native compiler, no device access."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile
import uuid

HARNESS = r'''
#include "mosaico_wifi_rx_observer.h"
#include <cassert>
#include <cstring>
using namespace mosaico_wifi_rx_observer;
using namespace mosaico_wifi_rx_observer::detail;
struct Input { uint8_t bytes[160]{}; unsigned limit=24, furthest=0; bool fail=false; };
bool read(void* ctx,uint16_t off,uint8_t* out,uint16_t n) {
 auto& i=*static_cast<Input*>(ctx);assert(unsigned(off)+n<=i.limit);
 if(i.fail)return false;std::memcpy(out,i.bytes+off,n);
 if(unsigned(off)+n>i.furthest)i.furthest=unsigned(off)+n;return true;
}
int main() {
 uint8_t sta[6]={2,3,4,5,6,7},ap[6]={8,9,10,11,12,13};
 Input i;Record r;auto parseIt=[&](unsigned len){return parse(read,&i,len,sta,ap,r);};
 i.bytes[0]=8;i.bytes[1]=2;std::memcpy(i.bytes+4,sta,6);std::memcpy(i.bytes+10,ap,6);
 i.bytes[22]=0xb3;i.bytes[23]=0x12;
 assert(parseIt(160)==Parsed::Target && i.furthest==24 && r.sequence==0x12b && r.fragment==3 && r.tid==255);
 for(unsigned n=0;n<24;++n){i.furthest=0;assert(parseIt(n)==Parsed::Invalid);}
 i.bytes[0]=0x88;i.bytes[1]=0xca;i.limit=26;i.bytes[24]=5;i.bytes[25]=0;
 assert(parseIt(160)==Parsed::Target && i.furthest==26 && r.tid==5 && r.retry && r.protectedFrame);
 for(unsigned n=24;n<30;++n)assert(parseIt(n)==Parsed::Invalid);
 i.bytes[1]=2;assert(parseIt(26)==Parsed::Target);
 i.bytes[4]^=1;assert(parseIt(160)==Parsed::Filtered);i.bytes[4]^=1;
 i.bytes[10]^=1;assert(parseIt(160)==Parsed::Filtered);i.bytes[10]^=1;
 for(unsigned dir:{0u,1u}){i.bytes[1]=dir;assert(parseIt(160)==Parsed::Filtered);}
 // Mislabeled CTRL/MGMT/WDS/null/reserved headers must stop at FC, not body.
 for(unsigned fc:{0x0000u,0x00d4u,0x00c8u,0x0388u,0x0209u,0x0048u,0x0038u}) {
  i.bytes[0]=fc&255;i.bytes[1]=fc>>8;i.limit=2;i.furthest=0;
  assert(parseIt(160)==Parsed::Invalid && i.furthest==2);
 }
 i.fail=true;assert(parseIt(160)==Parsed::Invalid);i.fail=false;
 MetadataRing ring;for(unsigned n=0;n<128;++n){r.sequence=n;assert(ring.push(r));}assert(!ring.push(r));
 Record out[16];assert(ring.pop(nullptr,99)==0);assert(ring.pop(out,99)==16 && out[0].sequence==0 && out[15].sequence==15);
 for(unsigned n=0;n<16;++n)assert(ring.push(r));assert(!ring.push(r));ring.clear();assert(ring.pop(out,16)==0);
 assert(alive(0xfffffff0u,32) && alive(31,32) && !alive(32,32) && !alive(33,32));
 uint32_t ttl=0;assert(arguments("on","1",nullptr,ttl)==Command::On && ttl==1);
 Snapshot stop;
 assert(canStopRadio(stop));stop.cleanupPending=true;assert(!canStopRadio(stop));
 stop.actualKnown=true;assert(canStopRadio(stop));stop.actualPromisc=true;assert(!canStopRadio(stop));
 stop.cleanupPending=false;assert(canStopRadio(stop));
 assert(arguments("on","120",nullptr,ttl)==Command::On && ttl==120);
 for(const char* s:{"0","121","-1","+1","1s","","99999999999999"})assert(arguments("on",s,nullptr,ttl)==Command::Invalid);
 assert(arguments("on",nullptr,nullptr,ttl)==Command::Invalid);
 assert(arguments("on","1","extra",ttl)==Command::Invalid);
 assert(arguments(nullptr,nullptr,nullptr,ttl)==Command::Invalid);
 assert(arguments("off",nullptr,nullptr,ttl)==Command::Off);
 assert(arguments("status",nullptr,nullptr,ttl)==Command::Status);
 assert(arguments("drain",nullptr,nullptr,ttl)==Command::Drain);
 assert(arguments("off","1",nullptr,ttl)==Command::Invalid);
}
'''

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--compiler',default='c++')
    p.add_argument('--ssh-host')
    args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    header=root/'main/debug/mosaico_wifi_rx_observer.h'
    source=(root/'main/debug/mosaico_wifi_rx_observer.cpp').read_text()
    cb=source.split('void callback(',1)[1].split('void sample()',1)[0]
    assert all(x not in cb for x in ['printf(', 'malloc(', 'new ', 'esp_wifi_', 'set_ps(', 'free('])
    assert cb.count('esp_timer_get_time()')==1
    assert 'esp_wifi_set_ps(' not in source and 'twt_config(' not in source
    assert 'restoreFilter=true; owned=true;' in source
    assert 'state.actualKnown=g==ESP_OK' in source
    assert 'if(actual)return;' in source
    assert 'if(e==ESP_OK)state.actualPromisc=actual' in source
    with tempfile.TemporaryDirectory(prefix='wifi-rx-host-') as temp:
        d=Path(temp);(d/'test.cpp').write_text('#include <initializer_list>\n'+HARNESS)
        (d/header.name).write_bytes(header.read_bytes())
        if args.ssh_host:
            remote='/tmp/mosaico-wifi-mac-native-'+str(uuid.uuid4())
            subprocess.run(['ssh',args.ssh_host,'mkdir '+remote],check=True)
            try:
                subprocess.run(['scp',str(d/'test.cpp'),str(d/header.name),args.ssh_host+':'+remote+'/'],check=True)
                cmd=f'cd {remote} && /usr/bin/clang++ -std=c++11 -Wall -Wextra -Werror test.cpp -o test && ./test && shasum -a 256 test.cpp {header.name}'
                subprocess.run(['ssh',args.ssh_host,cmd],check=True)
            finally:
                subprocess.run(['ssh',args.ssh_host,f'rm -f {remote}/test {remote}/test.cpp {remote}/{header.name} && rmdir {remote}'],check=True)
        else:
            exe=d/'test'
            subprocess.run([args.compiler,'-std=c++11','-Wall','-Wextra','-Werror',str(d/'test.cpp'),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)
    print('PASS header-only parser, malformed/unsupported, QoS/HT length, target, no FCS/body reads, ring/drop/drain, TTL wrap, exact args')
    print('header_sha256='+hashlib.sha256(header.read_bytes()).hexdigest())

if __name__=='__main__':main()
