// SPDX-License-Identifier: MIT
#include "mosaico_wifi_rx_observer.h"
#ifdef ESP_PLATFORM
#include "esp_attr.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
namespace mosaico_wifi_rx_observer {
namespace {
portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
DRAM_ATTR detail::MetadataRing ring;
Snapshot state;
uint8_t own[6]{}, bssid[6]{};
bool pending=false, wanted=false, owned=false, restoreFilter=false;
uint32_t requestedTtl=0, nextSample=0;
wifi_promiscuous_filter_t originalFilter{};
int32_t diagnosticError(esp_err_t e) { return e==ESP_OK?0:-int32_t(e); }
void error(esp_err_t e) { portENTER_CRITICAL(&mux); state.lastError=diagnosticError(e); portEXIT_CRITICAL(&mux); }
bool readHeader(void* ctx,uint16_t off,uint8_t* out,uint16_t n) {
    std::memcpy(out,static_cast<const uint8_t*>(ctx)+off,n); return true;
}
void callback(void* buf,wifi_promiscuous_pkt_type_t type) {
    // Sole allowed SDK call: monotonic clock. No driver calls/logging/body reads.
    const uint64_t now=uint64_t(esp_timer_get_time());
    portENTER_CRITICAL(&mux);
    if (!state.active || !detail::alive(uint32_t(now/1000),state.deadlineMs)) {
        if(state.active)state.cleanupPending=true;
        state.active=false; portEXIT_CRITICAL(&mux); return;
    }
    ++state.seen;
    if (!buf || type!=WIFI_PKT_DATA) { ++state.filtered; portEXIT_CRITICAL(&mux); return; }
    const auto* p=static_cast<const wifi_promiscuous_pkt_t*>(buf);
    Record r; r.localUs=now; r.timestampUs=p->rx_ctrl.timestamp;
    r.length=p->rx_ctrl.sig_len; r.rssi=int8_t(p->rx_ctrl.rssi); r.rxState=p->rx_ctrl.rx_state;
#if CONFIG_SOC_WIFI_HE_SUPPORT
    const uint16_t available=p->rx_ctrl.dump_len; // Native HE length explicitly excludes FCS.
    const bool invalidLength=available>r.length; // sig_len is MPDU; don't assume its FCS convention on HE.
#else
    const uint16_t available=r.length>=4?r.length-4:0;
    const bool invalidLength=r.length<4;
#endif
    const auto parsed=invalidLength?detail::Parsed::Invalid:
        detail::parse(readHeader,const_cast<uint8_t*>(p->payload),available,own,bssid,r);
    if(parsed==detail::Parsed::Invalid)++state.invalid;
    else if(parsed==detail::Parsed::Filtered)++state.filtered;
    else { ++state.target; if(!ring.push(r))++state.drops; }
    portEXIT_CRITICAL(&mux);
}
void sample() {
    bool actual=false; wifi_ps_type_t ps;
    const esp_err_t a=esp_wifi_get_promiscuous(&actual), p=esp_wifi_get_ps(&ps);
    portENTER_CRITICAL(&mux);
    state.actualKnown=a==ESP_OK; if(a==ESP_OK)state.actualPromisc=actual;
    state.psKnown=p==ESP_OK; if(p==ESP_OK)state.currentPs=int32_t(ps);
    if(a!=ESP_OK)state.lastError=diagnosticError(a);
    else if(p!=ESP_OK)state.lastError=diagnosticError(p);
    portEXIT_CRITICAL(&mux);
}
void driverUnavailable(esp_err_t e) {
    // Lease belonged to a now-uninitialized driver. Never restore its filter into a new instance.
    owned=false;restoreFilter=false;
    portENTER_CRITICAL(&mux);
    state.active=false;state.cleanupPending=false;state.actualKnown=false;state.psKnown=false;
    state.lastError=diagnosticError(e);
    portEXIT_CRITICAL(&mux);
}
void cleanup() {
    portENTER_CRITICAL(&mux); state.active=false;state.cleanupPending=owned || restoreFilter; portEXIT_CRITICAL(&mux);
    if(!owned && !restoreFilter)return;
    // Always verify actual driver state; failed teardown remains pending and retries.
    esp_err_t e=esp_wifi_set_promiscuous(false);
    if(e==ESP_ERR_WIFI_NOT_INIT){driverUnavailable(e);return;}
    if(e!=ESP_OK)error(e);
    bool actual=true; const esp_err_t g=esp_wifi_get_promiscuous(&actual);
    if(g==ESP_ERR_WIFI_NOT_INIT){driverUnavailable(g);return;}
    portENTER_CRITICAL(&mux); state.actualKnown=g==ESP_OK;
    if(g==ESP_OK)state.actualPromisc=actual;
    portEXIT_CRITICAL(&mux);
    if(g!=ESP_OK){error(g);return;}
    if(actual)return;
    owned=false;
    if(restoreFilter){e=esp_wifi_set_promiscuous_filter(&originalFilter);if(e==ESP_ERR_WIFI_NOT_INIT){driverUnavailable(e);return;}if(e!=ESP_OK){error(e);return;}restoreFilter=false;}
    // No callback getter exists: don't overwrite a pre-existing enabled user.
    // Our inert callback remains registered while disabled (no presumed prior callback).
    portENTER_CRITICAL(&mux); state.cleanupPending=false; portEXIT_CRITICAL(&mux);
    sample(); // Retain actual PS after teardown as well as after enable.
}
}
bool request(bool on,uint32_t ttl) {
    if(on && (!ttl || ttl>120))return false;
    portENTER_CRITICAL(&mux); wanted=on; requestedTtl=ttl; pending=true; state.queued=true;
    if(!on){if(state.active)state.cleanupPending=true;state.active=false;} // Never claim ownership of external promiscuous mode.
    portEXIT_CRITICAL(&mux); return true;
}
Snapshot snapshot(){portENTER_CRITICAL(&mux);auto s=state;portEXIT_CRITICAL(&mux);return s;}
std::size_t drain(Record* out,std::size_t capacity){portENTER_CRITICAL(&mux);auto n=ring.pop(out,capacity);portEXIT_CRITICAL(&mux);return n;}
void service(bool allowEnable) {
    const uint32_t now=uint32_t(esp_timer_get_time()/1000);
    bool command,on,active; uint32_t ttl;
    portENTER_CRITICAL(&mux);
    command=pending; on=wanted && allowEnable; ttl=requestedTtl; pending=false; state.queued=false;
    if(!allowEnable || (command && !on) || (state.active && !detail::alive(now,state.deadlineMs)))state.active=false;
    active=state.active;
    portEXIT_CRITICAL(&mux);
    if(!active && (owned || restoreFilter))cleanup();
    if(command && on) {
        if(owned || restoreFilter) { // Never replace an outstanding cleanup or active lease.
            error(ESP_ERR_INVALID_STATE); return;
        }
        bool actual=true; wifi_ps_type_t ps; wifi_ap_record_t ap; uint8_t mac[6];
        esp_err_t e=esp_wifi_get_promiscuous(&actual);
        portENTER_CRITICAL(&mux);state.actualKnown=e==ESP_OK;if(e==ESP_OK)state.actualPromisc=actual;portEXIT_CRITICAL(&mux);
        if(e!=ESP_OK){error(e);return;} if(actual){error(ESP_ERR_INVALID_STATE);return;}
        if((e=esp_wifi_get_promiscuous_filter(&originalFilter))!=ESP_OK ||
           (e=esp_wifi_get_ps(&ps))!=ESP_OK || (e=esp_wifi_get_mac(WIFI_IF_STA,mac))!=ESP_OK ||
           (e=esp_wifi_sta_get_ap_info(&ap))!=ESP_OK){error(e);return;}
        portENTER_CRITICAL(&mux);state.originalPs=int32_t(ps);state.currentPs=int32_t(ps);state.psKnown=true;
        std::memcpy(own,mac,6);std::memcpy(bssid,ap.bssid,6);ring.clear();state.cleanupPending=true;
        portEXIT_CRITICAL(&mux);
        restoreFilter=true; owned=true;
        wifi_promiscuous_filter_t dataFilter{};dataFilter.filter_mask=WIFI_PROMIS_FILTER_MASK_DATA;
        if((e=esp_wifi_set_promiscuous_filter(&dataFilter))!=ESP_OK ||
           (e=esp_wifi_set_promiscuous_rx_cb(callback))!=ESP_OK ||
           (e=esp_wifi_set_promiscuous(true))!=ESP_OK){error(e);cleanup();sample();return;}
        sample();
        const auto verified=snapshot();
        if(!verified.actualKnown || !verified.actualPromisc) {
            if(verified.actualKnown)error(ESP_ERR_INVALID_STATE);
            cleanup();return;
        }
        portENTER_CRITICAL(&mux);
        // Concurrent OFF request must not be undone by slow driver enable.
        state.active=!(pending && !wanted);state.deadlineMs=now+ttl*1000;
        state.lastError=0;state.actualKnown=true;state.actualPromisc=true;state.cleanupPending=!state.active;
        portEXIT_CRITICAL(&mux);
        if(!snapshot().active)cleanup(); // OFF during slow enable must not leave hardware on until another fetch.
        nextSample=now;
    }
    if((command || owned || restoreFilter) && !detail::alive(now,nextSample)) { nextSample=now+1000; sample();
        if(snapshot().active) { wifi_ap_record_t ap;uint8_t mac[6];
            const esp_err_t a=esp_wifi_sta_get_ap_info(&ap),m=esp_wifi_get_mac(WIFI_IF_STA,mac);
            if(a!=ESP_OK || m!=ESP_OK || std::memcmp(ap.bssid,bssid,6) || std::memcmp(mac,own,6)) {
                error(a!=ESP_OK?a:(m!=ESP_OK?m:ESP_ERR_INVALID_STATE));cleanup();
            }
        }
    }
}
}
#endif
