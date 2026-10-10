// SPDX-License-Identifier: MIT
#include "mosaico_rx_observer.h"
#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_netif_net_stack.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "lwip/netif.h"
#include "lwip/pbuf.h"
#include "lwip/def.h"
#include "esp_attr.h"

namespace mosaico_rx_observer {
namespace {
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
DRAM_ATTR detail::MetadataRing ring;
Snapshot state;
netif* sta = nullptr;
bool pending = false, requested = false, handlers = false;
uint32_t requestedTtl = 0, nextTsf = 0;
esp_netif_t* ownerNetif = nullptr;
esp_event_handler_instance_t ipHandler = nullptr, wifiHandler = nullptr;
uint32_t nowMs() { return uint32_t(esp_timer_get_time() / 1000); }
void events(void*, esp_event_base_t base, int32_t id, void* data) {
    const uint32_t now = nowMs();
    portENTER_CRITICAL(&mux);
    if (state.active && detail::alive(now, state.deadlineMs)) {
        if (base == IP_EVENT && id == IP_EVENT_TX_RX && data) {
            const auto* e = static_cast<const ip_event_tx_rx_t*>(data);
            if (e->esp_netif == ownerNetif) {
                if (e->dir == ESP_NETIF_TX) ++state.txEvents;
                else if (e->dir == ESP_NETIF_RX) ++state.rxEvents;
                state.eventMillis = now;
            }
        } else if (base == WIFI_EVENT) {
            if (id == WIFI_EVENT_TWT_WAKEUP) ++state.wakeEvents;
            else if (id == WIFI_EVENT_ITWT_SUSPEND) ++state.suspendEvents;
            else if (id == WIFI_EVENT_ITWT_PROBE) ++state.probeEvents;
            if (id == WIFI_EVENT_TWT_WAKEUP || id == WIFI_EVENT_ITWT_SUSPEND || id == WIFI_EVENT_ITWT_PROBE) state.twtEventMillis = now;
        }
    }
    portEXIT_CRITICAL(&mux);
}
bool readPbuf(void* ctx, uint16_t offset, uint8_t* out, uint16_t length) {
    return pbuf_copy_partial(static_cast<const pbuf*>(ctx), out, length, offset) == length;
}
void error(esp_err_t e) { portENTER_CRITICAL(&mux); state.lastError = e; portEXIT_CRITICAL(&mux); }
}
bool request(bool on, uint32_t ttlSeconds) {
    if (on && (!ttlSeconds || ttlSeconds > 1200)) return false;
    portENTER_CRITICAL(&mux);
    requested = on; requestedTtl = ttlSeconds; pending = true; state.queued = true;
    portEXIT_CRITICAL(&mux);
    return true;
}
Snapshot snapshot() { portENTER_CRITICAL(&mux); Snapshot s = state; portEXIT_CRITICAL(&mux); return s; }
std::size_t drain(Record* out, std::size_t capacity) {
    portENTER_CRITICAL(&mux);
    const std::size_t n = ring.pop(out, capacity);
    portEXIT_CRITICAL(&mux);
    return n;
}
void service(bool allowEnable) {
    const uint32_t now = nowMs();
    bool command, on; uint32_t ttl;
    portENTER_CRITICAL(&mux);
    command = pending || !allowEnable; on = requested && allowEnable; ttl = requestedTtl;
    pending = false; state.queued = false;
    bool expired = state.active && !detail::alive(now, state.deadlineMs);
    if ((command && !on) || expired) { state.active = false; sta = nullptr; }
    bool reporting = state.reporting;
    portEXIT_CRITICAL(&mux);
    if (((command && !on) || expired || !snapshot().active) && reporting) {
        esp_err_t e = esp_netif_tx_rx_event_disable(ownerNetif);
        error(e);
        if (e == ESP_OK) { portENTER_CRITICAL(&mux); state.reporting = false; portEXIT_CRITICAL(&mux); }
    }
    if (command && on) {
        esp_netif_t* candidate = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        if (!candidate) { error(ESP_ERR_INVALID_STATE); return; }
        if (!handlers) {
            esp_err_t e = esp_event_handler_instance_register(IP_EVENT, IP_EVENT_TX_RX, events, nullptr, &ipHandler);
            if (e != ESP_OK) { error(e); return; }
            e = esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, events, nullptr, &wifiHandler);
            if (e != ESP_OK) {
                esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_TX_RX, ipHandler);
                ipHandler = nullptr; error(e); return;
            }
            handlers = true;
        }
        netif* impl = static_cast<netif*>(esp_netif_get_netif_impl(candidate));
        if (!impl) { error(ESP_ERR_INVALID_STATE); return; }
        esp_err_t e = snapshot().reporting ? ESP_OK : esp_netif_tx_rx_event_enable(candidate);
        if (e != ESP_OK) { error(e); return; }
        portENTER_CRITICAL(&mux);
        ownerNetif = candidate; sta = impl;
        state.active = true; state.reporting = true; state.deadlineMs = now + ttl * 1000;
        state.lastError = ESP_OK;
        portEXIT_CRITICAL(&mux);
        nextTsf = now;
    }
    if (snapshot().active && !detail::alive(now, nextTsf)) {
        nextTsf = now + 1000;
        wifi_ap_record_t ap;
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
            const int64_t tsf = esp_wifi_get_tsf_time(WIFI_IF_STA);
            if (tsf > 0) { portENTER_CRITICAL(&mux); state.tsf = uint64_t(tsf); state.tsfMillis = now; portEXIT_CRITICAL(&mux); }
        }
    }
}
} // namespace mosaico_rx_observer
extern "C" int mosaico_rx_ip4_input(pbuf* p, netif* inp) {
    using namespace mosaico_rx_observer;
    const uint32_t now = nowMs();
    portENTER_CRITICAL(&mux);
    const bool enabled = p && inp && inp == sta && state.active && detail::alive(now, state.deadlineMs);
    portEXIT_CRITICAL(&mux);
    if (!enabled) return 0;
    Record r; r.millis = now;
    const uint32_t local = lwip_ntohl(netif_ip4_addr(inp)->addr);
    const auto result = detail::parse(readPbuf, p, p->tot_len, local, r);
    portENTER_CRITICAL(&mux);
    if (inp == sta && state.active && detail::alive(now, state.deadlineMs)) {
        if (result != detail::Parsed::Ignore) ++state.seen;
        if (result == detail::Parsed::Malformed) ++state.malformed;
        else if (result == detail::Parsed::Fragment) ++state.fragments;
        else if (result == detail::Parsed::OtherUdp) { ++state.udp; ++state.otherUdp; }
        else if (result == detail::Parsed::Tcp || result == detail::Parsed::Wireguard) {
            if (result == detail::Parsed::Tcp) ++state.tcp;
            else { ++state.udp; ++state.wireguard[r.wgType - 1]; }
            r.tsf = state.tsf; r.tsfMillis = state.tsfMillis;
            if (!ring.push(r)) ++state.ringDrops;
        }
    }
    portEXIT_CRITICAL(&mux);
    return 0;
}
#endif // ESP_PLATFORM

