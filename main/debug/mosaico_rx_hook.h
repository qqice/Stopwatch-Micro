// SPDX-License-Identifier: MIT
#pragma once
// Supplement SDK default hooks; return zero leaves packet ownership with lwIP.
#ifdef __cplusplus
extern "C" {
#endif
struct pbuf;
struct netif;
int mosaico_rx_ip4_input(struct pbuf* p, struct netif* inp);
#ifdef __cplusplus
}
#endif
#define LWIP_HOOK_IP4_INPUT(p, inp) mosaico_rx_ip4_input((p), (inp))
