// SPDX-License-Identifier: MIT
#include "mosaico_rx_ingress.h"
#include <atomic>
#include "esp_attr.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
namespace mosaico_rx_ingress {
namespace {
std::atomic<bool> enabled{false},pending{false};
static_assert(std::atomic<bool>::is_always_lock_free,"Disabled path must be lock free");
portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
DRAM_ATTR detail::MetadataRing ring;
Snapshot state;
void* staHandle=nullptr;
uint32_t generation=0,requestedSrc=0,requestedTtl=0;
uint16_t requestedPort=0;
bool wanted=false;
bool readHeader(void* ctx,std::size_t off,uint8_t* out,std::size_t n){std::memcpy(out,static_cast<const uint8_t*>(ctx)+off,n);return true;}
}
bool request(bool on,uint32_t src,uint16_t port,uint32_t ttl){
 if(on&&!detail::validTarget(src,port,ttl))return false;
 portENTER_CRITICAL(&mux);enabled.store(false);++generation;wanted=on;requestedSrc=src;requestedPort=port;requestedTtl=ttl;state.active=false;state.queued=true;pending.store(true);portEXIT_CRITICAL(&mux);return true;
}
Snapshot snapshot(){portENTER_CRITICAL(&mux);auto s=state;s.active=enabled.load();s.buffered=ring.count;portEXIT_CRITICAL(&mux);return s;}
std::size_t drain(Record* out,std::size_t cap){portENTER_CRITICAL(&mux);auto n=ring.pop(out,cap);portEXIT_CRITICAL(&mux);return n;}
void service(bool allowEnable){
 if(!allowEnable){request(false);portENTER_CRITICAL(&mux);pending.store(false);state.queued=false;portEXIT_CRITICAL(&mux);return;}
 if(!pending.load()&&!enabled.load())return;
 const auto now=uint32_t(esp_timer_get_time()/1000);
 bool on=false,command=false;uint32_t gen=0,src=0,ttl=0;uint16_t port=0;
 portENTER_CRITICAL(&mux);
 if(enabled.load()&&!detail::alive(now,state.deadlineMs)){enabled.store(false);state.active=false;++generation;}
 command=pending.exchange(false);if(command){on=wanted;gen=generation;src=requestedSrc;port=requestedPort;ttl=requestedTtl;state.queued=false;}
 portEXIT_CRITICAL(&mux);
 if(!command||!on)return;
 void* handle=esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
 portENTER_CRITICAL(&mux);
 if(gen==generation){state={};state.src=src;state.srcPort=port;state.deadlineMs=now+ttl*1000;ring.clear();staHandle=handle;state.lastError=handle?ESP_OK:ESP_ERR_INVALID_STATE;state.active=handle!=nullptr;enabled.store(state.active);}
 portEXIT_CRITICAL(&mux);
}
}
extern "C" esp_err_t __real_esp_netif_receive(esp_netif_t*,void*,size_t,void*);
extern "C" esp_err_t __wrap_esp_netif_receive(esp_netif_t* netif,void* buffer,size_t len,void* eb){
 using namespace mosaico_rx_ingress;
 // Disabled default: no clock, SDK call, buffer access or lock; original exactly once.
 if(!enabled.load(std::memory_order_acquire))return __real_esp_netif_receive(netif,buffer,len,eb);
 const uint64_t now=uint64_t(esp_timer_get_time());
 Record record;uint32_t gen=0,src=0;uint16_t port=0;bool observe=false,sta=false;
 portENTER_CRITICAL(&mux);
 if(enabled.load()&&detail::alive(uint32_t(now/1000),state.deadlineMs)){
  observe=true;gen=generation;record.localUs=now;record.ingressId=++state.seen;record.frameLength=uint16_t(len>65535?65535:len);sta=netif==staHandle;if(sta)++state.sta;src=state.src;port=state.srcPort;
 }else{enabled.store(false);state.active=false;}
 portEXIT_CRITICAL(&mux);
 if(observe&&sta)record.classification=buffer?detail::parse(readHeader,buffer,len,src,port,record):Classification::Invalid;
 const esp_err_t rc=__real_esp_netif_receive(netif,buffer,len,eb);
 // The original can release both buffer and eb: only immutable local metadata here.
 if(observe){record.netifResult=rc;const auto after=uint32_t(esp_timer_get_time()/1000);
  portENTER_CRITICAL(&mux);
  if(enabled.load()&&gen==generation&&detail::alive(after,state.deadlineMs)){
   if(rc==ESP_OK)++state.ok;else if(rc==ESP_ERR_NO_MEM)++state.noMem;else if(rc==ESP_FAIL)++state.fail;else ++state.other;
   if(sta){if(record.classification==Classification::Invalid)++state.invalid;else if(record.classification!=Classification::Target)++state.filtered;else{++state.target;if(!ring.push(record))++state.ringDrops;}}
  }else if(gen==generation){enabled.store(false);state.active=false;}
  portEXIT_CRITICAL(&mux);
 }
 return rc;
}
