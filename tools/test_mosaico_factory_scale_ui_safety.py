"""Extracted production touch/IMU policy on RV32; scheduler and I2C are mocks."""
from pathlib import Path
import unittest
import test_microlink_peer_probe as rv
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'.artifacts/private/mosaico/gauge95r4-recovery-safety-tests-001/ui'
COMMON=r'''
typedef unsigned char uint8_t;typedef unsigned short uint16_t;typedef unsigned uint32_t;typedef long long int64_t;
namespace std {enum {memory_order_relaxed};}
template<class T>struct Atomic {T value{};T load(int=0)const{return value;}T exchange(T v,int=0){T old=value;value=v;return old;}void fetch_add(T v,int=0){value+=v;}};
extern "C" void* memcpy(void* d,const void* s,__SIZE_TYPE__ n){for(__SIZE_TYPE__ i=0;i<n;++i)((char*)d)[i]=((const char*)s)[i];return d;}
static bool protectedTrial;static unsigned reads,now=1000;
struct Hal {struct TouchPoint {int num=0,x=0,y=0;};TouchPoint getTouchPoint();bool gaugeScaleTrialActive()const{return protectedTrial;}};
static Hal hal;Hal& GetHAL(){return hal;}
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
int64_t esp_timer_get_time(){return ++now;}
'''
TOUCH=r'''
struct lv_indev_t{};struct lv_point_t {int x,y;};struct lv_indev_data_t {int state;lv_point_t point;};
constexpr int LV_INDEV_STATE_RELEASED=0,LV_INDEV_STATE_PRESSED=1,ESP_OK=0,Resolution=466;
Atomic<bool> touch_idle_polling,orientation_healthy;Atomic<int64_t> touch_last_us;Atomic<unsigned> touch_reads,touch_max_gap_us;
static bool touch_wait_for_release,contact=true,validSample=true;static Hal::TouchPoint cached_touch;static int touch_mux;static void* display=nullptr;static void* touch=(void*)1;
namespace TouchSleep {bool blocksTouch(){return false;}}
void update_max(Atomic<unsigned>& a,unsigned b){if(a.value<b)a.value=b;}
int esp_lcd_touch_read_data(void*){++reads;return validSample?0:1;}
bool esp_lcd_touch_get_coordinates(void*,uint16_t* x,uint16_t* y,void*,uint8_t* count,int){*x=12;*y=34;*count=contact?1:0;return true;}
void lv_display_rotate_point(void*,lv_point_t*){}
'''
MOTION=r'''
constexpr int ESP_OK=0;constexpr unsigned portMAX_DELAY=0xffffffff,pdTRUE=1;
#define pdMS_TO_TICKS(x) (x)
static unsigned waits[4],waitCount,writes,samples;static bool forcedOnly;static int motion_mux,imu;
Atomic<bool> motion_idle;
struct Snapshot{bool idle=false,valid=false;int error=0,ax=0,ay=0,az=0;int64_t sampleUs=0;unsigned samples=0,generation=0,degrees=0,readErrors=0;};static Snapshot motion_snapshot,snapshot;
namespace mosaico_orientation {struct Model{unsigned degrees=0;void resetPending(){}bool sample(int,int,int,int64_t){return false;}};}
int motion_write(int,int,int value){++writes;return 0;}
int bmi270_get_acce_data(int,int*,int*,int*){++samples;return 0;}
void vTaskDelay(unsigned){}
void ulTaskNotifyTake(int,unsigned timeout){waits[waitCount++]=timeout;protectedTrial=false;}
'''
class SafetyTests(unittest.TestCase):
 def execute(self,code,label,count):
  code+=f'\nextern "C" {{volatile int test_results[{count}]={{}};}}extern "C" __attribute__((noinline,used)) void test_done(){{for(;;){{asm volatile("");}}}}extern "C" void _start(){{for(int i=0;i<{count};++i)test_results[i]=run_case(i);test_done();}}\n'
  old_run,old_fixture=rv.RUN,rv.fixture
  try:
   rv.RUN=RUN;rv.fixture=lambda source:source
   values=rv.execute(code,label,count)
   (RUN/(label+'-execution.log')).write_text('Extracted production policy; mock scheduler/I2C, bounded task iterations.\n'+str(values))
   self.assertEqual(values,(0,)*count)
  finally:rv.RUN,rv.fixture=old_run,old_fixture
 def test_wake_guard_precedes_every_side_effect(self):
  source=(ROOT/'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text()
  body=rv.function(source,'void CodexMicroView::wakeDisplay()')
  self.assertEqual(body.split('{',1)[1].lstrip().splitlines()[0],'if (GetHAL().gaugeScaleTrialActive()) return; // Cached atomic only; no gauge I2C or cross-lock.')
  self.assertEqual(source.count('_locked = false'),1)
  trial=rv.function((ROOT/'main/hal/mosaico/mosaico_gauge_scale_trial.inc').read_text(),'bool Hal::gaugeScaleTrialActive()')
  self.assertNotIn('mutex',trial);self.assertNotIn('gauge_word',trial)
 def test_actual_touch_protection_and_fresh_release(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  body=rv.function(source,'void read_touch(')
  release=rv.function((ROOT/'main/hal/mosaico/mosaico_touch_power_model.h').read_text(),'constexpr bool waitForPhysicalRelease(')
  getter=rv.function(source,'Hal::TouchPoint Hal::getTouchPoint()')
  code=COMMON+TOUCH+'namespace mosaico_touch_power {'+release+'}\n'+body+getter+r'''
int run_case(int id){reads=0;protectedTrial=true;touch_wait_for_release=false;contact=validSample=true;touch_idle_polling.value=false;orientation_healthy.value=true;cached_touch={1,12,34};lv_indev_data_t data{1,{12,34}};
 if(GetHAL().getTouchPoint().num||reads)return 6;
 read_touch(nullptr,&data);if(reads||data.state||cached_touch.num||!touch_wait_for_release)return 1;
 if(id==0)return 0;protectedTrial=false;read_touch(nullptr,&data);if(reads!=1||data.state||cached_touch.num||!touch_wait_for_release)return 2;
 validSample=false;contact=false;read_touch(nullptr,&data);if(data.state||!touch_wait_for_release)return 3;
 validSample=true;read_touch(nullptr,&data);if(data.state||touch_wait_for_release)return 4;
 contact=true;read_touch(nullptr,&data);return data.state!=1||cached_touch.num!=1||GetHAL().getTouchPoint().num!=1?5:0;}
'''
  self.execute(code,'touch',2)
 def test_actual_motion_loop_resumes_after_protection(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  task=rv.function(source,'void motion_task(')
  loop=task[task.index('    mosaico_orientation::Model model;'):].rsplit('}',1)[0]
  self.assertIn('requestedIdle ? portMAX_DELAY : pdMS_TO_TICKS(250)',loop)
  self.assertNotIn('snapshot.idle = motion_idle.load()',rv.function(source,'Hal::MotionOrientation Hal::motionOrientation()'))
  # Bound only task lifetime, retaining every production statement within loop.
  loop=loop.replace('for (;;)','for (unsigned iteration=0;iteration<3;++iteration)')
  code=COMMON+MOTION+'void run_task(){'+loop+'}\n'+r'''
int run_case(int id){snapshot={};motion_snapshot={};waitCount=writes=samples=0;protectedTrial=true;motion_idle.value=id==1;
 run_task();if(waitCount!=3)return 1;
 if(id==0){if(waits[0]!=251||waits[1]!=81||waits[2]!=81||writes!=1||samples!=2||motion_snapshot.idle||!motion_snapshot.valid)return 2;}
 else if(waits[0]!=portMAX_DELAY||waits[1]!=portMAX_DELAY||waits[2]!=portMAX_DELAY||writes||samples||!motion_snapshot.idle||motion_snapshot.valid)return 3;
 return 0;}
'''
  self.execute(code,'motion',2)
if __name__=='__main__':unittest.main()
