"""Execute production SNTP config/owner lambdas on RV32 with mock SDK/time.
No NTP, Wi-Fi, scheduler, callback concurrency or hardware claim.
"""
import hashlib
from pathlib import Path
import unittest
import test_microlink_peer_probe as rv

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'main/host/network_quota.cpp'
RUN = ROOT / 'artifacts/network-sntp-startup-tests'
PREFIX = r'''
#define ESP_OK 0
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
typedef int esp_err_t;
typedef long long int64_t;
struct timeval{long tv_sec;};
struct esp_sntp_config_t{bool start;void(*sync_cb)(timeval*);const char* server;};
#define ESP_NETIF_SNTP_DEFAULT_CONFIG(server) {true,nullptr,server}
static long epoch;static int64_t now;
namespace std{static long time(void*){return epoch;}}
static int init_result,start_result,inits,starts,stops,marks,notifications,forwarded;
static int pending_stops,serializations,stop_result;static bool init_auto_start,client_active;
static int64_t esp_timer_get_time(){return now;}
static esp_err_t esp_netif_sntp_init(esp_sntp_config_t*c){++inits;init_auto_start=c->start;return init_result;}
// Model CORE_LOCKING: start executes directly; queued async stops are NOT drained.
static void esp_sntp_stop(){++pending_stops;}
static void sntp_stop(){++stops;client_active=false;}
static esp_err_t esp_netif_tcpip_exec(esp_err_t(*fn)(void*),void*ctx){
 if(stop_result)return stop_result;
 ++serializations;return fn(ctx);
}
static void drain_async_stops(){if(pending_stops){client_active=false;pending_stops=0;}}
static esp_err_t esp_netif_sntp_start(){++starts;client_active=start_result==ESP_OK;return start_result;}
namespace MosaicoClock{static void onSntpTime(timeval*){++forwarded;}}
struct Quota{void noteStartup(int){++marks;}void notifyStartupProgress(){++notifications;}};
static Quota quota;
static Quota& GetNetworkQuota(){return quota;}
static void noteStartup(int stage){quota.noteStartup(stage);}
'''
CASES = r'''
    if (inits!=1||init_auto_start||sntpStarted||starts)return 1;
    auto online = [&](int64_t seconds){now=seconds*1000000;serviceSntp();};
    switch(id){
    case 0: // Invalid cold clock: attempts at 0,30,60; offline stop, reopen immediately.
        online(0);online(29);if(starts!=1)return 2;
        online(30);online(60);if(starts!=3||stops!=0||serializations!=0)return 3;
        stopInvalidClockSntp();if(sntpStarted||stops!=1)return 4;
        online(300);return starts==4&&serializations==1?0:5;
    case 1: // Short windows must not wait out the previous retry deadline.
        online(1);stopInvalidClockSntp();online(11);
        return starts==2&&stops==1&&serializations==1?0:6;
    case 2:online(0);online(30);return !starts&&!stops?0:7; // Init failure.
    case 3: // Failed start is rate limited, including owner wake notifications.
        online(0);online(0);online(1);online(29);if(starts!=1)return 8;
        online(30);if(starts!=2)return 9;
        start_result=ESP_OK;online(31);if(starts!=2)return 10;
        online(60);return starts==3&&sntpStarted&&!stops?0:11;
    case 4: // Warm clock still starts SDK periodic sync exactly once across windows.
        if(marks!=1)return 12;
        online(0);stopInvalidClockSntp();online(300);online(600);
        return starts==1&&sntpStarted&&!stops?0:13;
    case 5: // Observed disconnect is idempotent; restoration is immediate.
        online(0);stopInvalidClockSntp();stopInvalidClockSntp();online(5);drain_async_stops();
        return starts==2&&stops==1&&serializations==1&&client_active?0:14;
    case 6: // Clock becomes valid: leave normal SDK periodic sync alone.
        online(0);epoch=1735689600;online(30);stopInvalidClockSntp();online(300);
        return starts==1&&!stops&&sntpStarted?0:15;
    case 7:{timeval tv{1735689600};timeConfig.sync_cb(&tv);
        return forwarded==1&&marks==1&&notifications==1&&!starts?0:16;}
    case 8:{timeval tv{1735689599};timeConfig.sync_cb(&tv);timeConfig.sync_cb(nullptr);
        return forwarded==2&&!marks&&!notifications?0:17;}
    case 9: // Restart failure consumes retry interval, not every owner iteration.
        online(0);start_result=-1;online(30);online(31);online(59);
        if(starts!=2||stops!=0||sntpStarted)return 18;
        start_result=ESP_OK;online(60);return starts==3&&sntpStarted?0:19;
    case 10: // Failed startup then link loss/restoration admits a fresh start.
        start_result=-1;online(0);stopInvalidClockSntp();start_result=0;online(5);
        return starts==2&&sntpStarted&&!stops?0:20;
    case 11: // Stop failure is surfaced, no offline busy retry; next start recovers.
        online(0);stop_result=-1;stopInvalidClockSntp();stopInvalidClockSntp();
        if(sntpStarted||stops||serializations)return 21;
        online(5);drain_async_stops();return starts==2&&client_active?0:22;
    }
    return 99;
}
'''


def fixture(s, mosaico=True):
    configuration = s[s.index('    esp_sntp_config_t timeConfig ='):s.index('    bool hadConnection = false;')]
    setup = r'''
static int run_case(int id){
 init_result=id==2?-1:ESP_OK;start_result=id==3?-1:ESP_OK;
 inits=starts=stops=marks=notifications=forwarded=pending_stops=serializations=0;
 now=0;stop_result=0;client_active=false;epoch=id==4?1735689601:0;init_auto_start=true;
'''
    count = 12 if mosaico else 1
    cases = CASES if mosaico else 'return inits==1&&init_auto_start&&!starts?0:1;\n}\n'
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
            count = 12 if mosaico else 1
            result = rv.execute(SOURCE.read_text(encoding='utf8'), label, count)
            log = RUN / (label + '-execution.log')
            log.write_text('Actual SNTP configuration and owner lifecycle lambdas. '
                           'Mock SDK/time; no NTP/network/scheduler/concurrency claim. Source SHA256=' +
                           log.read_text(encoding='utf8').split('Source SHA256=', 1)[1], encoding='utf8')
            return result
        finally:
            rv.fixture, rv.RUN = oldf, oldr

    def test_actual_mosaico_config_and_lifecycle(self):
        self.assertEqual(self.execute(True), (0,) * 12)

    def test_non_mosaico_keeps_default_autostart(self):
        self.assertEqual(self.execute(False), (0,))

    def test_source_order_and_unchanged_owner_gates(self):
        s = SOURCE.read_text(encoding='utf8')
        self.assertIn('ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org")', s)
        self.assertIn('timeConfig.start = false;', s)
        self.assertEqual(s.count('esp_netif_sntp_start()'), 1)
        self.assertNotIn('esp_sntp_stop(', s)
        stop = rv.function(s, '    auto stopInvalidClockSntp =')
        self.assertIn('esp_netif_tcpip_exec([](void*) -> esp_err_t {\n                sntp_stop();\n                return ESP_OK;\n            }, nullptr)', stop)
        self.assertIn('SNTP suspend failed: %s', stop)
        self.assertIn('attemptUs + 30LL * 1000000', s)
        self.assertIn('constexpr int64_t RefreshIntervalUs = 300LL * 1000000;', s)
        self.assertIn('constexpr int64_t UpdateWindowUs    = 90LL * 1000000;', s)
        self.assertLess(s.index('timeConfig.start = false;'), s.index('esp_netif_sntp_init(&timeConfig)'))
        no_ip = rv.function(s, '        if (!_connected) {')
        self.assertLess(no_ip.index('stopInvalidClockSntp();'), no_ip.index('hadConnection = false;'))
        self.assertIn('esp_wifi_connect();\n            wait(5000);\n            continue;', no_ip)
        self.assertNotIn('serviceSntp();', no_ip)
        connected = s.index('_connected = esp_wifi_sta_get_ap_info(&ap)')
        valid_ip = s.index('ip.ip.addr != 0;', connected)
        start = s.index('        serviceSntp();', valid_ip)
        self.assertLess(valid_ip, s.index(no_ip))
        self.assertLess(s.index(no_ip) + len(no_ip), start)
        clock_gate = s.index('if (std::time(nullptr) < 1735689600)', start)
        self.assertLess(start, clock_gate)
        self.assertIn('wait(5000);\n                continue;', rv.function(s, '            if (std::time(nullptr) < 1735689600)'))
        pause = s.index('                stopInvalidClockSntp();')
        self.assertLess(pause, s.index('                esp_wifi_disconnect();', pause))
        self.assertLess(pause, s.index('esp_wifi_stop()', pause))
        # Exact inverse binds every unrelated owner/window/firstQuota/TLS path to R56.
        before = s.replace('#ifdef MOSAICO_BOARD\n#include <esp_sntp.h>\n#undef SNTP_OPMODE_POLL\n#include <lwip/apps/sntp.h>\n#endif\n', '', 1)
        begin = before.index('    int64_t sntpNextAttemptUs = 0;')
        end = before.index('#endif\n    bool hadConnection = false;', begin)
        before = before[:begin] + before[end:]
        before = before.replace('#ifdef MOSAICO_BOARD\n                stopInvalidClockSntp();\n#endif\n', '', 1)
        before = before.replace('#ifdef MOSAICO_BOARD\n            stopInvalidClockSntp();\n#endif\n', '', 1)
        before = before.replace('        serviceSntp();', '''        if (timeInit == ESP_OK && !sntpStarted) {
            if (esp_netif_sntp_start() == ESP_OK) sntpStarted = true;
        }''', 1)
        self.assertEqual(hashlib.sha256(before.encode()).hexdigest(),
                         '99c178d372f720442770cddd3d5a8be815f24a298458283e314f2ae91c572565')


if __name__ == '__main__':
    unittest.main()
