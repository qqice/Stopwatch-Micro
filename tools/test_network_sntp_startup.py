"""R54 owner-IP-ordered SNTP init/start; actual local production blocks on RV32.
Mock SDK/time/owner iteration; no NTP, scheduler, Wi-Fi or hardware claim.
"""
import hashlib
from pathlib import Path
import unittest
import test_microlink_peer_probe as rv

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'main/host/network_quota.cpp'
RUN = ROOT / 'artifacts/network-sntp-startup-tests'
CONFIG_LINE = '    timeConfig.start = false; // Start only after the owner observes a usable IPv4 address.\n'
ONCE_DECL = '#ifdef MOSAICO_BOARD\n    bool sntpStarted = false;\n#endif\n'
START_BLOCK = '''#ifdef MOSAICO_BOARD
        if (timeInit == ESP_OK && !sntpStarted) {
            if (esp_netif_sntp_start() == ESP_OK) sntpStarted = true;
        }
#endif
'''
PREFIX = r'''
#define ESP_OK 0
#define ESP_LOGW(...) ((void)0)
#define nullptr 0
typedef int esp_err_t;
struct timeval{long tv_sec;};
struct esp_sntp_config_t{bool start;void(*sync_cb)(timeval*);const char* server;};
#define ESP_NETIF_SNTP_DEFAULT_CONFIG(server) {true,nullptr,server}
static long epoch;
namespace std{static long time(void*){return epoch;}}
static int init_result,start_result,inits,starts,marks,notifications,forwarded;
static bool init_auto_start;
static esp_err_t esp_netif_sntp_init(esp_sntp_config_t*c){++inits;init_auto_start=c->start;return init_result;}
static esp_err_t esp_netif_sntp_start(){++starts;return start_result;}
namespace MosaicoClock{static void onSntpTime(timeval*){++forwarded;}}
struct Quota{void noteStartup(int){++marks;}void notifyStartupProgress(){++notifications;}};
static Quota quota;
static Quota& GetNetworkQuota(){return quota;}
static void noteStartup(int stage){quota.noteStartup(stage);}
'''
MOSAICO_CASES = r'''
    if (inits!=1||init_auto_start||sntpStarted||starts)return 1;
    auto iteration = [&](bool idle,bool ipv4) {
        // Model admission only; source tests bind the real idle/no-IP continues.
        if (idle||!ipv4)return;
START
    };
    switch(id){
    case 0:iteration(false,false);iteration(true,true);return !starts&&!sntpStarted?0:2;
    case 1:iteration(false,true);iteration(false,true);return starts==1&&sntpStarted?0:3;
    case 2:iteration(false,true);return !starts&&!sntpStarted?0:4;
    case 3:iteration(false,true);iteration(false,true);if(starts!=2||sntpStarted)return 5;start_result=ESP_OK;iteration(false,true);iteration(false,true);return starts==3&&sntpStarted?0:6;
    case 4:if(marks!=1)return 7;iteration(false,true);return starts==1&&sntpStarted?0:8; // Warm clock still starts synchronization.
    case 5:iteration(false,true);iteration(true,false);iteration(false,false);iteration(false,true);return starts==1&&sntpStarted?0:9; // Radio off/resume does not reset once state.
    case 6:iteration(false,false);iteration(false,true);return starts==1&&sntpStarted?0:10;
    case 7:{timeval tv{1735689600};timeConfig.sync_cb(&tv);return forwarded==1&&marks==1&&notifications==1&&!starts?0:11;}
    case 8:{timeval tv{1735689599};timeConfig.sync_cb(&tv);timeConfig.sync_cb(nullptr);return forwarded==2&&!marks&&!notifications?0:12;}
    }
    return 99;
}
'''


def fixture(s, mosaico=True):
    configuration = s[s.index('    esp_sntp_config_t timeConfig ='):s.index('    bool hadConnection = false;')]
    once_start = rv.function(s, '        if (timeInit == ESP_OK && !sntpStarted)')
    setup = r'''
static int run_case(int id){
 init_result=id==2?-1:ESP_OK;start_result=id==3?-1:ESP_OK;inits=starts=marks=notifications=forwarded=0;epoch=id==4?1735689601:0;init_auto_start=true;
'''
    cases = MOSAICO_CASES.replace('START\n', once_start) if mosaico else 'return inits==1&&init_auto_start&&!starts?0:1;\n}\n'
    count = 9 if mosaico else 1
    return (('#define MOSAICO_BOARD 1\n' if mosaico else '') + PREFIX + setup + configuration + cases + f'''
extern "C" {{volatile int test_results[{count}]={{}};}}
extern "C" __attribute__((noinline,used)) void test_done(){{for(;;){{asm volatile("");}}}}
extern "C" void _start(){{for(int i=0;i<{count};++i)test_results[i]=run_case(i);test_done();}}
''')


class SntpStartupTests(unittest.TestCase):
    def execute(self, mosaico):
        oldf, oldr = rv.fixture, rv.RUN
        try:
            rv.fixture = lambda s: fixture(s, mosaico)
            rv.RUN = RUN
            label = 'mosaico' if mosaico else 'non-mosaico'
            result = rv.execute(SOURCE.read_text(encoding='utf8'), label, 9 if mosaico else 1)
            log = RUN / (label + '-execution.log')
            log.write_text('Actual SNTP configuration and owner once-start block. '
                           'Mock SDK/time/IP/idle admissions; no NTP/network/scheduler claim. Source SHA256=' +
                           log.read_text(encoding='utf8').split('Source SHA256=', 1)[1], encoding='utf8')
            return result
        finally:
            rv.fixture, rv.RUN = oldf, oldr

    def test_actual_mosaico_config_and_start(self):
        self.assertEqual(self.execute(True), (0,) * 9)

    def test_non_mosaico_keeps_default_autostart(self):
        self.assertEqual(self.execute(False), (0,))

    def test_source_order_and_unchanged_owner_gates(self):
        s = SOURCE.read_text(encoding='utf8')
        self.assertIn('#ifdef MOSAICO_BOARD\n' + CONFIG_LINE, s)
        self.assertIn(ONCE_DECL, s)
        self.assertIn(START_BLOCK, s)
        self.assertEqual(s.count('esp_netif_sntp_start()'), 1)
        self.assertEqual(s.count('sntpStarted = false'), 1)
        self.assertEqual(s.count('sntpStarted = true'), 1)
        self.assertNotIn('esp_netif_sntp_stop(', s)
        config = s.index('timeConfig.start = false;')
        init = s.index('const esp_err_t timeInit = esp_netif_sntp_init(&timeConfig);')
        declaration = s.index(ONCE_DECL)
        self.assertLess(config, init)
        self.assertLess(init, declaration)
        self.assertLess(declaration, s.index('while (true)', declaration))
        no_ip = rv.function(s, '        if (!_connected) {')
        self.assertIn('esp_wifi_connect();\n            wait(5000);\n            continue;', no_ip)
        self.assertNotIn('esp_netif_sntp_start', no_ip)
        connected = s.index('_connected = esp_wifi_sta_get_ap_info(&ap)')
        valid_ip = s.index('ip.ip.addr != 0;', connected)
        start = s.index(START_BLOCK)
        clock_gate = s.index('if (std::time(nullptr) < 1735689600)', start)
        self.assertLess(valid_ip, s.index(no_ip))
        self.assertLess(s.index(no_ip) + len(no_ip), start)
        self.assertLess(start, clock_gate)
        self.assertIn('wait(5000);\n                continue;', rv.function(s, '            if (std::time(nullptr) < 1735689600)'))
        # Exact inverse of the three authorized insertions recovers R53 HEAD
        # 24b36cb. This binds unchanged idle path, TLS clock gate, callbacks,
        # five-second fallback, OTA/window/gauge behavior and server selection.
        before = s.replace(CONFIG_LINE, '', 1).replace(ONCE_DECL, '', 1).replace(START_BLOCK, '', 1)
        self.assertEqual(hashlib.sha256(before.encode()).hexdigest(),
                         '8be07ca2585d1da4c0a9e64d1559ee806aa289fd7bbbcdc8f09024c8ce70d1af')


if __name__ == '__main__':
    unittest.main()
