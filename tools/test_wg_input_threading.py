"""Offline input-dispatch contract tests; not a vendor or firmware build.

Set WG_TEST_CC to a host gcc/clang executable to also run the extracted C
ownership harness. The harness compiles only the dispatch and cleanup snippets.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MICROLINK = ROOT / "components/microlink-source/components/microlink"
WG = MICROLINK / "components/wireguard_lwip/src/wireguardif.c"
MGR = MICROLINK / "src/ml_wg_mgr.c"
DISPATCH = re.compile(
    r"if \(device->netif->input && device->netif->input\(pbuf, device->netif\) == ERR_OK\) \{"
    r"\s*pbuf = NULL;\s*\}"
)
CLEANUP = re.compile(r"if \(pbuf\) \{\s*pbuf_free\(pbuf\);\s*\}")


class WireguardInputTests(unittest.TestCase):
    def setUp(self):
        self.source = WG.read_text(encoding="utf-8")

    def test_dispatch_uses_configured_callback_and_success_only_transfer(self):
        self.assertIsNotNone(DISPATCH.search(self.source))
        self.assertNotRegex(self.source, r"\bip_input\s*\(")
        self.assertIn("netif->input = tcpip_input;", MGR.read_text(encoding="utf-8"))

    def test_security_gates_still_precede_delivery_and_cleanup_follows(self):
        delivery = DISPATCH.search(self.source)
        self.assertIsNotNone(delivery)
        decrypt = self.source.index("if (decrypt_ok)")
        replay = self.source.index("if (wireguard_check_replay(keypair, nonce))")
        allowed = self.source.index("IP_ADDR_NETCMP_COMPAT(&src_ip,")
        length = self.source.index("if (header_len <= pbuf->tot_len)")
        destination = self.source.index("if (dest_ok)", length)
        self.assertLess(decrypt, replay)
        self.assertLess(replay, allowed)
        self.assertLess(allowed, length)
        self.assertLess(length, destination)
        self.assertLess(destination, delivery.start())
        self.assertIsNotNone(CLEANUP.search(self.source, delivery.end()))

    def test_extracted_c_ownership_harness(self):
        cc = os.environ.get("WG_TEST_CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if not cc:
            self.skipTest("host C compiler unavailable; set WG_TEST_CC")
        dispatch = DISPATCH.search(self.source).group()
        cleanup = CLEANUP.search(self.source, DISPATCH.search(self.source).end()).group()
        harness = r'''
#include <assert.h>
#include <stddef.h>
#define ERR_OK 0
#define ERR_MEM -1
struct pbuf { int unused; };
struct netif { int (*input)(struct pbuf *, struct netif *); };
struct device { struct netif *netif; };
static int calls, frees, result;
static struct pbuf *owned;
static void pbuf_free(struct pbuf *p) { assert(p != NULL); ++frees; }
static int input(struct pbuf *p, struct netif *n) {
    assert(n != NULL); ++calls;
    if (result == ERR_OK) owned = p;
    return result;
}
static void deliver(struct device *device, struct pbuf *pbuf) {
DISPATCH
CLEANUP
}
static void check(int status, int callback, int expected_calls, int expected_frees) {
    struct pbuf packet = {0};
    struct netif netif = {callback ? input : NULL};
    struct device device = {&netif};
    calls = frees = 0; owned = NULL; result = status;
    deliver(&device, &packet);
    assert(calls == expected_calls && frees == expected_frees);
    if (owned) pbuf_free(owned); /* eventual input consumer cleanup */
    assert(frees == 1);
}
int main(void) {
    check(ERR_OK, 1, 1, 0);
    check(ERR_MEM, 1, 1, 1);
    check(ERR_OK, 0, 0, 1);
    return 0;
}
'''.replace("DISPATCH", dispatch).replace("CLEANUP", cleanup)
        with tempfile.TemporaryDirectory(prefix="wg-input-test-") as temp:
            source = Path(temp) / "ownership.c"
            executable = Path(temp) / ("ownership.exe" if os.name == "nt" else "ownership")
            source.write_text(harness, encoding="utf-8")
            subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
