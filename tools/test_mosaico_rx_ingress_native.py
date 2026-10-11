"""Explicit SSH native ASan/UBSan tests; imports never access hardware or network."""
import argparse

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh",required=True,help="Authorized Mac compiler SSH alias")
    host=parser.parse_args().ssh
    from pathlib import Path
    import subprocess, uuid, tempfile
    root=Path(__file__).resolve().parents[1];out=root/'.artifacts/mosaico/rx-ingress-native-tests';out.mkdir(parents=True,exist_ok=True)
    headers={
    'esp_attr.h':'#define DRAM_ATTR\n',
    'esp_netif.h':'''#pragma once
    #include <cstddef>
    using esp_err_t=int;
    struct esp_netif_t {};
    #define ESP_OK 0
    #define ESP_FAIL -1
    #define ESP_ERR_NO_MEM 0x101
    #define ESP_ERR_INVALID_STATE 0x103
    extern "C" esp_netif_t* esp_netif_get_handle_from_ifkey(const char*);
    ''',
    'esp_timer.h':'#pragma once\n#include <cstdint>\nextern "C" int64_t esp_timer_get_time();\n',
    'freertos/FreeRTOS.h':'''#pragma once
    using portMUX_TYPE=int;
    #define portMUX_INITIALIZER_UNLOCKED 0
    void testLock();void testUnlock();
    #define portENTER_CRITICAL(p) ((void)(p),testLock())
    #define portEXIT_CRITICAL(p) ((void)(p),testUnlock())
    '''}
    harness=r'''
    #include "mosaico_rx_ingress.h"
    #include "esp_netif.h"
    #include <cassert>
    #include <initializer_list>
    #include <cstdlib>
    #include <sys/mman.h>
    #include <unistd.h>
    #include <cstdio>
    using namespace mosaico_rx_ingress;
    static int clocks=0,locks=0,calls=0,lookups=0,rc=0;
    static int64_t now=1000000;
    static esp_netif_t sta,foreign;
    static bool freeIt=false,offInReal=false,expireInReal=false,noHandle=false,offInLookup=false;
    static void* expectedBuffer;static void* expectedEb;static size_t expectedLen;static esp_netif_t* expectedNetif;
    void testLock(){++locks;}void testUnlock(){}
    extern "C" int64_t esp_timer_get_time(){++clocks;return now;}
    extern "C" esp_netif_t* esp_netif_get_handle_from_ifkey(const char* key){++lookups;assert(!std::strcmp(key,"WIFI_STA_DEF"));if(offInLookup)request(false);return noHandle?nullptr:&sta;}
    extern "C" esp_err_t __real_esp_netif_receive(esp_netif_t* n,void* b,size_t len,void* eb){++calls;assert(n==expectedNetif&&b==expectedBuffer&&len==expectedLen&&eb==expectedEb);if(freeIt)std::free(b);if(offInReal)request(false);if(expireInReal)now+=2000000;return rc;}
    extern "C" esp_err_t __wrap_esp_netif_receive(esp_netif_t*,void*,size_t,void*);
    int invoke(void* b,size_t len,esp_netif_t* n=&sta){expectedBuffer=b;expectedLen=len;expectedNetif=n;expectedEb=reinterpret_cast<void*>(0x1234);auto before=calls;auto result=__wrap_esp_netif_receive(n,b,len,expectedEb);assert(calls==before+1&&result==rc);return result;}
    void arm(uint32_t ttl=1){assert(request(true,0x2f6bf696,33380,ttl));auto n=lookups;service();assert(lookups==n+1);assert(snapshot().active);}
    void frame(uint8_t* b){std::memset(b,0,54);b[12]=8;b[14]=0x45;b[17]=40;b[23]=6;b[26]=47;b[27]=107;b[28]=246;b[29]=150;b[30]=10;b[33]=2;b[34]=0x82;b[35]=0x64;b[36]=1;b[37]=187;b[41]=7;b[45]=9;b[46]=0x50;b[47]=0x12;}
    struct Input {uint8_t b[256]{};size_t limit=54,furthest=0;};
    bool read(void* p,size_t off,uint8_t* b,size_t n){auto& x=*static_cast<Input*>(p);assert(off+n<=x.limit);x.furthest=off+n;std::memcpy(b,x.b+off,n);return true;}
    Classification parseIt(Input& x,size_t len){Record r;return detail::parse(read,&x,len,0x2f6bf696,33380,r);}
    int main(){
     assert(sizeof(Record)==48);assert(!snapshot().active);
     // OFF even invalid buffers cannot be accessed and must not lock/call clock.
     for(int code:{0,ESP_ERR_NO_MEM,ESP_FAIL,89}){rc=code;auto l=locks,t=clocks;invoke(reinterpret_cast<void*>(1),999);assert(locks==l&&clocks==t);}
     Input x;frame(x.b);assert(parseIt(x,54)==Classification::Target&&x.furthest==54);
     for(size_t n=0;n<54;++n){x.limit=n;assert(parseIt(x,n)==Classification::Invalid);}x.limit=54;
     x.b[12]=0x86;x.b[13]=0xdd;x.limit=14;assert(parseIt(x,200)==Classification::NonIpv4);frame(x.b);
     // Third VLAN rejected without copying third tag/body.
     x.b[12]=0x81;x.b[13]=0;x.b[16]=0x81;x.b[17]=0;x.b[20]=0x81;x.b[21]=0;x.limit=22;assert(parseIt(x,200)==Classification::Invalid);
     for(unsigned tags:{1u,2u}){frame(x.b);std::memmove(x.b+14+tags*4,x.b+14,40);x.b[12]=0x81;x.b[13]=0;for(unsigned i=0;i<tags;++i){x.b[14+i*4]=0;x.b[15+i*4]=0;x.b[16+i*4]=i+1==tags?8:0x81;x.b[17+i*4]=0;}x.limit=54+tags*4;assert(parseIt(x,x.limit)==Classification::Target);}
     frame(x.b);x.limit=34;x.b[14]=0x65;assert(parseIt(x,200)==Classification::Invalid);
     for(unsigned ih:{0u,4u}){frame(x.b);x.b[14]=0x40|ih;x.limit=34;assert(parseIt(x,200)==Classification::Invalid);}
     frame(x.b);x.b[23]=17;x.limit=34;assert(parseIt(x,200)==Classification::NonTcp);
     frame(x.b);x.b[20]=0x20;x.limit=34;assert(parseIt(x,200)==Classification::Fragment);
     for(unsigned th:{0u,4u,15u}){frame(x.b);x.b[46]=th<<4;x.limit=54;assert(parseIt(x,200)==Classification::Invalid);}
     frame(x.b);x.b[26]=48;assert(parseIt(x,54)==Classification::OtherTcp);
     // Header options validated but skipped; body read canary starts after TCP base.
     frame(x.b);std::memmove(x.b+74,x.b+34,20);x.b[14]=0x4f;x.b[17]=140;x.b[86]=0xf0;x.limit=94;assert(parseIt(x,154)==Classification::Target);
     uint32_t ip=0,ttl=0;uint16_t port=0;
     assert(detail::targetArguments("47.107.246.150","33380","120",ip,port,ttl));
     for(const char* t:{"0","121","-1","1s","999999999999"})assert(!detail::targetArguments("47.107.246.150","33380",t,ip,port,ttl));
     for(const char* p:{"0","65536","-1","x"})assert(!detail::targetArguments("47.107.246.150",p,"1",ip,port,ttl));
     for(const char* a:{"127.0.0.1","224.1.2.3","256.1.2.3","1.2.3","1.2.3.4x"})assert(!detail::targetArguments(a,"1","1",ip,port,ttl));
     uint8_t b[54];frame(b);Record rows[16];
     for(int code:{0,ESP_ERR_NO_MEM,ESP_FAIL,89}){arm();rc=code;invoke(b,54);auto s=snapshot();assert(s.target==1&&s.seen==1&&s.sta==1);assert(s.ok==(code==0)&&s.noMem==(code==ESP_ERR_NO_MEM)&&s.fail==(code==ESP_FAIL)&&s.other==(code==89));assert(drain(rows,99)==1&&rows[0].netifResult==code&&rows[0].seq==7&&rows[0].ack==9&&rows[0].src==0x2f6bf696);}
     arm();rc=0;invoke(b,54,&foreign);assert(snapshot().seen==1&&snapshot().sta==0&&snapshot().target==0);invoke(nullptr,54);assert(snapshot().invalid==1);
     arm();for(unsigned i=0;i<130;++i)invoke(b,54);assert(snapshot().target==130&&snapshot().ringDrops==2);assert(drain(rows,99)==16);
     arm();freeIt=true;auto heap=std::malloc(54);frame(static_cast<uint8_t*>(heap));invoke(heap,54);freeIt=false;assert(drain(rows,16)==1);
     arm();offInReal=true;invoke(b,54);offInReal=false;assert(!snapshot().active&&drain(rows,16)==0);
     arm();expireInReal=true;invoke(b,54);expireInReal=false;assert(!snapshot().active&&drain(rows,16)==0);
     arm();now+=1000000;invoke(b,54);assert(!snapshot().active&&snapshot().seen==0);auto l=locks,t=clocks;invoke(b,54);assert(locks==l&&clocks==t);
     arm();request(false);l=locks;t=clocks;invoke(b,54);assert(locks==l&&clocks==t);service();assert(!snapshot().active);
     noHandle=true;assert(request(true,0x2f6bf696,33380,1));service();assert(!snapshot().active&&snapshot().lastError==ESP_ERR_INVALID_STATE);noHandle=false;
     offInLookup=true;assert(request(true,0x2f6bf696,33380,1));service();assert(!snapshot().active&&snapshot().queued);offInLookup=false;service();assert(!snapshot().active);
     arm();service(false);assert(!snapshot().active);assert(!request(true,0x2f6bf696,33380,121));
     // Guard page immediately after valid fixed headers proves production wrapper no body read.
     auto page=size_t(sysconf(_SC_PAGESIZE));auto mem=static_cast<uint8_t*>(mmap(nullptr,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0));assert(mem!=MAP_FAILED);assert(!mprotect(mem+page,page,PROT_NONE));auto tail=mem+page-54;frame(tail);arm();invoke(tail,54);assert(drain(rows,16)==1);munmap(mem,page*2);
     std::printf("PASS production wrapper parser/forward/returns/off/TTL/ownership/freed-buffer/ring/gate/body-canary Record=%zu ring=%zu\n",sizeof(Record),sizeof(detail::MetadataRing));
    }
    '''
    (out/'test.cpp').write_text(harness)
    with tempfile.TemporaryDirectory(prefix='ingress-fixture-') as temp:
     d=Path(temp)
     for name,s in headers.items():p=d/name;p.parent.mkdir(exist_ok=True);p.write_text(s)
     for name in ['mosaico_rx_ingress.h','mosaico_rx_ingress.cpp']:(d/name).write_bytes((root/'main/debug'/name).read_bytes())
     (d/'test.cpp').write_text(harness)
     remote='/tmp/mosaico-ingress-native-'+str(uuid.uuid4())
     subprocess.run(['ssh',host,'mkdir '+remote+' '+remote+'/freertos'],check=True)
     try:
      files=[str(d/name) for name in headers if '/' not in name]+[str(d/'test.cpp'),str(d/'mosaico_rx_ingress.h'),str(d/'mosaico_rx_ingress.cpp')]
      subprocess.run(['scp',*files,host+':'+remote+'/'],check=True)
      subprocess.run(['scp',str(d/'freertos/FreeRTOS.h'),host+':'+remote+'/freertos/'],check=True)
      cmd=f'cd {remote} && /usr/bin/clang++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -I. test.cpp mosaico_rx_ingress.cpp -o test && ./test && shasum -a 256 test.cpp mosaico_rx_ingress.h mosaico_rx_ingress.cpp'
      result=subprocess.run(['ssh',host,cmd],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
      (out/'native.log').write_text(result.stdout);print(result.stdout);result.check_returncode()
     finally:
      names=' '.join(remote+'/'+n for n in ['test','test.cpp','mosaico_rx_ingress.h','mosaico_rx_ingress.cpp',*headers])
      subprocess.run(['ssh',host,'rm -f '+names+' && rmdir '+remote+'/freertos '+remote],check=True)
    

if __name__ == "__main__":
    main()
