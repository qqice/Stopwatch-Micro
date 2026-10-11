#!/usr/bin/env python3
"""Native sink fixture: actual observer.cpp, mocked SDK only, existing packet fixture."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from test_mosaico_rx_observer import HARNESS
ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''
#include <cstdint>
#include <cstring>
using portMUX_TYPE = int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
#define DRAM_ATTR
using esp_err_t = int;
const int ESP_OK=0, ESP_ERR_INVALID_STATE=-1;
using esp_event_base_t = int;
using esp_event_handler_instance_t = void*;
const int IP_EVENT=1, WIFI_EVENT=2, IP_EVENT_TX_RX=3, ESP_NETIF_TX=4, ESP_NETIF_RX=5;
const int WIFI_EVENT_TWT_WAKEUP=6, WIFI_EVENT_ITWT_SUSPEND=7, WIFI_EVENT_ITWT_PROBE=8, ESP_EVENT_ANY_ID=-1, WIFI_IF_STA=0;
struct ip4 { uint32_t addr; };
struct netif { ip4 ip={0x0a000001}; };
struct esp_netif_t {};
struct ip_event_tx_rx_t { esp_netif_t* esp_netif; int dir; };
struct wifi_ap_record_t {};
struct pbuf { const uint8_t* payload; uint16_t tot_len; };
static netif iface;
static esp_netif_t owner;
static uint32_t clockMs=100;
int64_t esp_timer_get_time() { return int64_t(clockMs)*1000; }
uint16_t pbuf_copy_partial(const pbuf* p,void* out,uint16_t n,uint16_t off) {
    if (uint32_t(off)+n>p->tot_len) return 0;
    std::memcpy(out,p->payload+off,n); return n;
}
ip4* netif_ip4_addr(netif* p) { return &p->ip; }
uint32_t lwip_ntohl(uint32_t n) { return n; }
esp_netif_t* esp_netif_get_handle_from_ifkey(const char*) { return &owner; }
void* esp_netif_get_netif_impl(esp_netif_t*) { return &iface; }
int esp_netif_tx_rx_event_enable(esp_netif_t*) { return ESP_OK; }
int esp_netif_tx_rx_event_disable(esp_netif_t*) { return ESP_OK; }
using EventFn=void(*)(void*,esp_event_base_t,int32_t,void*);
int esp_event_handler_instance_register(int,int,EventFn,void*,void**) { return ESP_OK; }
int esp_event_handler_instance_unregister(int,int,void*) { return ESP_OK; }
int esp_wifi_sta_get_ap_info(wifi_ap_record_t*) { return -1; }
int64_t esp_wifi_get_tsf_time(int) { return 0; }
'''
CASES = r'''
void rx(Input& i) {
    const auto original=i.b;
    pbuf p={i.b.data(),uint16_t(i.b.size())};
    assert(mosaico_rx_ip4_input(&p,&iface)==0);
    assert(i.b==original && p.payload==i.b.data() && p.tot_len==i.b.size());
}
int main() {
    parser_checks();
    uint32_t ip=77,ttl=78; uint16_t port=79;
    assert(targetArguments("10.0.0.2","443","1200",ip,port,ttl));
    assert(ip==0x0a000002 && port==443 && ttl==1200);
    for(const char* bad: {"", "0.0.0.0", "0.1.2.3", "127.0.0.1", "224.0.0.1", "255.255.255.255", "10.0.0", "10.0.0.2x", "10.0.0.256", "+10.0.0.2"}) {
        assert(!targetArguments(bad,"443","10",ip,port,ttl));
        assert(ip==0x0a000002 && port==443 && ttl==1200);
    }
    for(const char* bad: {"0", "65536", "-1", "+443", "443x", "42949672960", ""})
        assert(!targetArguments("10.0.0.2",bad,"10",ip,port,ttl));
    for(const char* bad: {"0", "1201", "-1", "+1", "1x", "42949672960", ""})
        assert(!targetArguments("10.0.0.2","443",bad,ip,port,ttl));
    auto i=packet(6,20,20,300);put16(i.b,20,443);
    Record out[32];
    assert(request(true,10));service();rx(i);
    assert(request(true,2,false,0x0a000002,443));service();
    assert(snapshot().discardedRecords==1 && drain(out,32)==0);
    auto before=snapshot();rx(i);
    i.b[15]=3;rx(i);i.b[15]=2;
    put16(i.b,20,444);rx(i);put16(i.b,20,443);
    auto udp=packet(17,20,8,4);rx(udp);
    mosaico_wg_tx_trace(1,1,2,3,4,5,6,7);
    mosaico_derp_trace(1,1,0,0,1,1);
    auto s=snapshot();assert(s.seen==before.seen+4 && s.tcp==before.tcp+3 && s.udp==before.udp+1);
    assert(s.targetMatch==1 && s.filtered==3 && s.txTrace[1]==1);
    assert(drain(out,32)==2 && out[0].protocol==6 && out[0].srcPort==443 && out[1].tracePhase==9);
    rx(i);assert(request(true,10,false,0x0a000002,444));service();
    assert(snapshot().discardedRecords==2 && drain(out,32)==0);
    assert(!request(true,10,false,0,443) && !request(true,10,false,0x0a000002,0));
    assert(!request(true,10,true,0x0a000002,443) && !request(true,1201,false,0x0a000002,443));
    assert(snapshot().filterSrcPort==444 && !snapshot().queued);
    assert(request(false));service();assert(!snapshot().active && !snapshot().filterSrcIp && !snapshot().filterSrcPort);
    assert(request(true,1));service();rx(i);assert(drain(out,32)==1);
    clockMs+=1000;rx(i);assert(drain(out,32)==0);service();assert(!snapshot().active);
    assert(request(true,1,false,0x0a000002,443));service(false);service();
    assert(!snapshot().active && !snapshot().queued && !snapshot().filterSrcIp);
    assert(request(true,1,false,0x0a000002,443));service();clockMs+=1000;
    mosaico_derp_trace(1,1,0,0,1,1);mosaico_wg_tx_trace(1,1,2,3,4,5,6,7);rx(i);
    assert(drain(out,32)==0);service();assert(!snapshot().active && !snapshot().filterSrcPort);
    assert(request(true,10,true));service();rx(i);mosaico_wg_tx_trace(1,1,2,3,4,5,6,7);
    mosaico_derp_trace(1,1,0,0,1,1);assert(drain(out,32)==1 && out[0].tracePhase==9);
    assert(request(false));service();assert(request(true,10));service();
    mosaico_wg_tx_trace(1,1,2,3,4,5,6,7);rx(i);assert(drain(out,32)==2);
    return 0;
}
'''
def harness():
    cpp=(ROOT/'main/debug/mosaico_rx_observer.cpp').read_text()
    cpp='\n'.join(line for line in cpp.splitlines() if not line.startswith(('#include', '#ifdef', '#endif')))
    parser=HARNESS.replace('int main() {','void parser_checks() {')
    return parser + MOCK + cpp + CASES

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--compiler',default=os.environ.get('CXX','c++'))
    ap.add_argument('--export-native-harness',type=Path)
    args=ap.parse_args()
    serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
    target=serial.split('if (!std::strcmp(action,"target")) {',1)[1].split('return;',1)[0]
    assert 'const char* extra = ::strtok_r' in target and '!extra &&' in target
    assert 'requestRxObservation(true,ttl,false,ip,port)' in target
    owner=(ROOT/'main/host/network_quota.cpp').read_text().split('bool NetworkQuota::requestRxObservation',1)[1].split('void NetworkQuota::serviceStandbySleep',1)[0]
    assert 'if (on && MosaicoOta::busy()) return false;' in owner
    assert 'request(on, ttlSeconds, controlOnly, filterSrcIp, filterSrcPort)' in owner
    assert 'xTaskNotifyGive(_task_handle)' in owner
    if args.export_native_harness:
        args.export_native_harness.write_text(harness());print('Exported native production sink fixture');return
    compiler=shutil.which(args.compiler)
    if not compiler:raise SystemExit('Native C++ compiler unavailable; export fixture for host execution')
    with tempfile.TemporaryDirectory(prefix='rx-target-test-') as d:
        source=Path(d)/'target.cpp';source.write_text(harness())
        exe=Path(d)/('target.exe' if os.name=='nt' else 'target')
        subprocess.run([compiler,'-std=c++11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'main/debug'),str(source),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print('PASS production observer sink: target/non-target, RX counters, TX suppression, DERP, selector discard, invalid unchanged, off/default, TTL, OTA gate, immutable pbuf')
if __name__=='__main__':main()
