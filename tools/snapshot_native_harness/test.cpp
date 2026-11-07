#include <lvgl.h>
#include "dot_widgets.h"
#include "view_transition_snapshot.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
bool native_external_ram = true;
static constexpr int W=480,H=480;
static std::vector<uint16_t> framebuffer(W*H), draw(W*H), draw_second(W*120);
static lv_display_t* display;
static int failures=0, comparisons=0;
static size_t active_draw_bytes=0;
static const char* mode_name="full";
static bool use_second_buffer=false;
static void check(bool ok,const char* what) { if(!ok) { ++failures; std::printf("FAIL %s\n",what); } }
static void flush(lv_display_t* d,const lv_area_t* a,uint8_t* p) {
    // This is the display-owned RGB565 color map, never a snapshot buffer.
    check(lv_display_get_color_format(d)==LV_COLOR_FORMAT_RGB565,"RGB565 flush format");
    const int width=a->x2-a->x1+1;
    uintptr_t begin=reinterpret_cast<uintptr_t>(draw.data()), ptr=reinterpret_cast<uintptr_t>(p);
    size_t count=size_t(width)*(a->y2-a->y1+1)*2;
    uintptr_t second=reinterpret_cast<uintptr_t>(draw_second.data());
    bool firstOwned=ptr>=begin && ptr+count<=begin+active_draw_bytes;
    bool secondOwned=use_second_buffer && ptr>=second && ptr+count<=second+active_draw_bytes;
    check(firstOwned || secondOwned,"flush source belongs to either display RGB565 buffer, not snapshot source");
    for(int y=a->y1;y<=a->y2;++y) std::memcpy(framebuffer.data()+y*W+a->x1,p+(y-a->y1)*width*2,width*2);
    lv_display_flush_ready(d);
}
static std::vector<uint16_t> render() { lv_obj_invalidate(lv_screen_active());lv_refr_now(display);return framebuffer; }
static void ppm(const char* path,const std::vector<uint16_t>& pixels) {
    auto* f=std::fopen(path,"wb"); if(!f) std::abort();std::fprintf(f,"P6\n%d %d\n255\n",W,H);
    for(auto v:pixels) { unsigned char p[3]={static_cast<unsigned char>(((v>>11)&31)*255/31),static_cast<unsigned char>(((v>>5)&63)*255/63),static_cast<unsigned char>((v&31)*255/31)};std::fwrite(p,1,3,f); }std::fclose(f);
}
static void compare(const std::vector<uint16_t>& live,const std::vector<uint16_t>& cache,const char* name,bool save=false) {
    size_t count=0;std::vector<uint16_t> diff(W*H);for(size_t i=0;i<live.size();++i) if(live[i]!=cache[i]) {++count;diff[i]=0xf800;}
    ++comparisons;std::printf("COMPARE mode=%s %s differing_pixels=%zu\n",mode_name,name,count);check(count==0,name);
    if(save || count) {char file[120];std::snprintf(file,sizeof file,"%s-%s-live.ppm",mode_name,name);ppm(file,live);std::snprintf(file,sizeof file,"%s-%s-cache.ppm",mode_name,name);ppm(file,cache);std::snprintf(file,sizeof file,"%s-%s-diff.ppm",mode_name,name);ppm(file,diff);}
}
static lv_obj_t* panel(int x,int y,int variant) {
    auto* p=lv_obj_create(lv_screen_active());lv_obj_remove_style_all(p);lv_obj_remove_flag(p,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(p,x,y);lv_obj_set_size(p,440,402);lv_obj_set_style_bg_color(p,lv_color_hex(variant?0x1c2940:0x142c28),0);lv_obj_set_style_bg_opa(p,LV_OPA_COVER,0);lv_obj_set_style_radius(p,24,0);lv_obj_set_style_pad_all(p,0,0);
    auto* label=lv_label_create(p);lv_label_set_text(label,variant?"SYNTHETIC SETTINGS":"SYNTHETIC DASHBOARD");lv_obj_set_pos(label,24,18);lv_obj_set_style_text_color(label,lv_color_hex(0xe9edf2),0);
    auto* t=mosaico_dot::createText(p,390,90,9);lv_obj_set_pos(t,24,60);mosaico_dot::setText(t,variant?"42:17":"12345");
    auto* m=mosaico_dot::createMeter(p,390,70,3);lv_obj_set_pos(m,24,175);mosaico_dot::setMeter(m,variant?7325:4180,true);
    auto* i=mosaico_dot::createIcon(p,variant?mosaico_dot::Icon::Chip:mosaico_dot::Icon::Battery,72);lv_obj_set_pos(i,30,280);
    auto* b=lv_label_create(p);lv_label_set_text(b,"RGB565 target / public snapshot");lv_obj_set_pos(b,24,375);lv_obj_set_style_text_color(b,lv_color_hex(0xaabbcc),0);
    return p;
}
static long long micros() {return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
static void run_mode(bool partial) {
    mode_name=partial?"partial120":"full";use_second_buffer=partial;std::printf("MODE %s buffers=%d synchronous_headless_flush_ready=1 DMA_concurrency_test=0\n",mode_name,partial?2:1);
    active_draw_bytes=W*(partial?120:H)*2;std::fill(framebuffer.begin(),framebuffer.end(),0);
    lv_init();display=lv_display_create(W,H);lv_display_set_color_format(display,LV_COLOR_FORMAT_RGB565);lv_display_set_buffers(display,draw.data(),partial?draw_second.data():nullptr,active_draw_bytes,partial?LV_DISPLAY_RENDER_MODE_PARTIAL:LV_DISPLAY_RENDER_MODE_FULL);lv_display_set_flush_cb(display,flush);
    auto* screen=lv_screen_active();lv_obj_remove_style_all(screen);lv_obj_set_style_bg_color(screen,lv_color_hex(0x000000),0);lv_obj_set_style_bg_opa(screen,LV_OPA_COVER,0);lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    check(mosaico_dot::selfTest(),"actual dot self test");
    auto* a=panel(20,64,0);auto* b=panel(500,64,1);render();
    long long live_us=0,cache_us=0;
    {
      mosaico_transition::Snapshot snap;
      for(int direction: {-1,1}) for(int off: {0,80,240,480}) {
        int ax=20-direction*off,bx=20+direction*(480-off);
        snap.clear();lv_obj_set_pos(a,ax,64);lv_obj_set_pos(b,bx,64);
        auto t=micros();auto live=render();live_us+=micros()-t;
        lv_obj_set_pos(a,20,64);lv_obj_set_pos(b,20+direction*480,64);lv_obj_update_layout(screen);
        check(snap.begin(a,b),"two actual snapshots ready");check(snap.visualFor(a)!=a,"proxy created");check(lv_obj_has_flag(a,LV_OBJ_FLAG_HIDDEN),"original hidden");
        snap.setX(a,ax);snap.setX(b,bx);t=micros();auto cache=render();cache_us+=micros()-t;
        char name[80];std::snprintf(name,sizeof name,"slide_%s_%d",direction<0?"right":"left",off);compare(live,cache,name,off==80 && direction==1);
        snap.clear();snap.clear();check(!lv_obj_has_flag(a,LV_OBJ_FLAG_HIDDEN),"clear unhides original");check(lv_obj_get_x(a)==20,"clear preserves original position");
      }
      snap.clear();lv_obj_set_pos(a,20,64);lv_obj_add_flag(b,LV_OBJ_FLAG_HIDDEN);
      auto* sheet=lv_obj_create(screen);lv_obj_remove_style_all(sheet);lv_obj_remove_flag(sheet,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_size(sheet,440,402);lv_obj_set_pos(sheet,20,480);
      lv_obj_set_style_bg_color(sheet,lv_color_hex(0x080B10),0);lv_obj_set_style_bg_opa(sheet,LV_OPA_COVER,0);lv_obj_set_style_radius(sheet,10,0);lv_obj_set_style_pad_all(sheet,0,0);
      auto* header=lv_label_create(sheet);lv_label_set_text(header,"SYNTHETIC SETTINGS");lv_obj_set_pos(header,18,18);lv_obj_set_style_text_color(header,lv_color_hex(0xe9edf2),0);
      auto* tiles=lv_obj_create(sheet);lv_obj_remove_style_all(tiles);lv_obj_remove_flag(tiles,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_size(tiles,440,346);lv_obj_set_pos(tiles,0,56);lv_obj_set_style_bg_color(tiles,lv_color_hex(0x080B10),0);lv_obj_set_style_bg_opa(tiles,LV_OPA_COVER,0);lv_obj_set_style_radius(tiles,10,0);lv_obj_set_style_pad_all(tiles,0,0);
      for(int row=0;row<2;++row) for(int col=0;col<2;++col) {
        auto* tile=lv_obj_create(tiles);lv_obj_remove_style_all(tile);lv_obj_remove_flag(tile,LV_OBJ_FLAG_SCROLLABLE);lv_obj_set_pos(tile,12+218*col,8+166*row);lv_obj_set_size(tile,206,154);lv_obj_set_style_bg_color(tile,lv_color_hex(0x172630),0);lv_obj_set_style_bg_opa(tile,LV_OPA_COVER,0);lv_obj_set_style_radius(tile,12,0);lv_obj_set_style_pad_all(tile,0,0);
        auto* icon=mosaico_dot::createIcon(tile,static_cast<mosaico_dot::Icon>(row*2+col),36);lv_obj_set_pos(icon,14,12);
        auto* text=mosaico_dot::createText(tile,170,40,4);lv_obj_set_pos(text,14,66);mosaico_dot::setText(text,row?"42:17":"12345");
        auto* meter=mosaico_dot::createMeter(tile,170,26,2);lv_obj_set_pos(meter,14,116);mosaico_dot::setMeter(meter,4180+row*1000+col*500,true);
      }
      for(int y: {64,180,400}) {
        snap.clear();lv_obj_set_y(sheet,y);auto live=render();lv_obj_set_y(sheet,480);lv_obj_update_layout(screen);
        check(snap.beginInset(tiles,12),"settings content inset snapshot ready");check(lv_obj_get_style_radius(tiles,LV_PART_MAIN)==10,"inset restores original radius");lv_obj_set_y(sheet,y);auto cache=render();char name[80];std::snprintf(name,sizeof name,"settings_%d",y);compare(live,cache,name,y==180);
        snap.clear();check(!lv_obj_has_flag(tiles,LV_OBJ_FLAG_HIDDEN),"inset clear unhides tiles");
      }
      lv_obj_delete(sheet);lv_obj_remove_flag(b,LV_OBJ_FLAG_HIDDEN);
      // Public yFor must account for deferred layout immediately after setY.
      lv_obj_set_pos(b,500,64);check(snap.begin(b),"yFor capture ready");snap.setY(b,180);check(snap.yFor(b)==180,"proxy yFor follows offset without caller layout");snap.clear();
      snap.clear();lv_obj_set_pos(b,500,64);lv_obj_add_flag(b,LV_OBJ_FLAG_HIDDEN);check(snap.begin(b),"capture initially hidden original");snap.clear();check(lv_obj_has_flag(b,LV_OBJ_FLAG_HIDDEN),"clear restores initially hidden flag");lv_obj_remove_flag(b,LV_OBJ_FLAG_HIDDEN);
      for(int i=0;i<3;++i) {native_external_ram=false;check(!snap.begin(a,b),"reject nonexternal real buffer");check(!lv_obj_has_flag(a,LV_OBJ_FLAG_HIDDEN),"failure restores visible original");check(snap.visualFor(a)==a,"failure removes proxy");snap.clear();}native_external_ram=true;
      // A mutation between separate begins must be captured afresh, not reused.
      lv_obj_set_style_bg_color(a,lv_color_hex(0x743421),0);lv_obj_set_pos(a,20,64);lv_obj_set_pos(b,500,64);auto live=render();check(snap.begin(a),"fresh recapture after mutation");auto cache=render();compare(live,cache,"fresh_capture");snap.clear();
    }
    // Production-like differing page heights and sibling Z order.
    lv_obj_delete(a);lv_obj_delete(b);
    auto* status=mosaico_dot::createIcon(screen,mosaico_dot::Icon::Wifi,32);lv_obj_set_pos(status,20,20);
    auto* quota=panel(20,64,0);lv_obj_set_height(quota,360);
    // Footer is above quota in the child list, but quota ends before y426.
    auto* clock=mosaico_dot::createText(screen,260,48,5);lv_obj_set_pos(clock,110,426);mosaico_dot::setText(clock,"12:34:56");
    auto* date=lv_label_create(screen);lv_label_set_text(date,"2026-11-07");lv_obj_set_pos(date,320,456);lv_obj_set_style_text_color(date,lv_color_hex(0xe9edf2),0);
    // History is later in Z order and intentionally may obscure the footer.
    auto* history=panel(500,52,1);lv_obj_set_height(history,408);
    {
      mosaico_transition::Snapshot snap;
      for(bool targetQuota: {false,true}) for(int direction: {-1,1}) for(int off: {0,80,240,480}) {
        snap.clear();
        // Match setPageForDebug BEFORE transition preparation: target History
        // hides root footer/date; target Quota shows them and caches only incoming Quota.
        if(targetQuota) {lv_obj_remove_flag(clock,LV_OBJ_FLAG_HIDDEN);lv_obj_remove_flag(date,LV_OBJ_FLAG_HIDDEN);}
        else {lv_obj_add_flag(clock,LV_OBJ_FLAG_HIDDEN);lv_obj_add_flag(date,LV_OBJ_FLAG_HIDDEN);}
        auto* outgoing=targetQuota?history:quota;auto* incoming=targetQuota?quota:history;
        int outY=targetQuota?52:64,inY=targetQuota?64:52;
        int outX=20-direction*off,inX=20+direction*(480-off);
        lv_obj_set_pos(outgoing,outX,outY);lv_obj_set_pos(incoming,inX,inY);auto live=render();
        lv_obj_set_pos(outgoing,20,outY);lv_obj_set_pos(incoming,20+direction*480,inY);lv_obj_update_layout(screen);
        const bool cacheBoth=!targetQuota;
        const uint32_t beforeChildren=lv_obj_get_child_count(screen);int captureAttempts=0;
        if(cacheBoth) {++captureAttempts;check(snap.begin(outgoing,incoming),"target History cached pair ready");}
        else {
          ++captureAttempts;check(snap.begin(incoming),"target Quota single incoming snapshot ready");
          check(captureAttempts==1,"target Quota capture operation count exactly one");
          check(snap.visualFor(outgoing)==outgoing,"outgoing History remains live over visible footer");
          check(snap.visualFor(incoming)!=incoming,"incoming Quota has snapshot proxy");
          check(lv_obj_get_child_count(screen)==beforeChildren+1,"target Quota owns exactly one image resource");
          check(!lv_obj_has_flag(outgoing,LV_OBJ_FLAG_HIDDEN) && lv_obj_has_flag(incoming,LV_OBJ_FLAG_HIDDEN),"target Quota hides only cached incoming panel");
        }
        snap.setX(outgoing,outX);snap.setX(incoming,inX);auto actual=render();char name[100];std::snprintf(name,sizeof name,"%s_%s_%d",targetQuota?"history_quota_target_cached":"quota_history_cached",direction<0?"right":"left",off);compare(live,actual,name,off==240 && direction==1);snap.clear();
      }
    }
    std::printf("NATIVE CPU ONLY live_frames_us=%lld cached_frames_us=%lld frames=8 (not device FPS)\n",live_us,cache_us);
    lv_deinit();std::printf("MODE COMPLETE %s comparisons=%d cumulative_failures=%d\n",mode_name,comparisons,failures);
}

int main() {run_mode(false);run_mode(true);std::printf("RESULT comparisons=%d failures=%d\n",comparisons,failures);return failures?1:0;}
