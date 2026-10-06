"""Actual parser/cache bodies against deterministic parsed cJSON trees and chart geometry."""
import json, re, subprocess, tempfile, unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
P=(R/'main/host/token_history.cpp').read_text(encoding='utf8')
UI=(V/'view_mosaico.cpp').read_text(encoding='utf8')

class QuotaTrendFirmwareTests(unittest.TestCase):
    def compiler(self):
        found=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not found:self.skipTest('embedded compiler unavailable')
        return str(found[-1])
    def compile(self,code,name,standard='c++17'):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/(name+'.cpp');source.write_text(code,encoding='utf8')
            result=subprocess.run([self.compiler(),'-std='+standard,'-fsyntax-only','-I'+str(R/'main'),str(source)],capture_output=True,text=True)
            (R/'.artifacts/mosaico'/f'{name}.log').write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_geometry_fixed_percent_real_time_and_gaps(self):
        code='#include "'+(V/'quota_trend_geometry.h').as_posix()+'"\n'+r'''
using namespace mosaico_trend;
constexpr bool geometry() {
 QuotaTrendPoint a{100,0,true},b{1100,10000,true};
 auto lo=position(a,100,1100),hi=position(b,100,1100);
 if(lo.x!=32 || lo.y!=54 || hi.x!=428 || hi.y!=6)return false;
 b.epoch=350;b.remainingBasisPoints=5000;auto mid=position(b,100,1100);
 if(mid.x!=131 || mid.y!=30)return false; // Real quarter-span time, fixed 50% axis.
 if(!connects(a,b,100,1100))return false;
 b.breakBefore=true;if(connects(a,b,100,1100))return false;
 b.breakBefore=false;b.resetBefore=true;if(connects(a,b,100,1100))return false;
 b.resetBefore=false;a.valid=false;if(connects(a,b,100,1100))return false;
 a.valid=true;b.epoch=99;if(position(b,100,1100).valid)return false;
 b.epoch=350;b.remainingBasisPoints=10001;if(position(b,100,1100).valid)return false;
 b.remainingBasisPoints=10000;
 std::array<QuotaTrendPoint,25> hours{};std::array<QuotaTrendPoint,8> days{};
 if(latest(hours) || latest(days))return false;
 days[7]={1100,7777,true};if(!latest(days) || latest(days)->remainingBasisPoints!=7777 || latest(hours))return false;
 hours[23]={1090,5000,true};hours[24].valid=false;
 if(!latest(hours) || latest(hours)->epoch!=1090 || latest(hours)->remainingBasisPoints!=5000)return false;
 for(int shift=-2;shift<=2;++shift) {
  auto point=position(b,100,1100);
  if(point.x-2+shift<0 || point.x+2+shift>440 || point.y-2+shift<0 || point.y+2+shift>80)return false;
 }
 b.epoch=0xffffffffU;auto large=position(b,0xffffffffU-604800,0xffffffffU);
 return large.valid && large.x==428 && large.y==6;
}
static_assert(geometry(),"fixed 0-100 percent, real epoch x, gaps/reset breaks and clip margins");
'''
        self.compile(code,'quota-trend-geometry')
        self.assertIn('lv_obj_set_pos(_historyChart, 0, 300); lv_obj_set_size(_historyChart, 440, 80)',UI)
        self.assertIn('panel(detail, 0, 380, 440, 28, 0)',UI)
        self.assertIn('trend.hours.data() : trend.days.data()',UI)
        draw=UI.split('void CodexMicroView::historyChartEvent(',1)[1].split('void CodexMicroView::refreshHistory()',1)[0]
        self.assertIn('mosaico_trend::connects',draw);self.assertIn('points[i].resetBefore ? Gold',draw)
        self.assertNotIn('tokens',draw);self.assertNotIn('maximum',draw)
        header=UI.split('void CodexMicroView::updateHistoryHeader(',1)[1].split('void CodexMicroView::renderHistory()',1)[0]
        self.assertIn('mosaico_trend::latest(trend.hours) : mosaico_trend::latest(trend.days)',header)
        self.assertIn('percent(latest->remainingBasisPoints, value, sizeof(value))',header)
        self.assertIn('trend.capturedEpoch && trendAge > 600 ? " STALE"',header)
        self.assertIn('char value[24] = "--"',header)
        for method,nextmethod in (('modeEvent','refreshBattery'),('cellEvent','modeEvent'),('showHistory','setPageForDebug'),('selectHistory','showHistory')):
            start=UI.index('CodexMicroView::'+method+'(');body=UI[start:UI.index('\n}',start)]
            self.assertNotIn('requestJson',body);self.assertNotIn('fetchHistory',body)
        self.assertLessEqual(300+80,380);self.assertLessEqual(380+28,408)
        self.assertIn('76 + (i / Columns) * CellHeight',UI) # Original heatmap touch slots unchanged.

    def test_actual_parser_and_independent_cache_merge(self):
        helpers=P[P.index('bool integer('):P.index('}  // namespace')]
        helpers=re.sub(r'^bool (integer|cells|trendPoints|trend)\(',r'constexpr bool \1(',helpers,flags=re.M)
        helpers=re.sub(r'reinterpret_cast<const unsigned char\*>\(([^)]+)\)',r'\1',helpers).replace('const unsigned char* p','const char* p')
        helpers=helpers.replace('std::strlen','textLength').replace('std::strcpy','textCopy').replace('std::strcmp','textCompare')
        nesting=P[P.index('bool JsonNestingValid('):P.index('bool InitTokenHistory()')]
        nesting=nesting.replace('bool JsonNestingValid','constexpr bool HarnessJsonNestingValid')
        apply=P[P.index('bool ApplyTokenHistory('):P.index('bool TokenHistoryRejectionSelfTest()')]
        apply=apply.replace('bool ApplyTokenHistory','constexpr bool Harness::ApplyTokenHistory')
        apply=apply.replace('JsonNestingValid(', 'HarnessJsonNestingValid(')
        apply=apply.replace('cJSON_ParseWithLength(json, length)','parsedRoot').replace('esp_timer_get_time()','clockUs')
        code='#include "'+(R/'main/host/token_history.h').as_posix()+'"\n'+r'''
#include <memory>
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cstring>
constexpr size_t textLength(const char* p) { size_t n=0;while(p[n])++n;return n; }
constexpr void textCopy(char* out,const char* p) { while((*out++=*p++)){} }
constexpr int textCompare(const char* a,const char* b) { while(*a && *a==*b){++a;++b;}return *a-*b; }
struct cJSON { int type=0;double valuedouble=0;const char* valuestring="";const char* key="";cJSON* child=nullptr;cJSON* next=nullptr; };
enum { Object=1,Array,Number,String,True,False,Null };
constexpr bool cJSON_IsObject(const cJSON* p){return p && p->type==Object;}
constexpr bool cJSON_IsArray(const cJSON* p){return p && p->type==Array;}
constexpr bool cJSON_IsNumber(const cJSON* p){return p && p->type==Number;}
constexpr bool cJSON_IsString(const cJSON* p){return p && p->type==String;}
constexpr bool cJSON_IsBool(const cJSON* p){return p && (p->type==True || p->type==False);}
constexpr bool cJSON_IsTrue(const cJSON* p){return p && p->type==True;}
constexpr bool cJSON_IsNull(const cJSON* p){return p && p->type==Null;}
constexpr cJSON* cJSON_GetObjectItemCaseSensitive(const cJSON* p,const char* key) {
 if(!p)return nullptr;for(auto* c=p->child;c;c=c->next)if(!textCompare(c->key,key))return c;return nullptr;
}
constexpr cJSON* cJSON_GetArrayItem(const cJSON* p,int i) { if(!p)return nullptr;auto* c=p->child;while(c && i--)c=c->next;return c; }
constexpr int cJSON_GetArraySize(const cJSON* p) { int n=0;if(p)for(auto* c=p->child;c;c=c->next)++n;return n; }
constexpr void cJSON_Delete(cJSON*) {}
constexpr int xSemaphoreTake(int,int) { return 1; }
constexpr void xSemaphoreGive(int) {}
constexpr int pdMS_TO_TICKS(int v) { return v; }
constexpr int pdTRUE=1;
'''+helpers+nesting+r'''
struct Revision { uint32_t value=0;constexpr uint32_t load(){return value;}constexpr void store(uint32_t v){value=v;} };
struct Harness {
 int mutex=1;TokenHistorySnapshot* current=nullptr;Revision revision;
 cJSON* parsedRoot=nullptr;int64_t clockUs=100000;
 constexpr bool ApplyTokenHistory(const char*,size_t);
 constexpr ~Harness(){delete current;}
};
'''+apply+r'''
// Parsed-tree adapter keeps every production validation/merge branch intact.
// Raw JSON tokenization remains cJSON's responsibility, not reimplemented here.
struct Fixture {
 std::array<cJSON,1000> nodes{};size_t used=0;cJSON* root=nullptr;
 constexpr cJSON* add(cJSON* parent,const char* key,int type,double value=0,const char* text="") {
  auto* n=&nodes[used++];*n={type,value,text,key};
  if(parent){auto** tail=&parent->child;while(*tail)tail=&(*tail)->next;*tail=n;}return n;
 }
 constexpr Fixture(bool tokens=false,uint32_t tokenCapture=0,uint32_t end=1000000,bool data=true) {
  root=add(nullptr,"",Object);add(root,"version",Number,2);add(root,"available",tokens || data?True:False);
  add(root,"captured_epoch",Number,tokenCapture);add(root,"age_seconds",Number,0);add(root,"timezone_offset_minutes",Number,480);
  add(root,"token_available",tokens?True:False);add(root,"token_captured_epoch",Number,tokenCapture);add(root,"token_age_seconds",Number,12);
  for(const char* name:{"days","hours"}) {
   auto* a=add(root,name,Array);for(int i=0;i<(textCompare(name,"days")?24:30);++i) {
    auto* c=add(a,"",Object);add(c,"label",String,0,textCompare(name,"days")?"2026-10-10T01:00":"2026-10-10");
    add(c,"quality",String,0,"missing");add(c,"tokens",Null);
   }
  }
  auto* t=add(root,"quota_trend",Object);add(t,"version",Number,1);add(t,"available",data?True:False);
  add(t,"captured_epoch",Number,data?end:0);add(t,"age_seconds",Number,0);add(t,"limit_id",String,0,data?"codex":"");
  add(t,"duration_minutes",Number,10080);add(t,"timezone_offset_minutes",Number,480);
  add(t,"start_24h_epoch",Number,end-86400);add(t,"start_7d_epoch",Number,end-604800);add(t,"end_epoch",Number,end);
  for(const char* name:{"hours","days"}) {
   bool hourly=!textCompare(name,"hours");int count=hourly?25:8;auto* a=add(t,name,Array);
   for(int i=0;i<count;++i) {
    auto* p=add(a,"",Object);add(p,"epoch",Number,end-(count-1-i)*(hourly?3600:86400));
    add(p,"remaining_bp",data && i==count-1?Number:Null,7500);
    add(p,"break_before",False);add(p,"reset_before",False);
   }
  }
 }
 constexpr cJSON* trend(){return cJSON_GetObjectItemCaseSensitive(root,"quota_trend");}
 constexpr void number(cJSON* p,const char* key,double value){auto* n=cJSON_GetObjectItemCaseSensitive(p,key);n->type=Number;n->valuedouble=value;}
};
constexpr bool apply(Harness& h,Fixture& f){h.parsedRoot=f.root;return h.ApplyTokenHistory("{}",2);}
constexpr bool caches() {
 Harness h;Fixture first(true,999900,1000000,true);if(!apply(h,first))return false;
 uint32_t tokenReceipt=h.current->receivedAtMs;
 h.clockUs=200000;Fixture newer(false,0,1000060,true);if(!apply(h,newer))return false;
 if(!h.current->tokenAvailable || h.current->capturedEpoch!=999900 || h.current->receivedAtMs!=tokenReceipt ||
    h.current->quotaTrend.endEpoch!=1000060 || h.current->quotaTrend.receivedAtMs!=200)return false;
 Fixture older(true,999800,1000120,true);if(!apply(h,older))return false;
 if(h.current->capturedEpoch!=999900 || h.current->quotaTrend.capturedEpoch!=1000120)return false;
 Fixture malformed(false,0,1000180,true);malformed.number(malformed.trend(),"duration_minutes",60);
 auto revision=h.revision.load();if(apply(h,malformed) || revision!=h.revision.load() || h.current->quotaTrend.endEpoch!=1000120)return false;
 Fixture missing(false,0,1000180,false);auto* t=cJSON_GetObjectItemCaseSensitive(missing.root,"quota_trend");t->key="ignored";
 if(!apply(h,missing) || h.current->quotaTrend.endEpoch!=1000120)return false;
 Fixture aged(false,0,1700000,false);aged.number(aged.trend(),"captured_epoch",1000120);
 cJSON_GetObjectItemCaseSensitive(aged.trend(),"limit_id")->valuestring="codex";
 if(!apply(h,aged) || h.current->quotaTrend.available || h.current->quotaTrend.endEpoch!=1700000)return false;
 Fixture legacy(true,1000200,1700060,false);legacy.number(legacy.root,"version",1);
 if(!apply(h,legacy) || !h.current->available || h.current->quotaTrend.endEpoch!=1700000)return false;
 return !h.ApplyTokenHistory("[[[[[[[[[0]]]]]]]]]",19) && !h.ApplyTokenHistory("{}",32769);
}
constexpr bool rejection() {
 for(int fault=0;fault<19;++fault) {
  Harness h;Fixture f(false,0,1000000,fault<16);auto* t=f.trend();auto* p=cJSON_GetArrayItem(cJSON_GetObjectItemCaseSensitive(t,"hours"),24);
  if(fault==0)f.number(p,"remaining_bp",10001);
  if(fault==1)f.number(p,"remaining_bp",0.5);
  if(fault==2)f.number(p,"epoch",1000001);
  if(fault==3)f.number(p,"epoch",996400); // Non-increasing.
  if(fault==4)cJSON_GetObjectItemCaseSensitive(p,"break_before")->type=Number;
  if(fault==5)cJSON_GetObjectItemCaseSensitive(p,"reset_before")->type=True;
  if(fault==6)f.number(t,"start_7d_epoch",1);
  if(fault==7)cJSON_GetObjectItemCaseSensitive(t,"limit_id")->valuestring="\x01";
  if(fault==8)f.number(t,"captured_epoch",999999);
  if(fault==9)cJSON_GetObjectItemCaseSensitive(t,"days")->child=nullptr;
  if(fault==10)cJSON_GetObjectItemCaseSensitive(t,"limit_id")->valuestring="other";
  if(fault==11)cJSON_GetObjectItemCaseSensitive(t,"limit_id")->valuestring="1234567890123456789012345678901234567890123456789012345678901234";
  if(fault==12)f.number(t,"version",2);
  if(fault==13)f.number(t,"timezone_offset_minutes",0);
  if(fault==14)cJSON_GetObjectItemCaseSensitive(f.root,"token_available")->type=Number;
  if(fault==15)cJSON_GetObjectItemCaseSensitive(t,"available")->type=False;
  if(fault==16){f.number(t,"captured_epoch",999999);cJSON_GetObjectItemCaseSensitive(t,"limit_id")->valuestring="codex";}
  if(fault==17){f.number(t,"captured_epoch",1);cJSON_GetObjectItemCaseSensitive(t,"limit_id")->valuestring="other";}
  if(fault==18)f.number(t,"age_seconds",1);
  if(apply(h,f) || h.current || h.revision.load())return false;
 }
 return true;
}
static_assert(caches(),"token freshness and receipt are independent of trend; missing/invalid and aged ranges behave honestly");
static_assert(rejection(),"strict point/schema rejection preserves the whole cache");
'''
        self.compile(code,'quota-trend-parser-source','c++23')

    def test_actual_target_syntax_and_network_version(self):
        entries=json.loads((R/'.artifacts/mosaico/ota-build/compile_commands.json').read_text())
        logs=[]
        for name in ('token_history.cpp','network_quota.cpp','view_mosaico.cpp'):
            entry=next(e for e in entries if e['file'].endswith(name))
            command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
            result=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
            logs.append(name+'\n'+result.stdout+result.stderr)
            (R/'.artifacts/mosaico/quota-trend-target-syntax.log').write_text('\n'.join(logs),encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        network=(R/'main/host/network_quota.cpp').read_text()
        history=network.split('bool NetworkQuota::fetchHistory()',1)[1].split('bool NetworkQuota::fetch()',1)[0]
        self.assertIn('#ifdef MOSAICO_BOARD\n    const char* path = "/v2/history";',history)
        self.assertIn('#else\n    const char* path = "/v1/history";',history)
        self.assertEqual(history.count('requestJson('),1)

if __name__=='__main__':unittest.main()
