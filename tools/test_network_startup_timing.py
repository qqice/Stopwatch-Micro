"""First-write RAM startup marks: actual app helper on RV32, no device/network."""
from pathlib import Path
import unittest
import test_microlink_peer_probe as rv
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'main/host/network_quota.cpp'
PREFIX=r'''
typedef unsigned uint32_t;typedef long long int64_t;
extern "C" void* memcpy(void*d,const void*s,unsigned n){auto*a=(unsigned char*)d;auto*b=(const unsigned char*)s;for(unsigned i=0;i<n;++i)a[i]=b[i];return d;}
extern "C" unsigned long long __udivdi3(unsigned long long a,unsigned long long b){unsigned long long q=0,r=0;for(int i=63;i>=0;--i){r=(r<<1)|((a>>i)&1);if(r>=b){r-=b;q|=1ULL<<i;}}return q;}
static int locks,enters,exits;static int64_t now_us;
static int startupTimingMux;
#define portENTER_CRITICAL(p) (++locks,++enters)
#define portEXIT_CRITICAL(p) (--locks,++exits)
static int64_t esp_timer_get_time(){return now_us;}
class NetworkQuota {public:struct StartupTimings{uint32_t validMask=0;uint32_t markMs[10]{};};void noteStartup(unsigned);StartupTimings startupTimings()const;};
static NetworkQuota::StartupTimings startupTiming{};
'''
CASES=r'''
static int run_case(int i){startupTiming={};locks=enters=exits=0;now_us=0;NetworkQuota q;
switch(i){
case 0:q.noteStartup(0);return startupTiming.validMask==1&&startupTiming.markMs[0]==0&&!locks&&enters==1&&exits==1?0:1;
case 1:now_us=1234567;q.noteStartup(3);now_us=9999999;q.noteStartup(3);return startupTiming.markMs[3]==1234&&startupTiming.validMask==8?0:2;
case 2:q.noteStartup(10);q.noteStartup(99);return !startupTiming.validMask&&!enters&&!exits?0:3;
case 3:for(unsigned x=0;x<10;++x){now_us=(x+1)*1000;q.noteStartup(x);}return startupTiming.validMask==1023&&startupTiming.markMs[9]==10?0:4;
case 4:now_us=8000;q.noteStartup(5);{auto s=q.startupTimings();s.validMask=0;s.markMs[5]=99;return startupTiming.validMask==32&&startupTiming.markMs[5]==8&&!locks&&enters==2&&exits==2?0:5;}
case 5:now_us=3000;q.noteStartup(8);{auto a=q.startupTimings();auto b=q.startupTimings();return a.validMask==b.validMask&&a.markMs[8]==b.markMs[8]&&!locks?0:6;}
}return 99;}
extern "C" {volatile int test_results[6]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<6;++i)test_results[i]=run_case(i);test_done();}
'''
class StartupAppTests(unittest.TestCase):
 def test_actual_helpers(self):
  s=SOURCE.read_text(encoding='utf8');oldf,oldr=rv.fixture,rv.RUN
  try:
   rv.fixture=lambda _:PREFIX+rv.function(s,'void NetworkQuota::noteStartup(')+rv.function(s,'NetworkQuota::StartupTimings NetworkQuota::startupTimings() const')+CASES
   rv.RUN=ROOT/'.artifacts/private/mosaico/startup-app-tests-001'
   self.assertEqual(rv.execute(s,'app-current',6),(0,)*6)
  finally:rv.fixture,rv.RUN=oldf,oldr
 def test_passive_baseline_hooks(self):
  s=SOURCE.read_text(encoding='utf8');h=(ROOT/'main/host/network_quota.h').read_text(encoding='utf8')
  self.assertIn('uint32_t markMs[10]',h)
  cb=s.split('esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,',1)[1].split('esp_netif_sntp_init',1)[0]
  self.assertIn('MosaicoClock::onSntpTime(tv)',cb)
  self.assertNotIn('xTaskNotify',cb);self.assertNotIn('esp_wifi_connect',cb)
  self.assertIn('esp_wifi_connect();\n            wait(5000);',s)
  self.assertIn('if (GetTailnetQuota().enabled() && !GetTailnetQuota().ready()) {\n            wait(5000);',s)
  for action in ('malloc','esp_wifi','xTask','nvs_','GetHAL','MosaicoOta'):
   self.assertNotIn(action,rv.function(s,'void NetworkQuota::noteStartup('))
if __name__=='__main__':unittest.main()
