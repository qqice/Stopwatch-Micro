"""Compile-time fault traces of production owner/setter/cleanup; no hardware claim."""
from pathlib import Path
import unittest
import subprocess
import tempfile
from tools import test_mosaico_twt
ROOT=Path(__file__).resolve().parents[1]
class Keepalive(unittest.TestCase):
    def test_extracted_owner_and_cleanup(self):
        src=(ROOT/'main/host/network_quota.cpp').read_text()
        setter=src[src.index('bool NetworkQuota::requestTwtKeepalive'):src.index('MosaicoTwtKeepalive::State NetworkQuota::twtKeepaliveSnapshot')]
        cleanup=src[src.index('void NetworkQuota::cancelTwtTrial'):src.index('void NetworkQuota::serviceTwtTrial')]
        publish=src[src.index('void NetworkQuota::publishTwt()'):src.index('void NetworkQuota::twtEvent')]
        body=(setter+cleanup+publish).replace('bool NetworkQuota::','constexpr bool Owner::').replace('void NetworkQuota::','constexpr void Owner::')
        guard=src.split('    struct KeepaliveOwnerGuard {',1)[1].split('    reconcileTwtCycleIdentity();',1)[0]
        guard=('    struct KeepaliveOwnerGuard {'+guard).replace('explicit KeepaliveOwnerGuard(', 'constexpr explicit KeepaliveOwnerGuard(').replace('~KeepaliveOwnerGuard()', 'constexpr ~KeepaliveOwnerGuard()').replace('portENTER_CRITICAL(', 'owner.portENTER_CRITICAL(').replace('portEXIT_CRITICAL(', 'owner.portEXIT_CRITICAL(')
        code=r'''
#include "main/host/mosaico_twt_model.h"
#include "main/host/mosaico_twt_keepalive.h"
namespace std {
struct mutex { bool available=true; }; struct Tag{}; constexpr Tag try_to_lock{};
template<class T>struct unique_lock { bool held;constexpr unique_lock(T& m,Tag):held(m.available){} constexpr bool owns_lock(){return held;} };
}
using esp_err_t=int;using wifi_ps_type_t=int;
constexpr int ESP_OK=0,FLOW_ID_ALL=8,ESP_ERR_INVALID_STATE=-2;
struct wifi_twt_config_t {bool post_wakeup_event=false,twt_enable_keep_alive=false;};
struct Slot {int value=-1;constexpr int load(){return value;}};
struct Owner;using NetworkQuota=Owner;
struct Owner {
 MosaicoTwt::Model _twt; MosaicoTwtKeepalive::State _twt_keepalive,_twt_keepalive_cache;
 bool _task_handle=true,locked=true,ota=false,otaAtEntry=false,_twt_keepalive_busy=false,_twt_keepalive_owner_processing=false;
 int _twt_mux=0; Slot _twt_request;std::mutex _wifi_settings_mutex;
 struct Settings{bool pending=false,restartPending=false;} _wifi_settings;
 constexpr bool idleLocked(){return locked;} constexpr bool twtOtaBlocked(){return ota;}
 constexpr void portENTER_CRITICAL(int*){if(otaAtEntry)ota=true;}constexpr void portEXIT_CRITICAL(int*){}
 bool _twt_submitted=false,_twt_ps_saved=false,_twt_negotiation_pending=false;
 bool _twt_cleanup_failed=false,_twt_teardown_sent=false,_twt_teardown_ack=false,_twt_cleanup_barrier_needed=false;
 int _twt_saved_ps=2;unsigned _twt_cleanup_attempts=0;long long now=1;
 int configs=0,configError=0,configAction=0,psRestores=0,teardowns=0;bool lastKeepalive=false,lastWake=false;
 constexpr int64_t esp_timer_get_time(){return now;}
 constexpr int esp_wifi_sta_twt_config(wifi_twt_config_t* c){
  ++configs;lastKeepalive=c->twt_enable_keep_alive;lastWake=c->post_wakeup_event;
  if(configAction==1)ota=true;if(configAction==2)_twt_request.value=0;
  return c->twt_enable_keep_alive ? configError : (configAction==3 ? configError : 0);
 }
 constexpr int esp_wifi_set_ps(int){++psRestores;return 0;}
 constexpr int esp_wifi_sta_itwt_teardown(int){++teardowns;return 0;}
 constexpr int esp_wifi_sta_itwt_get_flow_id_status(int* p){*p=0;return 0;}
 MosaicoTwt::Snapshot _twt_cache;
 constexpr void publishTwt();
 constexpr bool requestTwtKeepalive(bool);constexpr bool serviceTwtKeepaliveConfig();
 constexpr void cancelTwtTrial(MosaicoTwt::Stop);constexpr void cleanupTwtTrial();
 constexpr bool publicationWindow(){
'''+guard+r'''
  cancelTwtTrial(MosaicoTwt::Stop::Explicit); // Real production cancel -> publish admission window.
  return _twt_keepalive_owner_processing && _twt_keepalive_busy && !requestTwtKeepalive(true);
 }
 constexpr void arm(){_twt.start(MosaicoTwt::Mode::On,now);_twt_keepalive_busy=true;_twt_keepalive.beginTrial(true);}
};
'''+body+r'''
constexpr bool traces(){
 Owner window;if(!window.publicationWindow()||window._twt_keepalive_busy||window._twt_keepalive_owner_processing||!window.requestTwtKeepalive(false))return false;
 Owner d;d.arm();if(!d.serviceTwtKeepaliveConfig()||d.configs)return false;d.cancelTwtTrial(MosaicoTwt::Stop::Explicit);if(d.configs)return false;
 Owner b;b.requestTwtKeepalive(true);b._twt_keepalive.beginTrial(false);if(!b.serviceTwtKeepaliveConfig()||b.configs)return false;
 for(int gate=0;gate<7;++gate){Owner g;
  if(gate==0)g.ota=true;if(gate==1)g.otaAtEntry=true;if(gate==2)g.locked=false;
  if(gate==3)g._wifi_settings.pending=true;if(gate==4)g._wifi_settings.restartPending=true;
  if(gate==5)g._twt_keepalive_busy=true;if(gate==6)g._twt_request.value=0;
  if(g.requestTwtKeepalive(true)||g._twt_keepalive.requested!=-1||g.configs)return false;
 }
 Owner o;if(!o.requestTwtKeepalive(true)||o.configs)return false;o.arm();if(o.requestTwtKeepalive(false))return false;
 if(!o.serviceTwtKeepaliveConfig()||o.configs!=1||!o.lastKeepalive||o.lastWake||!o._twt_keepalive.applied)return false;
 o.cancelTwtTrial(MosaicoTwt::Stop::Expired);
 if(o.configs!=2||o.lastKeepalive||o.lastWake||o._twt_keepalive.applied||!o._twt_keepalive.restoredKnownBaseline||!o._twt_keepalive.needsRestartToRestoreDefaults)return false;
 Owner off;off.requestTwtKeepalive(false);off.arm();if(!off.serviceTwtKeepaliveConfig()||off.configs!=1||off.lastKeepalive)return false;
 Owner q;q.requestTwtKeepalive(true);q.arm();q._twt_request.value=0;if(q.serviceTwtKeepaliveConfig()||q.configs)return false;
 for(int action=1;action<=2;++action){Owner r;r.requestTwtKeepalive(true);r.arm();r.configAction=action;
  if(r.serviceTwtKeepaliveConfig()||r.configs!=2||r._twt_keepalive.applied)return false;
 }
 Owner f;f.requestTwtKeepalive(true);f.arm();f.serviceTwtKeepaliveConfig();f.configError=-9;f.configAction=3;f.cancelTwtTrial(MosaicoTwt::Stop::Explicit);
 if(!f._twt.state.cleanupPending||!f._twt_cleanup_failed||f._twt.state.cleanupStage!=8||!f._twt_keepalive.applied||f._twt_keepalive.configKnown||f._twt_keepalive.restoreResult!=-9)return false;
 f.cleanupTwtTrial();if(f.configs!=2||f.psRestores||f.teardowns)return false;
 Owner partial;partial.requestTwtKeepalive(true);partial.arm();partial.configError=-4;
 if(partial.serviceTwtKeepaliveConfig()||partial.configs!=2||partial._twt_keepalive.applied||partial._twt_keepalive.restoreOwed||!partial._twt_keepalive.restoredKnownBaseline)return false;
 Owner failed;failed.requestTwtKeepalive(true);failed.arm();failed.configError=-4;failed.configAction=3;
 if(failed.serviceTwtKeepaliveConfig()||failed.configs!=2||failed._twt_keepalive.applied||!failed._twt_keepalive.restoreOwed||!failed._twt_cleanup_failed||!failed._twt.state.cleanupPending||failed._twt_keepalive.configKnown||!failed._twt_keepalive.needsRestartToRestoreDefaults)return false;
 failed.cleanupTwtTrial();if(failed.configs!=2)return false;
 return true;
}
static_assert(traces(),"default no SDK, baseline no SDK, gates, cancel/OTA race and restoration debt");
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'keepalive.cpp';path.write_text(code)
            result=subprocess.run([str(test_mosaico_twt.COMPILER),'-std=c++20','-fsyntax-only','-I',str(ROOT),str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_actual_parser_invalid_arguments(self):
        src=(ROOT/'main/debug/serial_debug.cpp').read_text()
        block=src.split('    if (std::strcmp(command, "twt-keepalive") == 0)',1)[1].split('    if (std::strcmp(command, "twt") == 0)',1)[0]
        block=block.replace('::strtok_r','tokenize').replace('GetNetworkQuota()','quota()')
        code=r'''
#include <cstring>
#include <cstdio>
#include "main/host/mosaico_twt_keepalive.h"
struct Parser {
 const char* args[4]{};int cursor=0,calls=0;bool passed=false,selected=false;
 constexpr const char* tokenize(void*,const char*,char**){return args[cursor++];}
 constexpr Parser& quota(){return *this;}
 constexpr bool requestTwtKeepalive(bool on){++calls;selected=on;return true;}
 constexpr MosaicoTwtKeepalive::State twtKeepaliveSnapshot(){return {};}
 constexpr void result(const char*,const char* outcome,const char*){passed=!std::strcmp(outcome,"PASS");}
 constexpr void parse(){char* save=nullptr;
'''+block+r'''
 }
};
constexpr bool arguments(){
 Parser missing;missing.parse();if(missing.passed||missing.calls)return false;
 Parser bad;bad.args[0]="enable";bad.parse();if(bad.passed||bad.calls)return false;
 Parser extra;extra.args[0]="on";extra.args[1]="extra";extra.parse();if(extra.passed||extra.calls)return false;
 Parser statusExtra;statusExtra.args[0]="status";statusExtra.args[1]="off";statusExtra.parse();if(statusExtra.passed||statusExtra.calls)return false;
 Parser on;on.args[0]="on";on.parse();if(!on.passed||on.calls!=1||!on.selected)return false;
 Parser off;off.args[0]="off";off.parse();return off.passed&&off.calls==1&&!off.selected;
}
static_assert(arguments(),"production parser strict args do not mutate settings");
'''
        test_mosaico_twt.TwtTests().compile_cpp(code)
    def test_uart_admission_production(self):
        src=(ROOT/'main/debug/serial_debug_transport.h').read_text()
        body=src[src.index('inline bool uartAllowed'):src.index('\n}',src.index('inline bool uartAllowed'))]
        body=body.replace('inline bool','constexpr bool')+'\n}'
        code=r'''namespace std { constexpr int strcmp(const char* a,const char* b){while(*a&&*a==*b){++a;++b;}return *a-*b;} }
'''+body+r'''
static_assert(serial_debug_transport_placeholder);'''
        code=code.replace('static_assert(serial_debug_transport_placeholder);','static_assert(uartAllowed("twt-keepalive")); static_assert(!uartAllowed("arbitrary-unknown-command"));')
        test_mosaico_twt.TwtTests().compile_cpp(code)
    def test_strict_parser_and_no_notification(self):
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text().split('if (std::strcmp(command, "twt-keepalive") == 0)',1)[1].split('if (std::strcmp(command, "twt") == 0)',1)[0]
        self.assertIn(r'!option || ::strtok_r(nullptr, " \t", &save)',serial)
        for arg in ('on','off','status'):self.assertIn('std::strcmp(option, "'+arg+'")',serial)
        setter=(ROOT/'main/host/network_quota.cpp').read_text().split('bool NetworkQuota::requestTwtKeepalive',1)[1].split('MosaicoTwtKeepalive::State NetworkQuota::twtKeepaliveSnapshot',1)[0]
        self.assertNotIn('esp_wifi_',setter);self.assertNotIn('xTaskNotify',setter)
if __name__=='__main__':unittest.main()
