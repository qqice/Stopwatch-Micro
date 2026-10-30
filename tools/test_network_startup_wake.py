"""R53 startup-only owner wake: extracted helpers/callbacks on RV32; no device IO."""
import hashlib
from pathlib import Path
import unittest
import test_microlink_peer_probe as rv

ROOT = Path(__file__).resolve().parents[1]
NETWORK = ROOT / 'main/host/network_quota.cpp'
HEADER = ROOT / 'main/host/network_quota.h'
TRANSPORT = ROOT / 'main/host/tailscale_transport.cpp'
RUN = ROOT / 'artifacts/network-startup-wake-tests'

PREFIX = r'''
namespace std {enum memory_order{memory_order_acquire};}
template<class T>struct Atomic{T value{};T load()const{return value;}T load(std::memory_order)const{return value;}};
static int notifications, notified_handle, marks, last_stage, forwarded, sequence, mark_order, forward_order, notify_order;
static void xTaskNotifyGive(int h){++notifications;notified_handle=h;notify_order=++sequence;}
class NetworkQuota {public:
 Atomic<unsigned> _accepted;Atomic<bool> _ota_owner_ready;int _task_handle=0;
 bool force_refresh=false,locked=true,configured=true;unsigned window=123,interval=300000;
 void notifyStartupProgress();void notifyOtaQueued();
 void noteStartup(unsigned stage){++marks;last_stage=stage;mark_order=++sequence;}
};
static NetworkQuota quota;
static NetworkQuota& GetNetworkQuota(){return quota;}
struct timeval{long tv_sec;};
namespace MosaicoClock {static void onSntpTime(timeval*){++forwarded;forward_order=++sequence;}}
struct microlink_t{};
enum microlink_state_t{ML_STATE_IDLE,ML_STATE_CONNECTING,ML_STATE_CONNECTED,ML_STATE_DISCONNECTED};
'''
CASES = r'''
static int run_case(int id){quota={};quota._ota_owner_ready.value=true;quota._task_handle=42;notifications=notified_handle=marks=last_stage=forwarded=sequence=mark_order=forward_order=notify_order=0;
 switch(id){
 case 0:quota.notifyStartupProgress();if(notifications!=1||notified_handle!=42)return 1;break;
 case 1:quota._accepted.value=1;quota.notifyStartupProgress();if(notifications)return 2;break;
 case 2:quota._accepted.value=0xffffffff;quota.notifyStartupProgress();if(notifications)return 3;break;
 case 3:quota._ota_owner_ready.value=false;quota.notifyStartupProgress();if(notifications)return 4;break;
 case 4:quota._task_handle=0;quota.notifyStartupProgress();if(notifications)return 5;break;
 case 5:gotip(&quota,nullptr,0,nullptr);if(notifications!=1||marks!=1||last_stage!=4||mark_order>=notify_order)return 6;break;
 case 6:{timeval tv{1735689600};sntp(&tv);if(forwarded!=1||marks!=1||last_stage!=5||notifications!=1||forward_order>=mark_order||mark_order>=notify_order)return 7;break;}
 case 7:{timeval tv{1735689599};sntp(&tv);sntp(nullptr);if(forwarded!=2||marks||notifications)return 8;break;}
 case 8:statecb(nullptr,ML_STATE_CONNECTED,nullptr);if(notifications!=1||marks)return 9;break;
 case 9:for(int i=0;i<4;++i)if(i!=ML_STATE_CONNECTED)statecb(nullptr,(microlink_state_t)i,nullptr);if(notifications)return 10;break;
 case 10:quota._accepted.value=1;gotip(&quota,nullptr,0,nullptr);{timeval tv{1735689601};sntp(&tv);}statecb(nullptr,ML_STATE_CONNECTED,nullptr);if(notifications||marks!=2||forwarded!=1)return 11;break;
 case 11:quota._ota_owner_ready.value=false;gotip(&quota,nullptr,0,nullptr);statecb(nullptr,ML_STATE_CONNECTED,nullptr);if(notifications)return 12;break;
 case 12:quota._task_handle=0;{timeval tv{1735689601};sntp(&tv);}if(notifications||forwarded!=1||marks!=1)return 13;break;
 case 13:quota.notifyStartupProgress();quota._accepted.value=1;quota.notifyStartupProgress();statecb(nullptr,ML_STATE_CONNECTED,nullptr);if(notifications!=1)return 14;break;
 }
 return !quota.force_refresh&&quota.locked&&quota.configured&&quota.window==123&&quota.interval==300000?0:90;
}
extern "C" {volatile int test_results[14]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<14;++i)test_results[i]=run_case(i);test_done();}
'''


def fixture(s):
    transport = TRANSPORT.read_text(encoding='utf8')
    return (PREFIX + rv.function(s, 'void NetworkQuota::notifyStartupProgress(') +
            rv.function(s, 'void NetworkQuota::notifyOtaQueued(') +
            'static auto gotip = ' + rv.function(s, '        [](void* arg,const char*,int32_t,void*)').replace('int32_t', 'int') + ';\n' +
            'static auto sntp = ' + rv.function(s, '[](timeval* tv)') + ';\n' +
            'static auto statecb = ' + rv.function(transport, '[](microlink_t*, microlink_state_t state, void*)') + ';\n' + CASES)


class StartupWakeTests(unittest.TestCase):
    def test_actual_helpers_and_callbacks(self):
        s = NETWORK.read_text(encoding='utf8')
        oldf, oldr = rv.fixture, rv.RUN
        try:
            rv.fixture, rv.RUN = fixture, RUN
            self.assertEqual(rv.execute(s, 'startup-wake', 14), (0,) * 14)
            log = RUN / 'startup-wake-execution.log'
            log.write_text('Actual R53 startup/OTA wake helpers and GOT_IP/SNTP/CONNECTED lambdas. '
                           'Mock atomics, task notify, clock forwarding and timing marks; no scheduler/RF/IO claim.\nSource SHA256=' +
                           log.read_text(encoding='utf8').split('Source SHA256=', 1)[1], encoding='utf8')
        finally:
            rv.fixture, rv.RUN = oldf, oldr

    def test_source_binding_and_install_order(self):
        s, h, t = (p.read_text(encoding='utf8') for p in (NETWORK, HEADER, TRANSPORT))
        self.assertIn('void notifyStartupProgress();', h.split('#ifdef MOSAICO_BOARD', 2)[2])
        body = rv.function(s, 'void NetworkQuota::notifyStartupProgress(')
        self.assertEqual(body.split('{', 1)[1].strip(), 'if (_accepted.load() == 0) notifyOtaQueued();\n}')
        got = s.split('esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,', 1)[1].split('#else', 1)[0]
        self.assertIn('quota->noteStartup(4);\n            quota->notifyStartupProgress();', got)
        sntp = rv.function(s, '[](timeval* tv)')
        self.assertLess(sntp.index('MosaicoClock::onSntpTime(tv);'), sntp.index('if (tv && tv->tv_sec>=1735689600)'))
        self.assertIn('noteStartup(5);\n            GetNetworkQuota().notifyStartupProgress();', sntp)
        self.assertIn('if (std::time(nullptr)>=1735689600) noteStartup(5);', s)
        self.assertIn('#ifdef MOSAICO_BOARD\n#include "network_quota.h"', t)
        start = rv.function(t, 'void TailnetQuota::start(')
        init = start.index('microlink_init(&config)')
        callback = start.index('microlink_set_state_callback(')
        self.assertLess(init, start.index('if (!_client)'))
        self.assertLess(start.index('if (!_client)'), callback)
        self.assertLess(callback, start.index('microlink_start(_client)', callback))
        self.assertNotIn('microlink_set_state_callback', start[:init])  # resume keeps callback
        self.assertIn('#ifdef MOSAICO_BOARD\n    microlink_set_state_callback', start)
        cb = rv.function(t, '[](microlink_t*, microlink_state_t state, void*)')
        self.assertEqual(cb.split('{', 1)[1].strip(), 'if (state == ML_STATE_CONNECTED) GetNetworkQuota().notifyStartupProgress();\n        }')
        self.assertEqual(s.count('notifyStartupProgress();'), 2)
        self.assertEqual(t.count('notifyStartupProgress();'), 1)

    def test_r52_authoritative_owner_wait_and_ota_logic_unchanged(self):
        # Frozen R52 HEAD 35656c2 digests: no timeout/window/idle/OTA owner changes.
        s, h = NETWORK.read_text(encoding='utf8'), HEADER.read_text(encoding='utf8')
        # R54's reviewed once-start block is separately exercised by
        # test_network_sntp_startup. Strip exactly it, never bless a new whole-body SHA.
        sntp_once = ("#ifdef MOSAICO_BOARD\n"
                     "        if (timeInit == ESP_OK && !sntpStarted) {\n"
                     "            if (esp_netif_sntp_start() == ESP_OK) sntpStarted = true;\n"
                     "        }\n#endif\n")
        self.assertEqual(s.count(sntp_once), 1)
        owner_baseline = s.replace(sntp_once, "", 1)
        bodies = (
            (rv.function(s, 'void NetworkQuota::wait('), 'eff5d22c8cf0b42ee53428af984db4f4f1f1d3dda4e590f010102a4481ebd507'),
            (rv.function(s, 'void NetworkQuota::notifyOtaQueued('), 'b02dd4426c536261aeba08439ed4e83885193fbfc54288ad43b17e7f8a2b9420'),
            (rv.function(h, '    bool idleLocked('), '70e319ebe61a7b215afb25913f95cd7dc3a8c2efaf3eca5fbc0857e1e1fcd0f4'),
            (owner_baseline.split('    bool hadConnection = false;', 1)[1], '4d6cf0301fa6c5503e27c3959e71353980b87c0078d5114109398989e6351a17'),
        )
        for body, expected in bodies:
            self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), expected)
        self.assertIn('esp_wifi_connect();\n            wait(5000);', s)
        self.assertIn('if (GetTailnetQuota().enabled() && !GetTailnetQuota().ready()) {\n            wait(5000);', s)


if __name__ == '__main__':
    unittest.main()
