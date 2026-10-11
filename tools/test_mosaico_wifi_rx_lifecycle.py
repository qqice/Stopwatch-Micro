#!/usr/bin/env python3
"""Execute real observer implementation with deterministic public API fault shims.

Shims test lifecycle, not SDK ABI; native IDF syntax checking independently verifies ABI.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import uuid

SHIMS={
'esp_attr.h':'#define DRAM_ATTR\n',
'esp_timer.h':'#include <cstdint>\nint64_t esp_timer_get_time();\n',
'freertos/FreeRTOS.h':'''#pragma once
using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
''',
'esp_wifi.h':'''#pragma once
#include <cstdint>
#define CONFIG_SOC_WIFI_HE_SUPPORT 1
using esp_err_t=int;
constexpr int ESP_OK=0, ESP_ERR_INVALID_STATE=259, ESP_ERR_WIFI_NOT_INIT=12289, WIFI_IF_STA=0;
constexpr uint32_t WIFI_PROMIS_FILTER_MASK_DATA=4;
enum wifi_promiscuous_pkt_type_t { WIFI_PKT_MGMT,WIFI_PKT_CTRL,WIFI_PKT_DATA,WIFI_PKT_MISC };
enum wifi_ps_type_t { WIFI_PS_NONE,WIFI_PS_MIN_MODEM,WIFI_PS_MAX_MODEM };
struct wifi_promiscuous_filter_t {uint32_t filter_mask;};
struct wifi_ap_record_t {uint8_t bssid[6];};
// ABI intentionally not mocked: just fields used by the compiled production callback.
struct wifi_pkt_rx_ctrl_t {uint32_t timestamp=0;uint16_t sig_len=0,dump_len=0;int8_t rssi=0;uint8_t rx_state=0;};
struct wifi_promiscuous_pkt_t {wifi_pkt_rx_ctrl_t rx_ctrl;uint8_t payload[160]{};};
using wifi_promiscuous_cb_t=void(*)(void*,wifi_promiscuous_pkt_type_t);
esp_err_t esp_wifi_get_promiscuous(bool*);
esp_err_t esp_wifi_set_promiscuous(bool);
esp_err_t esp_wifi_get_promiscuous_filter(wifi_promiscuous_filter_t*);
esp_err_t esp_wifi_set_promiscuous_filter(const wifi_promiscuous_filter_t*);
esp_err_t esp_wifi_get_ps(wifi_ps_type_t*);
esp_err_t esp_wifi_get_mac(int,uint8_t*);
esp_err_t esp_wifi_sta_get_ap_info(wifi_ap_record_t*);
esp_err_t esp_wifi_set_promiscuous_rx_cb(wifi_promiscuous_cb_t);
'''}
HARNESS=r'''
#include <cassert>
#include <cstring>
#include "esp_wifi.h"
#include "mosaico_wifi_rx_observer.h"
using namespace mosaico_wifi_rx_observer;
int64_t clockUs=1000000;bool promisc=false, failOff=false, failGet=false, failRestore=false, cancelEnable=false, roam=false;
int failStage=0,setCalls=0;bool unavailable=false;uint32_t filter=77;wifi_promiscuous_cb_t cb=nullptr;
uint8_t ownMac[6]={2,3,4,5,6,7},apMac[6]={8,9,10,11,12,13};
int64_t esp_timer_get_time(){return clockUs;}
int esp_wifi_get_promiscuous(bool* out){if(unavailable)return ESP_ERR_WIFI_NOT_INIT;if(failGet)return 123;*out=promisc;return 0;}
int esp_wifi_set_promiscuous(bool on){++setCalls;if(unavailable)return ESP_ERR_WIFI_NOT_INIT;if(!on && failOff)return 124;if(on && failStage==3)return 125;promisc=on;if(on && cancelEnable)request(false);return 0;}
int esp_wifi_get_promiscuous_filter(wifi_promiscuous_filter_t* p){p->filter_mask=filter;return 0;}
int esp_wifi_set_promiscuous_filter(const wifi_promiscuous_filter_t* p){if((p->filter_mask==77 && failRestore)||(p->filter_mask==4 && failStage==1))return 126;filter=p->filter_mask;return 0;}
int esp_wifi_get_ps(wifi_ps_type_t* p){*p=WIFI_PS_MIN_MODEM;return 0;}
int esp_wifi_get_mac(int,uint8_t* p){std::memcpy(p,ownMac,6);return 0;}
int esp_wifi_sta_get_ap_info(wifi_ap_record_t* p){std::memcpy(p->bssid,apMac,6);if(roam)p->bssid[0]^=1;return 0;}
int esp_wifi_set_promiscuous_rx_cb(wifi_promiscuous_cb_t p){if(failStage==2)return 127;cb=p;return 0;}
#include "mosaico_wifi_rx_observer.cpp"
int main(int argc,char** argv){assert(argc==2);const char* mode=argv[1];
 if(!std::strcmp(mode,"external")) {promisc=true;assert(request(true,2));service();assert(!snapshot().active && snapshot().lastError<0);request(false);service(false);assert(!snapshot().cleanupPending && promisc && setCalls==0);return 0;}
 if(!std::strcmp(mode,"cancel"))cancelEnable=true;
 if(!std::strcmp(mode,"failfilter"))failStage=1;
 if(!std::strcmp(mode,"failcallback"))failStage=2;
 if(!std::strcmp(mode,"failenable"))failStage=3;
 assert(!request(true,0) && !request(true,121));assert(request(true,2));service();
 if(cancelEnable || failStage){assert(!snapshot().active && !promisc && filter==77 && !snapshot().cleanupPending);return 0;}
 assert(snapshot().active && promisc && filter==4 && snapshot().originalPs==1 && snapshot().currentPs==1);
 wifi_promiscuous_pkt_t pkt;pkt.rx_ctrl.sig_len=100;pkt.rx_ctrl.dump_len=26;pkt.rx_ctrl.rssi=-44;
 pkt.payload[0]=0x88;pkt.payload[1]=2;std::memcpy(pkt.payload+4,ownMac,6);std::memcpy(pkt.payload+10,apMac,6);pkt.payload[24]=7;
 cb(&pkt,WIFI_PKT_DATA);assert(snapshot().target==1);Record rows[16];assert(drain(rows,16)==1 && rows[0].tid==7 && rows[0].length==100 && rows[0].rssi==-44);
 if(!std::strcmp(mode,"ttlcallback")){clockUs+=2000000;cb(&pkt,WIFI_PKT_DATA);assert(!snapshot().active && snapshot().target==1 && snapshot().cleanupPending);service();}
 else if(!std::strcmp(mode,"ttlowner")){clockUs+=2000000;service();}
 else if(!std::strcmp(mode,"roam")){roam=true;clockUs+=1000000;service();}
 else {request(false);
  if(!std::strcmp(mode,"notinit")){unavailable=true;promisc=false;service(false);assert(!snapshot().cleanupPending && !snapshot().actualKnown && snapshot().lastError==-ESP_ERR_WIFI_NOT_INIT);return 0;}
  if(!std::strcmp(mode,"failoff"))failOff=true;
  if(!std::strcmp(mode,"failget"))failGet=true;
  if(!std::strcmp(mode,"failrestore"))failRestore=true;
  service(false);
  if(failOff || failGet || failRestore){assert(snapshot().cleanupPending && snapshot().lastError<0);if(failGet)assert(!snapshot().actualKnown);
   if(failRestore){const auto s=snapshot();assert(s.actualKnown && !s.actualPromisc);assert(detail::canStopRadio(s));}
   failOff=failGet=failRestore=false;service(false);}
 }
 assert(!promisc && filter==77 && !snapshot().active && !snapshot().cleanupPending && snapshot().actualKnown && !snapshot().actualPromisc);
}
'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--ssh-host',required=True);args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    remote='/tmp/mosaico-wifi-mac-native-'+str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix='wifi-rx-lifecycle-') as tmp:
        d=Path(tmp)
        for name,text in SHIMS.items():
            f=d/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text)
        (d/'test.cpp').write_text(HARNESS)
        for name in ['mosaico_wifi_rx_observer.h','mosaico_wifi_rx_observer.cpp']:
            (d/name).write_bytes((root/'main/debug'/name).read_bytes())
        subprocess.run(['ssh',args.ssh_host,'mkdir '+remote],check=True)
        try:
            subprocess.run(['scp','-r',str(d)+'/.',args.ssh_host+':'+remote+'/'],check=True)
            cmd=f'cd {remote} && /usr/bin/clang++ -DESP_PLATFORM -I. -std=c++11 -Wall -Wextra -Werror test.cpp -o test && '
            cmd+=' && '.join('./test '+m for m in ['external','cancel','failfilter','failcallback','failenable','ttlcallback','ttlowner','roam','failoff','failget','failrestore','notinit','off'])
            cmd+=' && shasum -a 256 mosaico_wifi_rx_observer.cpp test.cpp'
            subprocess.run(['ssh',args.ssh_host,cmd],check=True)
        finally:
            names=list(SHIMS)+['test.cpp','test','mosaico_wifi_rx_observer.h','mosaico_wifi_rx_observer.cpp']
            cmd='rm -f '+' '.join(remote+'/'+n for n in names)+f' && rmdir {remote}/freertos {remote}'
            subprocess.run(['ssh',args.ssh_host,cmd],check=True)
    print('PASS real module: external ownership, OFF during enable, 3 enable failures, TTL callback/owner, roaming, off/get/filter retry, normal stop with pending filter, NOT_INIT lease release, HE dump_len vs sig_len, PS unchanged')

if __name__=='__main__':main()
