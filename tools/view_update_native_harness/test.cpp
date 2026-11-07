// Production functions are extracted by package_view_update_native_tests.py.
// Only LVGL/dot storage and measurement are spies; no firmware or device IO.
#include "ota_presentation_model.h"
#include "view_update_model.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
using mosaico_ota_view::Rect;
constexpr uint32_t Orange=0xFFA324,Purple=0xAE71D7,Cyan=0x68C9D6,Gold=0xE9C46A,Green=0x67E7AE;
enum class Icon {Refresh,Cross,Check,Chip,Unknown};
struct lv_font_t {int size;};
const lv_font_t lv_font_montserrat_14{14},lv_font_montserrat_16{16},lv_font_montserrat_20{20},lv_font_montserrat_24{24};
constexpr int LV_OBJ_FLAG_HIDDEN=1,LV_OBJ_FLAG_CLICKABLE=2,LV_STATE_DISABLED=1,LV_TEXT_ALIGN_CENTER=1,LV_TEXT_ALIGN_RIGHT=2,LV_LABEL_LONG_MODE_DOTS=1,LV_LABEL_LONG_MODE_CLIP=2,LV_COORD_MAX=32767,LV_TEXT_FLAG_NONE=0;
struct Stats {unsigned layout=0,label=0,measure=0,staticLabel=0;};
static Stats* stats=nullptr;
struct lv_obj_t {
 Rect rect{};std::string text;uint32_t color=0,borderColor=0;int font=0,align=0,longMode=0,pitch=0,borderWidth=0;
 unsigned flags=0,state=0;int icon=-1;unsigned meter=0;bool known=false;bool staticText=false;
 bool operator==(const lv_obj_t& b)const {
 return rect.x==b.rect.x&&rect.y==b.rect.y&&rect.w==b.rect.w&&rect.h==b.rect.h&&text==b.text&&color==b.color&&borderColor==b.borderColor&&font==b.font&&align==b.align&&longMode==b.longMode&&pitch==b.pitch&&borderWidth==b.borderWidth&&flags==b.flags&&state==b.state&&icon==b.icon&&meter==b.meter&&known==b.known;
 }
};
void place(lv_obj_t* o,int x,int y){++stats->layout;o->rect.x=x;o->rect.y=y;}
void lv_obj_set_size(lv_obj_t* o,int w,int h){++stats->layout;o->rect.w=w;o->rect.h=h;}
void lv_obj_remove_flag(lv_obj_t* o,unsigned f){o->flags&=~f;}
void lv_obj_add_flag(lv_obj_t* o,unsigned f){o->flags|=f;}
void lv_label_set_text(lv_obj_t* o,const char* t){++stats->label;if(o->staticText)++stats->staticLabel;o->text=t;}
const char* lv_label_get_text(lv_obj_t* o){return o->text.c_str();}
struct lv_point_t {int x,y;};
void lv_text_get_size(lv_point_t* p,const char* t,const lv_font_t* f,int,int,int,int){++stats->measure;p->x=int(std::strlen(t))*f->size/2;p->y=f->size;}
uint32_t lv_color_hex(uint32_t c){return c;}
void lv_obj_set_style_text_font(lv_obj_t* o,const lv_font_t* f,int){o->font=f->size;}
void lv_obj_set_style_text_color(lv_obj_t* o,uint32_t c,int){o->color=c;}
void lv_obj_set_style_text_align(lv_obj_t* o,int a,int){o->align=a;}
void lv_label_set_long_mode(lv_obj_t* o,int a){o->longMode=a;}
void lv_obj_set_style_border_width(lv_obj_t* o,int a,int){o->borderWidth=a;}
void lv_obj_set_style_border_color(lv_obj_t* o,uint32_t a,int){o->borderColor=a;}
bool lv_obj_has_state(lv_obj_t* o,unsigned s){return o->state&s;}
void lv_obj_add_state(lv_obj_t* o,unsigned s){o->state|=s;}
void lv_obj_remove_state(lv_obj_t* o,unsigned s){o->state&=~s;}
void lv_obj_invalidate(lv_obj_t*){}
void setText(lv_obj_t* o,const char* t,uint32_t c){o->text=t;o->color=c;}
void setTextPitch(lv_obj_t* o,int p){o->pitch=p;}
void setIcon(lv_obj_t* o,Icon i,uint32_t c){o->icon=int(i);o->color=c;}
void setMeter(lv_obj_t* o,unsigned bp,bool k,uint32_t c){o->meter=bp;o->known=k;o->color=c;}
void percent(uint16_t bp,char* p,size_t n){std::snprintf(p,n,"%u.%02u%%",bp/100,bp%100);}
struct ViewStorage {
 std::array<lv_obj_t,27> objects{};
 MosaicoOta::UiSnapshot _ota{},_otaRenderedStatic{};
 bool _otaStaticSeen=false,_otaRenderedEnabled=false,_otaApproved=false,_externalPowerReady=false,_otaNetworkReady=false,_otaLoader=false;
 const char* _otaRenderedAction=nullptr;
 lv_obj_t *_otaTitle=&objects[0],*_otaPhase=&objects[1],*_otaModeBadge=&objects[2],*_otaCurrent=&objects[3],*_otaTarget=&objects[4],*_otaStageIcon=&objects[5],*_otaPercent=&objects[6],*_otaMeter=&objects[7],*_otaBytes=&objects[8],*_otaHash=&objects[9],*_otaSignature=&objects[10],*_otaImageIcon=&objects[11],*_otaSignatureIcon=&objects[12],*_otaArrow=&objects[13],*_otaButton=&objects[14],*_otaButtonLabel=&objects[15];
 std::array<lv_obj_t*,2> _otaSlots{{&objects[16],&objects[17]}},_otaSlotNames{{&objects[18],&objects[19]}},_otaSlotNumbers{{&objects[20],&objects[21]}},_otaChips{{&objects[22],&objects[23]}};
 ViewStorage(){for(auto* p:{_otaCurrent,_otaTarget,_otaHash,_otaSignature,_otaSlotNames[0],_otaSlotNames[1]})p->staticText=true;}
};
#define VIEW_CLASS(N) struct N:ViewStorage {bool otaHasDownloadOffer()const;bool otaActionEnabled()const;void renderOtaAction();void renderOta();};
VIEW_CLASS(OldView)
VIEW_CLASS(NewView)
#include "OldView.inc"
#include "NewView.inc"
struct Pair {
 OldView old;NewView now;Stats before{},after{};unsigned steps=0;
 void run(bool staticStable=false){
  now._ota=old._ota;now._externalPowerReady=old._externalPowerReady;now._otaNetworkReady=old._otaNetworkReady;now._otaApproved=old._otaApproved;
  auto prev=after;stats=&before;old.renderOta();stats=&after;now.renderOta();++steps;
  for(size_t i=0;i<old.objects.size();++i)if(!(old.objects[i]==now.objects[i])){
   std::cerr<<"state mismatch step="<<steps<<" object="<<i<<" stage="<<int(old._ota.stage)<<"\n";std::abort();
  }
  assert(old._otaLoader==now._otaLoader);
  if(staticStable){assert(after.layout==prev.layout);assert(after.measure==prev.measure);assert(after.staticLabel==prev.staticLabel);}
 }
};
MosaicoOta::UiSnapshot sample(){
 MosaicoOta::UiSnapshot s{};s.currentSlot=0;s.targetSlot=1;s.size=4000000;s.transferSize=60000;
 std::strcpy(s.currentVersion,"0.13.95-r62-mosaico-ota");std::strcpy(s.targetVersion,"0.13.95-r63-mosaico-ota");
 std::memset(s.sha256,'a',64);std::strcpy(s.signatureShort,"1234567890abcdef");s.signatureVerified=true;s.modeKnown=true;return s;
}
int main(){
 using S=MosaicoOta::UiStage;using W=MosaicoOta::WorkPhase;
 Pair p;
 for(int stage=0;stage<=int(S::Failed);++stage)for(int work=0;work<=int(W::Verifying);++work)for(int delta=0;delta<2;++delta){
  p.old._ota=sample();p.old._ota.stage=S(stage);p.old._ota.workPhase=W(work);p.old._ota.deltaTransfer=delta;p.run();
  for(int n=0;n<12;++n){auto& s=p.old._ota;++s.revision;s.received+=31001;s.transferReceived+=937;s.progressBasisPoints+=123;s.size+=101;s.transferSize+=31;p.run(true);}
 }
 // First entry and all static dependencies, including binding appearing/disappearing.
 p.old._ota=sample();p.old._ota.stage=S::Available;p.run();
 auto change=[&](auto update){unsigned count=p.after.staticLabel;update(p.old._ota);p.run();assert(p.after.staticLabel>count);p.run(true);};
 change([](auto& s){s.signatureVerified=false;});change([](auto& s){s.signatureVerified=true;});
 change([](auto& s){s.sha256[0]='b';});change([](auto& s){s.sha256[0]='-';});change([](auto& s){s.sha256[0]='c';});
 change([](auto& s){s.targetVersion[0]='-';});change([](auto& s){s.targetVersion[0]='1';});
 change([](auto& s){s.currentVersion[0]='1';});change([](auto& s){s.signatureShort[0]='a';});
 change([](auto& s){s.targetSlot=0;});change([](auto& s){s.currentSlot=-1;});change([](auto& s){s.currentSlot=1;});
 change([](auto& s){s.imageVerified=true;});change([](auto& s){s.modeKnown=false;});
 change([](auto& s){s.stage=S::Failed;std::strcpy(s.error,"INVALID / SIGNATURE");});change([](auto& s){std::strcpy(s.error,"NEW_ERROR");});
 for(S stage:{S::Idle,S::Available,S::ReadyInstall,S::WaitingPower,S::ReadyReboot}){
  p.old._ota=sample();p.old._ota.stage=stage;p.old._ota.imageVerified=true;p.run();
  for(int network=0;network<2;++network)for(int power=0;power<2;++power)for(int latch=0;latch<2;++latch){
   p.old._otaNetworkReady=network;p.old._externalPowerReady=power;p.old._otaApproved=latch;p.run(true);
  }
 }
 std::cout<<"PASS "<<p.steps<<" equivalent frames; static labels old/new="<<p.before.staticLabel<<"/"<<p.after.staticLabel<<" measurement="<<p.before.measure<<"/"<<p.after.measure<<" layout="<<p.before.layout<<"/"<<p.after.layout<<"\n";
}
