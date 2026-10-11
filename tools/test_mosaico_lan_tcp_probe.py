"""Offline compile-time production parser/worker fault shims; no board or network."""
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
COMPILERS = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
CPP = (ROOT/'main/host/mosaico_lan_tcp_probe.cpp').read_text()
HEADER = (ROOT/'main/host/mosaico_lan_tcp_probe.h').read_text()

class ProbeTests(unittest.TestCase):
    def test_production_fault_shims(self):
        self.assertTrue(COMPILERS, 'Existing local cross compiler required')
        header = '\n'.join(x for x in HEADER.splitlines() if not x.startswith('#include') and x != '#pragma once')
        header = header.replace('std::atomic<bool>', 'Atomic<bool>').replace(' private:', ' public:')
        for declaration in ('bool parse(', 'bool destinationAllowed(', 'bool request(', 'void cancel(', 'Snapshot snapshot(', 'void service(', 'bool quiesce(', 'bool busy(', 'void run(', 'void publish('):
            header = header.replace(declaration, 'constexpr '+declaration)
        header = header.replace(' mutable portMUX_TYPE', ' portMUX_TYPE')
        header = header.replace(' Snapshot _cache;', SHIM+'\n Snapshot _cache;')
        # Const getter becomes non-const only in the test, since mutable is not a constant expression.
        header = header.replace('snapshot() const', 'snapshot()')
        source = '\n'.join(x for x in CPP.splitlines() if not x.startswith('#include'))
        for definition in ('static bool decimal(', 'bool parse(', 'bool destinationAllowed(', 'Snapshot Probe::snapshot(', 'void Probe::publish(', 'bool Probe::request(', 'bool Probe::quiesce(', 'void Probe::service(', 'void Probe::run('):
            source = source.replace(definition, 'constexpr '+definition)
        source = source.replace('snapshot() const', 'snapshot()').replace('std::memcpy', 'copyBytes').replace('std::strncpy', 'copyName')
        # ABI/sizeof is asserted in the unmodified source guard test. Constant evaluation forbids pointer reinterpretation.
        source = source.replace('reinterpret_cast<sockaddr*>(&src)', '&src').replace('reinterpret_cast<sockaddr*>(&dst)', '&dst')
        source = source.replace('errno', 'faultError')
        harness = PRELUDE+header+'\n'+source+'\n'+settings_gate_harness()+'\n'+CASES+'\nint main(){return cases()&&settingsCases()?0:1;}\n'
        evidence = ROOT/'.artifacts/mosaico/lan-tcp-probe-tests'
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence/'production_fault_shim.cpp').write_text(harness)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'probe.cpp'; path.write_text(harness)
            run = subprocess.run([str(COMPILERS[-1]), '-std=c++20', '-fsyntax-only', str(path)], capture_output=True, text=True)
            (evidence/'compiler.log').write_text(run.stdout+run.stderr)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)

    def test_integration_and_binding_boundaries(self):
        quota=(ROOT/'main/host/network_quota.cpp').read_text()
        admission=quota[quota.index('bool NetworkQuota::requestLanTcp'):quota.index('bool NetworkQuota::requestTsfProbe')]
        for guard in ('_hw_rx_owner_ready', 'idleLocked()', 'MosaicoOta::blocksTwt()', '_twt_request.load() == -1', 'eventsClear', '_twt.state.expiryUs', '_wifi_running.load()', '_connected.load()'):
            self.assertIn(guard, admission)
        self.assertNotIn('xTaskNotify', admission)
        cleanup=quota[quota.index('void NetworkQuota::cleanupTwtTrial'):quota.index('void NetworkQuota::serviceTwtTrial')]
        for driver in ('esp_wifi_sta_itwt_teardown', 'esp_wifi_set_ps'):
            self.assertLess(cleanup.index('_lan_tcp.quiesce()'), cleanup.index(driver))
        self.assertIn('cleanupStage = 7', cleanup)
        self.assertIn('cleanupDeadlineUs = 0', cleanup)
        loop=quota[quota.index('void NetworkQuota::run()'):]
        self.assertLess(loop.index('reconcileTwtCycleIdentity();'), loop.index('serviceLanTcp();'))
        self.assertLess(loop.index('serviceLanTcp();'), loop.index('if (_twt.state.cleanupPending'))
        self.assertIn('if (!_lan_tcp.quiesce()) { wait(100); continue; }',loop)
        self.assertIn('struct ifreq iface{}', CPP)
        self.assertIn('SO_BINDTODEVICE,&iface,sizeof(iface)', CPP)
        self.assertIn('src.sin_addr.s_addr=htonl(s.source)', CPP)
        self.assertIn('WIFI_STA_DEF', CPP)
        self.assertEqual(CPP.count('close(fd)'),1)
        self.assertLess(CPP.index('close(fd)'),CPP.rindex('s.active=false;s.finished=true'))
        for forbidden in ('ESP_LOG','nvs_', 'http', 'TaskNotify', 'tailscale', 'microlink'):
            self.assertNotIn(forbidden, CPP)
        settings=quota[quota.index('void NetworkQuota::serviceWifiSettings()'):quota.index('#if SOC_WIFI_HE_SUPPORT',quota.index('void NetworkQuota::serviceWifiSettings()'))]
        self.assertNotIn('wifiSettingsSnapshot(',settings)
        self.assertLess(settings.index('generation=_wifi_settings_generation'),settings.index('_lan_tcp.quiesce()'))
        self.assertLess(settings.index('_lan_tcp.quiesce()'),settings.index('generation!=_wifi_settings_generation'))
        for operation in ('esp_restart()', 'nvs_open('):
            self.assertLess(settings.index('generation!=_wifi_settings_generation'),settings.index(operation))
        self.assertIn('if (!settingsGuard.owns_lock() || _wifi_settings.pending || _wifi_settings.restartPending)',admission)
        self.assertIn('settingsClear && eventsClear',admission)
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
        block=serial[serial.index('std::strcmp(command, "lan-tcp")'):serial.index('std::strcmp(command, "tsf-probe")')]
        self.assertIn('if (extra ||',block)
        self.assertIn('ip != nullptr',block)
        self.assertIn('"QUEUED":"FAIL"',block)
        self.assertIn('"lan-tcp"',(ROOT/'main/debug/serial_debug_transport.h').read_text())

PRELUDE=r'''
#include <cstdint>
#include <cstddef>
#include <cerrno>
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
#define portMUX_INITIALIZER_UNLOCKED 0
using portMUX_TYPE=int;
template<class T> struct Atomic { T value{}; constexpr Atomic(T x):value(x){} constexpr T load()const{return value;} constexpr void store(T x){value=x;} };
constexpr int AF_INET=2,SOCK_STREAM=1,IPPROTO_TCP=6,SOL_SOCKET=1,SO_BINDTODEVICE=0x100b,SO_ERROR=4,F_SETFL=1,O_NONBLOCK=1,ESP_OK=0,pdPASS=1;
using socklen_t=unsigned;struct sockaddr_in {int sin_family=0;uint16_t sin_port=0;struct {uint32_t s_addr=0;} sin_addr;};
struct ifreq {char ifr_name[6]{};};struct fd_set {};struct timeval {long tv_sec,tv_usec;};
#define FD_ZERO(x) ((void)0)
#define FD_SET(fd,x) ((void)0)
constexpr uint32_t ntohl(uint32_t x){return x;} constexpr uint32_t htonl(uint32_t x){return x;}
constexpr uint16_t htons(uint16_t x){return x;} constexpr uint16_t ntohs(uint16_t x){return x;}
struct esp_netif_t {};struct esp_netif_ip_info_t {struct {uint32_t addr=0;} ip,netmask;};
constexpr void copyBytes(char* a,const char* b,size_t n){for(size_t i=0;i<n;++i)a[i]=b[i];}
constexpr void copyName(char* a,const char* b,size_t n){for(size_t i=0;i<n;++i){a[i]=b[i];if(!b[i])return;}}
inline void vTaskDelete(void*) {}
'''
SHIM=r'''
 int socketCalls=0,taskCalls=0,closeCalls=0,selectCalls=0,mode=0,readStep=0,faultError=EINPROGRESS;
 bool createFail=false,bindFail=false,closeSawActive=false,closeFail=false; int64_t now=1000000;
 esp_netif_t sta; uint32_t source=0xc0a80102,mask=0xffffff00;
 constexpr int64_t esp_timer_get_time(){return now;}
 constexpr esp_netif_t* esp_netif_get_handle_from_ifkey(const char*){return &sta;}
 constexpr int esp_netif_get_ip_info(esp_netif_t*,esp_netif_ip_info_t* i){i->ip.addr=source;i->netmask.addr=mask;return 0;}
 constexpr int esp_netif_get_netif_impl_index(esp_netif_t*){return 2;}
 constexpr int esp_netif_get_netif_impl_name(esp_netif_t*,char* s){s[0]='s';s[1]='t';s[2]='1';s[3]=0;return 0;}
 constexpr int xTaskCreate(void(*)(void*),const char*,int,void*,int,void*){++taskCalls;return createFail?0:1;}
 constexpr int socket(int,int,int){++socketCalls;return mode==5?-1:9;}
 constexpr int setsockopt(int,int,int opt,const void*,size_t n){if(opt!=SO_BINDTODEVICE||n!=sizeof(ifreq))return -1;return bindFail?-1:0;}
 constexpr int bind(int,sockaddr_in* s,size_t){return s->sin_addr.s_addr==source?0:-1;}
 constexpr int fcntl(int,int,int){return 0;}
 constexpr int connect(int,sockaddr_in*,size_t){return -1;}
 constexpr int getsockopt(int,int,int,int* error,socklen_t*){*error=mode==3?ECONNREFUSED:0;return 0;}
 constexpr int getsockname(int,sockaddr_in* s,socklen_t*){s->sin_port=33333;return 0;}
 constexpr int select(int,fd_set*,fd_set*,void*,timeval* t){++selectCalls;now+=t->tv_usec;if(mode==1)return 0;if(mode==2){cancel();return 0;}return 1;}
 constexpr int recv(int,uint8_t* b,size_t,int){if(mode==4){faultError=ECONNRESET;return -1;}if(readStep==2)return 0;int n=readStep++==0?17:512;for(int i=0;i<n;++i)b[i]=(_cache.recvBytes+i)%251;if(mode==6)b[3]=255;return n;}
 constexpr int close(int){++closeCalls;closeSawActive=_cache.active&&_cache.fdOpen;if(closeFail){faultError=EIO;return -1;}return 0;}
'''
CASES=r'''
using namespace MosaicoLanTcp;
constexpr bool cases(){
 Request r;
 if(!parse("192.168.1.3","33380","10",r))return false;
 for(const char* ip:{"8.8.8.8","172.32.0.2","256.1.1.1","1.2.3","10.1.1.1x","10.1.1.1.2"})if(parse(ip,"33380","10",r))return false;
 for(const char* port:{"1023","65536","-1","1x","999999999999"})if(parse("10.1.1.1",port,"10",r))return false;
 for(const char* ttl:{"9","61","-1","1x"})if(parse("10.1.1.1","1024",ttl,r))return false;
 if(destinationAllowed(0xc0a80203,0xc0a80102,0xffffff00)||destinationAllowed(0xc0a80102,0xc0a80102,0xffffff00)||destinationAllowed(0xc0a801ff,0xc0a80102,0xffffff00)||destinationAllowed(0xc0a80100,0xc0a80102,0xffffff00))return false;
 r={0xc0a80103,33380,10};
 Probe off;off.service(false,0,0);if(off.socketCalls||off.taskCalls||off.snapshot().active)return false;
 for(int mode=0;mode<=6;++mode){Probe p;p.mode=mode;if(!p.request(r,7,4))return false;if(p.request(r,7,4))return false;p.service(true,7,4);if(!p.snapshot().active||p.taskCalls!=1||p.socketCalls)return false;p.run();auto s=p.snapshot();if(p.closeCalls!=(mode==5?0:1)||s.active||s.fdOpen||!s.finished)return false;if(mode!=5&&!p.closeSawActive)return false;
 if(mode==0&&(s.recvBytes!=529||s.readCalls!=3||s.mismatchCount||!s.connected||!s.bindingSuccess))return false;
 if(mode==1&&(s.error!=ETIMEDOUT||p.now>6000000||p.selectCalls!=50))return false;
 if(mode==2&&(s.error!=ECANCELED||p.selectCalls!=1))return false;
 if(mode==3&&(s.error!=ECONNREFUSED||s.connected))return false;
 if(mode==4&&s.error!=ECONNRESET)return false;
 if(mode==6&&(s.mismatchCount!=2||s.firstMismatchOffset!=3||s.expected!=3||s.got!=255))return false;
 if(!p.request(r,7,4))return false;}
 for(int mode:{0,1,2,4}){Probe p;p.mode=mode;p.closeFail=true;p.request(r,7,4);p.service(true,7,4);p.run();auto s=p.snapshot();
 if(s.active||!s.fdOpen||!s.finished||!s.cleanupDebt||s.closeResult!=-1||s.closeError!=EIO||!p.busy()||p.quiesce()||p.request(r,7,4)||p.closeCalls!=1)return false;
 if(mode==0&&s.error!=0)return false;if(mode==1&&s.error!=ETIMEDOUT)return false;if(mode==2&&s.error!=ECANCELED)return false;if(mode==4&&s.error!=ECONNRESET)return false;
 p.service(false,7,4);if(p.closeCalls!=1||!p.snapshot().cleanupDebt)return false;}
 Probe create;create.createFail=true;create.request(r,7,4);create.service(true,7,4);if(create.snapshot().active||create.snapshot().queued||create.snapshot().error!=ENOMEM||!create.request(r,7,4))return false;
 Probe bind;bind.bindFail=true;bind.request(r,7,4);bind.service(true,7,4);bind.run();if(bind.snapshot().connected||bind.snapshot().bindingSuccess||bind.closeCalls!=1)return false;
 for(uint32_t dest:{0xc0a80203u,0xc0a80102u,0xc0a801ffu,0xc0a80100u}){Probe reject;r.destination=dest;reject.request(r,7,4);reject.service(true,7,4);if(reject.taskCalls||reject.socketCalls||reject.snapshot().queued)return false;}
 r.destination=0xc0a80103;Probe cancelled;cancelled.request(r,7,4);cancelled.cancel();cancelled.service(true,7,4);if(cancelled.taskCalls||cancelled.snapshot().queued||!cancelled.snapshot().finished)return false;
 Probe lost;lost.request(r,7,4);lost.service(true,8,4);if(lost.taskCalls||lost.snapshot().queued)return false;
 Probe hold;hold.request(r,7,4);hold.service(true,7,4);if(hold.quiesce())return false;hold.run();if(!hold.quiesce()||hold.closeCalls)return false;
 return true;
}
static_assert(cases());
'''
# initializer_list supports range literals in the constexpr cases.
PRELUDE = '#include <initializer_list>\n'+PRELUDE

def settings_gate_harness():
    quota=(ROOT/'main/host/network_quota.cpp').read_text()
    method=quota[quota.index('void NetworkQuota::serviceWifiSettings()'):quota.index('#if SOC_WIFI_HE_SUPPORT',quota.index('void NetworkQuota::serviceWifiSettings()'))]
    gate=method[method.index('    bool restart=false, forget=false;'):method.index('    if (restart) {')]
    gate=gate.replace('std::unique_lock<std::mutex> guard(_wifi_settings_mutex,std::try_to_lock);','Lock guard(this);').replace('return;', 'return false;').replace('std::memcpy','copyBytes')
    return r'''
#define CONFIG_IDF_TARGET_ESP32S31 1
struct SettingsGate {
 struct State { bool pending=false,restartPending=false;int error=0;} _wifi_settings;
 uint64_t _wifi_settings_generation=1;bool _pending_forget=false;char _pending_ssid[33]{},_pending_password[65]{};
 int lockCalls=0,failLock=0,closeCalls=0,lockDepth=0;bool closed=true,changeDuringClose=false,insertOnFirstLock=false;
 struct Lock {SettingsGate* p;bool locked;constexpr Lock(SettingsGate* x):p(x),locked(++x->lockCalls!=x->failLock){if(locked)++p->lockDepth;if(p->lockCalls==1&&p->insertOnFirstLock){p->_wifi_settings.pending=true;++p->_wifi_settings_generation;}}constexpr ~Lock(){if(locked)--p->lockDepth;}constexpr bool owns_lock()const{return locked;}};
 struct Lan { SettingsGate* owner;constexpr bool quiesce(){++owner->closeCalls;if(owner->lockDepth)return false;if(owner->changeDuringClose)++owner->_wifi_settings_generation;return owner->closed;} } _lan_tcp{this};
 constexpr bool gate(){char ssid[33]{},password[65]{};
''' + gate + r'''
 return true;}
};
constexpr bool settingsCases(){
 for(bool restart:{false,true}){
 SettingsGate active;active._wifi_settings.pending=!restart;active._wifi_settings.restartPending=restart;active.closed=false;if(active.gate()||active.closeCalls!=1||active.lockCalls!=1)return false;
 SettingsGate debt;debt._wifi_settings.pending=!restart;debt._wifi_settings.restartPending=restart;debt.closed=false;if(debt.gate())return false;
 SettingsGate first;first._wifi_settings.pending=!restart;first._wifi_settings.restartPending=restart;first.failLock=1;if(first.gate()||first.closeCalls)return false;
 SettingsGate second;second._wifi_settings.pending=!restart;second._wifi_settings.restartPending=restart;second.failLock=2;if(second.gate()||second.closeCalls!=1)return false;
 SettingsGate changed;changed._wifi_settings.pending=!restart;changed._wifi_settings.restartPending=restart;changed.changeDuringClose=true;if(changed.gate())return false;
 SettingsGate safe;safe._wifi_settings.pending=!restart;safe._wifi_settings.restartPending=restart;if(!safe.gate()||safe.closeCalls!=1||safe.lockCalls!=2)return false;
 }
 SettingsGate arrived;arrived.insertOnFirstLock=true;arrived.closed=false;if(arrived.gate()||arrived.closeCalls!=1)return false;
 SettingsGate idle;if(idle.gate()||idle.closeCalls)return false;
 return true;
}
static_assert(settingsCases(),"locked settings decision and queue identity must surround socket-close gate");
'''
if __name__ == '__main__': unittest.main()
