"""Extract production DERP stack lifecycle and execute on installed RV32/Unicorn.

Mocks RTOS/heap/TLS, not a hardware, scheduler or DMA correctness claim.
No downloads, devices, shared build or SDK writes.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
import test_microlink_peer_probe as rv32

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'components/microlink-source'
COMP = REPO / 'components/microlink'
RUN = ROOT / '.artifacts/private/mosaico/derp-rtc-stack-tests-001'

PREFIX = r'''
typedef unsigned uint32_t;typedef unsigned uintptr_t;typedef unsigned size_t;
typedef int BaseType_t;typedef int esp_err_t;typedef unsigned EventBits_t;
typedef void* TaskHandle_t;typedef int EventGroupHandle_t;typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) (++locks)
#define portEXIT_CRITICAL(p) (--locks)
#define NULL nullptr
#define pdPASS 1
#define pdTRUE 1
#define pdFALSE 0
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 2
#define ESP_ERR_INVALID_STATE 3
#define ESP_ERR_TIMEOUT 4
#define ML_STATE_IDLE 0
#define ML_STATE_WIFI_WAIT 1
#define ML_STOP_TIMEOUT_MS 15000
#define ML_TASK_DERP_TX_STACK (14*1024)
#define ML_TASK_DERP_TX_PRIO 5
#define ML_TASK_DERP_TX_CORE 0
#define ML_TASK_NET_IO_STACK (8*1024)
#define ML_TASK_NET_IO_PRIO 7
#define ML_TASK_NET_IO_CORE 0
#define ML_TASK_COORD_STACK (12*1024)
#define ML_TASK_COORD_PRIO 5
#define ML_TASK_COORD_CORE 1
#define ML_TASK_WG_MGR_STACK (8*1024)
#define ML_TASK_WG_MGR_PRIO 7
#define ML_TASK_WG_MGR_CORE 1
#define MALLOC_CAP_RTCRAM 1
#define MALLOC_CAP_INTERNAL 2
#define MALLOC_CAP_8BIT 4
#define ML_EVT_NET_IO_EXITED 1
#define ML_EVT_DERP_TX_EXITED 2
#define ML_EVT_COORD_EXITED 4
#define ML_EVT_WG_MGR_EXITED 8
#define ML_EVT_SHUTDOWN_REQUEST 16
#define ML_EVT_WIFI_CONNECTED 32
#define ML_EVT_DERP_CONNECTED 64
#define ML_CMD_CONNECT 1
#define ML_CMD_DISCONNECT 2
#define DERP_FRAME_SEND_PACKET 1
#define SHUT_RDWR 2
#define portMAX_DELAY 0xffffffffu
#define pdMS_TO_TICKS(x) (x)
#define ESP_LOGI(tag,fmt,...) log_message(fmt)
#define ESP_LOGE(...) ((void)0)
typedef int ml_coord_cmd_t;
struct ml_derp_tx_item_t {int frame_type;void* data;unsigned len;};
struct microlink_t {int state;unsigned lifecycle_expected;int events,coord_cmd_queue;
 TaskHandle_t net_io_task,derp_tx_task,coord_task,wg_mgr_task;
 int coord_sock,disco_sock4,stun_sock,stun_sock6;int config_httpd;
 int derp_tx_queue,disco_rx_queue,wg_rx_queue,stun_rx_queue,peer_update_queue;
 struct {unsigned rx_header_have,rx_header_first_us;char rx_header[16];bool connected;int connected_region,sockfd,ssl,ssl_conf;} derp;};
static int locks,bad,cap_calls,plain_calls,fail_plain,fail_cap_count,live_caps,joins,drains,waits;
static int sequence,ssl_free_at,config_free_at,queue_free_at,exit_log_at,exited_at,parked_at,ordinary_deletes,pending;
static bool timeout_wait,all_exited,marker_rtc;static unsigned cap_seen[2],stack_seen;
static unsigned partial_bits;
static TaskHandle_t caller;static microlink_t ml;
extern "C" void test_done();
static void log_message(const char* fmt){if(fmt[0]=='D'&&fmt[5]=='I')exit_log_at=++sequence;}
extern "C" void* memset(void* d,int v,size_t n){for(unsigned i=0;i<n;++i)((char*)d)[i]=v;return d;}
extern "C" void* memcpy(void* d,const void* s,size_t n){for(unsigned i=0;i<n;++i)((char*)d)[i]=((const char*)s)[i];return d;}
extern "C" void free(void*){queue_free_at=++sequence;++drains;}
static bool esp_ptr_in_rtc_dram_fast(const void*){return marker_rtc;}
static bool esp_ptr_internal(const void*){return true;}
static bool esp_ptr_dma_capable(const void*){return !marker_rtc;}
static void ml_derp_tx_task(void*){}static void ml_net_io_task(void*){}static void ml_coord_task(void*){}static void ml_wg_mgr_task(void*){}
static BaseType_t xTaskCreatePinnedToCoreWithCaps(void(*)(void*),const char*,unsigned bytes,void*,int prio,TaskHandle_t* h,int core,unsigned caps){
 if(locks||bytes!=14336||prio!=5||core!=0)++bad;cap_seen[cap_calls%2]=caps;++cap_calls;stack_seen=bytes;
 if(cap_calls<=fail_cap_count)return 0;*h=(void*)102;++live_caps;return pdPASS;}
static BaseType_t xTaskCreatePinnedToCore(void(*)(void*),const char*,unsigned,void*,int,TaskHandle_t* h,int){++plain_calls;if(plain_calls==fail_plain)return 0;*h=(void*)(100+plain_calls);return pdPASS;}
static TaskHandle_t xTaskGetCurrentTaskHandle(){return caller;}
static void vTaskDeleteWithCaps(TaskHandle_t h){if(!h||h==caller||!all_exited||!live_caps)++bad;else --live_caps;++joins;}
static void xEventGroupSetBits(int,unsigned bits){if(bits==ML_EVT_DERP_TX_EXITED)exited_at=++sequence;}
static void xEventGroupClearBits(int,unsigned){}
static unsigned xEventGroupWaitBits(int,unsigned expected,int,int,unsigned){++waits;all_exited=!timeout_wait;return timeout_wait?partial_bits:expected;}
static void xQueueSend(int,void*,unsigned){}
static int shutdown(int,int){return 0;}
static void ml_close_sock(int){}
static void ml_config_httpd_start(int,microlink_t*){}
static void ml_config_httpd_stop(int){if(!all_exited)++bad;}
static void drain_derp_queue(int){if(!all_exited)++bad;++drains;}
static void drain_packet_queue(int){if(!all_exited)++bad;++drains;}
static void drain_peer_queue(int){if(!all_exited)++bad;++drains;}
static void wireguardif_derp_note(int,int,int,int,int,int){}
static void wireguardif_tx_packet_note(int,void*,unsigned,int){}
static void mbedtls_ssl_close_notify(int*){}
static void mbedtls_ssl_free(int*){ssl_free_at=++sequence;}
static void mbedtls_ssl_config_free(int*){config_free_at=++sequence;}
static bool xQueueReceive(int,ml_derp_tx_item_t* item,int){if(!pending)return false;--pending;*item={1,(void*)99,1};return true;}
static void vTaskDelay(unsigned);
static void vTaskDelete(TaskHandle_t);
'''

CASES = r'''
static void reset(){locks=bad=cap_calls=plain_calls=fail_plain=fail_cap_count=live_caps=joins=drains=waits=0;partial_bits=0;timeout_wait=all_exited=marker_rtc=false;caller=(void*)999;cap_seen[0]=cap_seen[1]=0;
 ml={};ml.state=ML_STATE_WIFI_WAIT;ml.coord_sock=ml.disco_sock4=ml.stun_sock=ml.stun_sock6=ml.derp.sockfd=-1;ml.events=1;}
static int run_case(int id){reset();
 if(id<3){fail_cap_count=id;auto r=create_derp_task(&ml);microlink_derp_stack_diag_t d;microlink_derp_stack_get_diagnostics(&d);
#if CONFIG_IDF_TARGET_ESP32S31
  if(id==2)return r!=pdPASS&&!live_caps&&!d.selected_pool&&d.create_failures?0:1;
  if(r!=pdPASS||d.selected_pool!=unsigned(id+1)||live_caps!=1||cap_seen[0]!=7||stack_seen!=14336)return 2;
  if(id==1&&cap_seen[1]!=6)return 3;
  ml.lifecycle_expected=2;if(microlink_stop(&ml)||live_caps||joins!=1)return 4;
#else
  if(r!=pdPASS||cap_calls||plain_calls!=1)return 5;
#endif
  return bad||locks?6:0;}
 if(id==3){create_derp_task(&ml);ml.lifecycle_expected=15;timeout_wait=true;partial_bits=ML_EVT_DERP_TX_EXITED;auto h=ml.derp_tx_task;
  if(microlink_stop(&ml)!=ESP_ERR_TIMEOUT||ml.derp_tx_task!=h||joins||drains||!ml.lifecycle_expected)return 7;
  timeout_wait=false;if(microlink_stop(&ml)||ml.lifecycle_expected||ml.derp_tx_task)return 8;
#if CONFIG_IDF_TARGET_ESP32S31
  if(joins!=1||live_caps)return 9;
#endif
  if(microlink_stop(&ml)||bad)return 10;return 0;}
 if(id==4){create_derp_task(&ml);ml.lifecycle_expected=2;caller=ml.derp_tx_task;
#if CONFIG_IDF_TARGET_ESP32S31
  if(microlink_stop(&ml)!=ESP_ERR_INVALID_STATE||waits||joins||drains)return 11;caller=(void*)999;if(microlink_stop(&ml)||live_caps)return 12;
#else
  if(microlink_stop(&ml))return 13;
#endif
  return bad?14:0;}
 if(id>=5&&id<=7){fail_plain=id-4;auto r=start_tasks(&ml);if(r!=ESP_FAIL||ml.lifecycle_expected||ml.derp_tx_task||live_caps||bad)return 15;return 0;}
 if(id==8){for(int i=0;i<64;++i){ml.state=ML_STATE_WIFI_WAIT;if(start_tasks(&ml)||microlink_stop(&ml)||live_caps||ml.lifecycle_expected||bad)return 16;}
#if CONFIG_IDF_TARGET_ESP32S31
  if(joins!=64||cap_calls!=64)return 17;
#else
  if(joins||cap_calls)return 18;
#endif
  return 0;}
 if(id==9){marker_rtc=true;microlink_derp_stack_diag_t a,b;
#if CONFIG_IDF_TARGET_ESP32S31
  unsigned marker=0;ml_derp_stack_note_running(&marker);microlink_derp_stack_get_diagnostics(&a);if(!a.marker_rtc||!a.marker_internal||a.marker_dma||a.marker_address!=(uintptr_t)&marker)return 19;
#endif
  microlink_derp_stack_get_diagnostics(&a);microlink_derp_stack_get_diagnostics(NULL);microlink_derp_stack_get_diagnostics(&b);
  if(a.version!=1||a.requested_stack_bytes!=14336||locks||cap_calls||joins)return 20;return 0;}
 if(id==10){fail_cap_count=2;auto r=start_tasks(&ml);
#if CONFIG_IDF_TARGET_ESP32S31
  if(r!=ESP_FAIL||ml.lifecycle_expected||live_caps||drains!=5)return 21;
#else
  if(r||microlink_stop(&ml))return 22;
#endif
  return bad?23:0;}
 if(id==11){fail_plain=3;timeout_wait=true;if(start_tasks(&ml)!=ESP_FAIL||!ml.lifecycle_expected||drains||joins)return 24;
  timeout_wait=false;if(microlink_stop(&ml)||live_caps||ml.lifecycle_expected||bad)return 25;return 0;}
 if(id==12){
#if CONFIG_IDF_TARGET_ESP32S31
  unsigned marker=0;marker_rtc=false;ml_derp_stack_note_running(&marker);microlink_derp_stack_diag_t d;microlink_derp_stack_get_diagnostics(&d);
  if(d.marker_rtc||!d.marker_internal||!d.marker_dma||d.marker_address!=(uintptr_t)&marker)return 26;
#endif
  return locks?27:0;}
 return 99;}
extern "C" {volatile int test_results[13]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
static void vTaskDelay(unsigned){}static void vTaskDelete(TaskHandle_t){}
extern "C" void _start(){for(int i=0;i<13;++i)test_results[i]=run_case(i);test_done();}
'''


def production():
    return (COMP/'src/microlink.c').read_text(encoding='utf8'), (COMP/'src/ml_derp.c').read_text(encoding='utf8')


def has_caps_self_delete(code):
    code = re.sub(r'/\*.*?\*/|//[^\n]*', '', code, flags=re.S)
    return bool(re.search(r'vTaskDeleteWithCaps\s*\(\s*(?:NULL|0)\s*\)', code))


def fixture(s31, exiting=False):
    source, derp = production()
    public = (COMP/'include/microlink.h').read_text(encoding='utf8')
    end = public.index('} microlink_derp_stack_diag_t;') + len('} microlink_derp_stack_diag_t;')
    decl = public[public.rfind('typedef struct {', 0, end):end]
    helpers = source[source.index('static portMUX_TYPE derp_stack_diag_lock'):source.index('static void drain_packet_queue(')]
    stop = rv32.function(source, 'esp_err_t microlink_stop(')
    start = rv32.function(source, 'esp_err_t microlink_start(')
    # C permits the failure goto to skip cmd's initializer; C++ does not.
    # Hoist only the declaration in the harness; keep its assignment in place.
    start = 'static esp_err_t start_tasks(microlink_t* ml) {\nml_coord_cmd_t cmd;\n' + start[start.index('    /* Create tasks */'):]
    start = start.replace('ml_coord_cmd_t cmd = ML_CMD_CONNECT;', 'cmd = ML_CMD_CONNECT;')
    prefix = f'#define CONFIG_IDF_TARGET_ESP32S31 {int(s31)}\n' + PREFIX + decl + helpers + stop + start
    if not exiting:
        return prefix + CASES
    disconnect = rv32.function(derp, 'void ml_derp_disconnect(')
    exit_fn = rv32.function(derp, 'static void derp_task_exit(')
    return prefix + disconnect + exit_fn + r'''
extern "C" {volatile int test_results[1]={99};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
static void vTaskDelay(unsigned){parked_at=++sequence;test_results[0]=
 ssl_free_at>0&&config_free_at>ssl_free_at&&queue_free_at>config_free_at&&exit_log_at>queue_free_at&&exited_at>exit_log_at&&parked_at>exited_at&&ml.derp_tx_task==(void*)102&&live_caps==1&&!joins&&!ordinary_deletes?0:1;test_done();}
static void vTaskDelete(TaskHandle_t h){++ordinary_deletes;test_results[0]=!h&&!ml.derp_tx_task&&ssl_free_at&&config_free_at>ssl_free_at&&queue_free_at>config_free_at&&exited_at>queue_free_at&&exit_log_at>exited_at&&!joins&&!cap_calls?0:2;test_done();}
extern "C" void _start(){ml={};ml.events=1;ml.derp.sockfd=9;ml.derp_tx_task=(void*)102;pending=1;
#if CONFIG_IDF_TARGET_ESP32S31
 live_caps=1;
#endif
 derp_task_exit(&ml);test_done();}
'''


def execute(s31, exiting=False):
    old_fixture, old_run = rv32.fixture, rv32.RUN
    label = ('s31' if s31 else 'legacy') + ('-exit' if exiting else '-lifecycle')
    source = '\n'.join(production())
    try:
        rv32.fixture, rv32.RUN = lambda _: fixture(s31, exiting), RUN
        result = rv32.execute(source, label, 1 if exiting else 13)
        (RUN/(label+'-execution.log')).write_text(
            'Actual extracted create/stop/start-task statements or DERP disconnect/exit; mock RTOS/TLS/heap.\n'
            f'source_sha256={hashlib.sha256(source.encode()).hexdigest()}\n'
            f'elf_sha256={hashlib.sha256((RUN/(label+".elf")).read_bytes()).hexdigest()}\nresults={result}\n')
        return result
    finally:
        rv32.fixture, rv32.RUN = old_fixture, old_run


class DerpRtcTests(unittest.TestCase):
    def test_s31_lifecycle(self):
        self.assertEqual(execute(True), (0,)*13)

    def test_legacy_lifecycle(self):
        self.assertEqual(execute(False), (0,)*13)

    def test_s31_cleanup_then_exited_then_park(self):
        self.assertEqual(execute(True, True), (0,))

    def test_legacy_cleanup_and_self_delete(self):
        self.assertEqual(execute(False, True), (0,))

    def test_source_safety_gates(self):
        source, derp = production()
        self.assertFalse(has_caps_self_delete(source+'\n'+derp))
        self.assertTrue(has_caps_self_delete(source+'\nvTaskDeleteWithCaps(NULL);'))
        self.assertTrue(has_caps_self_delete(source+'\nvTaskDeleteWithCaps(0);'))
        task = rv32.function(derp, 'void ml_derp_tx_task(')
        self.assertIn('ml_derp_stack_note_running(&stack_marker);', task)
        exit_fn = rv32.function(derp, 'static void derp_task_exit(')
        s31 = exit_fn.split('#if CONFIG_IDF_TARGET_ESP32S31',1)[1].split('#else',1)[0]
        after_publish = s31.split('xEventGroupSetBits(events, ML_EVT_DERP_TX_EXITED);',1)[1]
        self.assertNotRegex(re.sub(r'/\*.*?\*/','',after_publish,flags=re.S), r'\bml\b|vTaskDelete')
        getter = rv32.function(source, 'void microlink_derp_stack_get_diagnostics(')
        for forbidden in ('heap_', 'malloc', 'socket', 'esp_ptr_', 'network', 'xTask'):
            self.assertNotIn(forbidden, getter)
        self.assertIn('start_failure:\n    microlink_stop(ml);',source)
        destroy = rv32.function(source, 'void microlink_destroy(')
        self.assertIn('if (microlink_stop(ml) != ESP_OK)', destroy)
        self.assertLess(destroy.index('return;\n    }'),destroy.index('vQueueDelete('))

    def test_full_20_file_patch_replay_reverse(self):
        patch_file = ROOT/'patches/microlink-idf61.patch'
        raw = patch_file.read_bytes()
        sections = re.findall(rb'^diff --git a/(.*?) b/[^\n]+\n.*?(?=^diff --git |\Z)',raw,re.M|re.S)
        self.assertEqual(len(sections),20)
        RUN.mkdir(parents=True, exist_ok=True)
        normalized_patch = RUN/'replay-normalized.patch'
        normalized_patch.write_bytes(raw.replace(b'\r\n',b'\n'))
        with tempfile.TemporaryDirectory(dir=RUN) as temporary:
            isolated = Path(temporary)
            # Outside repository semantics, without creating or modifying a .git.
            env = {**os.environ, 'GIT_CEILING_DIRECTORIES':str(isolated.parent)}
            upstream = {}
            for encoded in sections:
                name = encoded.decode()
                result = subprocess.run(['git','-C',str(REPO),'show','HEAD:'+name],capture_output=True)
                upstream[name] = result.stdout if result.returncode == 0 else None
                if result.returncode == 0:
                    target = isolated/name
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(result.stdout)
            commands = []
            def apply(*arguments):
                args = ['git','apply',*arguments,str(normalized_patch)]
                result = subprocess.run(args,cwd=isolated,env=env,capture_output=True,text=True)
                commands.append({'args':args,'returncode':result.returncode,'output':result.stdout+result.stderr})
                self.assertEqual(result.returncode,0,result.stderr)
            apply('--check')
            apply()
            for name in upstream:
                self.assertEqual((isolated/name).read_bytes().replace(b'\r\n',b'\n'),
                                 (REPO/name).read_bytes().replace(b'\r\n',b'\n'),name)
            apply('--reverse','--check')
            apply('--reverse')
            for name, original in upstream.items():
                if original is None:
                    self.assertFalse((isolated/name).exists(),name)
                else:
                    self.assertEqual((isolated/name).read_bytes().replace(b'\r\n',b'\n'),original.replace(b'\r\n',b'\n'),name)
            head = subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()
            self.assertEqual(head,'216da3300f0493b0860247d43f7af5ce29df63a5')
            (RUN/'patch-replay.json').write_text(json.dumps({
                'nested_head':head,'file_count':20,'normalized_forward_source_equivalence':True,
                'normalized_reverse_upstream_equivalence':True,'commands':commands,
                'patch_sha256':hashlib.sha256(raw).hexdigest()},indent=2))


if __name__ == '__main__':
    unittest.main()
