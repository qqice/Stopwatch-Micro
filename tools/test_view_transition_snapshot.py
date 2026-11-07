"""Actual helper control flow with constexpr public-API spies; no device/build."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / 'main/apps/app_codex_micro/view'


class TransitionSnapshotTests(unittest.TestCase):
    def test_actual_helper_fault_cleanup_and_geometry(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers)
        header = (VIEW / 'view_transition_snapshot.h').read_text(encoding='utf8')
        entry = header.split('    struct Entry {', 1)[1].split('    bool capture(', 1)[0]
        entry = 'struct Entry {' + entry
        source = (VIEW / 'view_transition_snapshot.cpp').read_text(encoding='utf8')
        source = source.split('namespace mosaico_transition {', 1)[1].rsplit('}', 1)[0]
        source = source.replace('Snapshot::~Snapshot() { clear(); }', 'constexpr void release() { clear(); }')
        source = re.sub(r'(?m)^(bool|void|lv_obj_t\*|int32_t) Snapshot::', r'constexpr \1 ', source)
        # Only namespace/class packaging changes; execute the actual helper body
        # in constant evaluation with deterministic API/allocator failure spies.
        code = r'''#include <cstdint>
#include <initializer_list>
#define ESP_LOGI(...) do {} while(0)
constexpr unsigned LV_OBJ_FLAG_HIDDEN=1,LV_OBJ_FLAG_CLICKABLE=2,LV_OBJ_FLAG_SCROLLABLE=4;
using lv_obj_flag_t=unsigned;
constexpr int LV_COLOR_FORMAT_RGB565=1,LV_PART_MAIN=0,LV_OPA_COVER=255,LV_IMAGE_ALIGN_TOP_LEFT=1;
struct Header {unsigned w=444,h=406;};
struct lv_draw_buf_t {Header header{};int* data=nullptr;bool live=false,dropped=false;};
struct lv_obj_t {int x=20,y=64,w=440,h=402;unsigned flags=0;lv_obj_t* parent=nullptr;const void* src=nullptr;bool live=true;
 int radius=10,border=0,shadow=0,outline=0,bg=0x080B10,opa=255,align=0;};
struct Harness {
 lv_obj_t parent{}, originals[2]{},images[4]{},children[4]{};lv_draw_buf_t buffers[4]{};
 int external[4]{},internal[4]{};
 int snapshots=0,creates=0,deletes=0,drops=0,destroys=0;
 int failSnapshot=0,failImage=0,failSource=0,failExternal=0,badGeometry=0,lastFormat=0,capturedRadius=-1;
 bool orderError=false;
 constexpr Harness(){originals[0].parent=originals[1].parent=&parent;}
 constexpr void lv_obj_update_layout(lv_obj_t*)const{}
 constexpr bool lv_obj_has_flag(lv_obj_t* p,unsigned f)const{return (p->flags&f)!=0;}
 constexpr void lv_obj_add_flag(lv_obj_t* p,unsigned f){p->flags|=f;}
 constexpr void lv_obj_remove_flag(lv_obj_t* p,unsigned f){p->flags&=~f;}
 constexpr lv_draw_buf_t* lv_snapshot_take(lv_obj_t* p,int cf){
  lastFormat=cf;capturedRadius=p->radius;
  ++snapshots;if(snapshots==failSnapshot)return nullptr;
  auto& b=buffers[snapshots-1];b.live=true;
  b.header.w=p->w+(p->radius?4:0);b.header.h=p->h+(p->radius?4:0);
  b.data=snapshots==failExternal?&internal[snapshots-1]:&external[snapshots-1];
  if(snapshots==badGeometry)b.header.h+=2;
  return &b;
 }
 constexpr bool esp_ptr_external_ram(int* data)const{for(int i=0;i<4;++i)if(data==&external[i])return true;return false;}
 constexpr lv_obj_t* lv_image_create(lv_obj_t* p){++creates;if(creates==failImage)return nullptr;images[creates-1].parent=p;return &images[creates-1];}
 constexpr void lv_image_set_src(lv_obj_t* p,lv_draw_buf_t* b){if(creates!=failSource)p->src=b;}
 constexpr const void* lv_image_get_src(lv_obj_t* p)const{return p->src;}
 constexpr lv_obj_t* lv_obj_get_parent(lv_obj_t* p)const{return p->parent;}
 constexpr int lv_obj_get_x(lv_obj_t* p)const{return p->x;}
 constexpr int lv_obj_get_y(lv_obj_t* p)const{return p->y;}
 constexpr int lv_obj_get_width(lv_obj_t* p)const{return p->w;}
 constexpr int lv_obj_get_height(lv_obj_t* p)const{return p->h;}
 constexpr int lv_obj_get_style_radius(lv_obj_t* p,int)const{return p->radius;}
 constexpr int lv_obj_get_style_border_width(lv_obj_t* p,int)const{return p->border;}
 constexpr int lv_obj_get_style_shadow_width(lv_obj_t* p,int)const{return p->shadow;}
 constexpr int lv_obj_get_style_outline_width(lv_obj_t* p,int)const{return p->outline;}
 constexpr int lv_obj_get_style_bg_color(lv_obj_t* p,int)const{return p->bg;}
 constexpr int lv_obj_get_style_bg_opa(lv_obj_t* p,int)const{return p->opa;}
 constexpr bool lv_color_eq(int a,int b)const{return a==b;}
 constexpr int lv_color_hex(int a)const{return a;}
 constexpr void lv_obj_set_style_radius(lv_obj_t* p,int r,int){p->radius=r;}
 constexpr unsigned lv_obj_get_child_count(lv_obj_t*)const{return 4;}
 constexpr lv_obj_t* lv_obj_get_child(lv_obj_t*,unsigned i){return &children[i];}
 constexpr void lv_image_set_inner_align(lv_obj_t* p,int a){p->align=a;}
 constexpr void lv_obj_set_size(lv_obj_t* p,int w,int h){p->w=w;p->h=h;}
 constexpr void lv_obj_set_pos(lv_obj_t* p,int x,int y){p->x=x;p->y=y;}
 constexpr void lv_obj_set_x(lv_obj_t* p,int x){p->x=x;}
 constexpr void lv_obj_set_y(lv_obj_t* p,int y){p->y=y;}
 constexpr void lv_obj_delete(lv_obj_t* p){if(!p->live)orderError=true;p->live=false;++deletes;}
 constexpr void lv_image_cache_drop(lv_draw_buf_t* b){
  for(int i=0;i<creates;++i)if(images[i].live&&images[i].src==b)orderError=true;
  if(!b->live)orderError=true;b->dropped=true;++drops;
 }
 constexpr void lv_draw_buf_destroy(lv_draw_buf_t* b){if(!b->live||!b->dropped)orderError=true;b->live=false;++destroys;}
 constexpr int64_t esp_timer_get_time()const{return 0;}
'''+entry+'Entry _entries[2]{};\n'+source+r'''
};
constexpr bool success(){
 Harness h;
 auto* a=&h.originals[0];auto* b=&h.originals[1];
 if(!h.begin(a,b)||h.lastFormat!=LV_COLOR_FORMAT_RGB565||h.visualFor(a)==a||h.visualFor(b)==b)return false;
 if(!h.lv_obj_has_flag(a,LV_OBJ_FLAG_HIDDEN)||!h.lv_obj_has_flag(b,LV_OBJ_FLAG_HIDDEN))return false;
 if(h.visualFor(a)->x!=18||h.visualFor(a)->y!=62)return false;
 h.setX(a,-220);h.setY(b,450);
 if(h.visualFor(a)->x!=-222||h.yFor(b)!=450||a->x!=20||b->y!=64)return false;
 h.clear();h.clear();
 if(h.orderError||h.deletes!=2||h.drops!=2||h.destroys!=2||a->flags||b->flags)return false;
 h.setX(a,40);h.setY(a,90);
 return a->x==40&&h.yFor(a)==90&&h.visualFor(a)==a;
}
constexpr bool faults(){
 for(int fault=0;fault<5;++fault)for(int n=1;n<=2;++n){
  Harness h;
  if(fault==0)h.failSnapshot=n;if(fault==1)h.failImage=n;
  if(fault==2)h.failSource=n;if(fault==3)h.failExternal=n;if(fault==4)h.badGeometry=n;
  auto* a=&h.originals[0];auto* b=&h.originals[1];
  b->flags=LV_OBJ_FLAG_HIDDEN;
  if(h.begin(a,b)||h.visualFor(a)!=a||h.visualFor(b)!=b)return false;
  if(a->flags||b->flags!=LV_OBJ_FLAG_HIDDEN||h.orderError)return false;
  for(auto& buf:h.buffers)if(buf.live)return false;
  h.clear();if(h.orderError)return false;
 }
 return true;
}
constexpr bool freshAndDestroy(){
 Harness h;auto* a=&h.originals[0];
 if(!h.begin(a,nullptr)||!h.begin(a,nullptr))return false;
 if(h.snapshots!=2||h.deletes!=1||h.destroys!=1)return false;
 h.release();return !h.orderError&&h.deletes==2&&h.destroys==2&&!a->flags;
}
static_assert(success(),"two-page proxy-only movement, ext border geometry and idempotent cleanup");
static_assert(faults(),"every capture/image/source/PSRAM/geometry fault in either slot rolls back");
static_assert(freshAndDestroy(),"fresh capture every transition and destructor cleanup");
constexpr bool insetCases(){
 for(int fault=0;fault<12;++fault){
  Harness h;auto* grid=&h.originals[0];grid->x=0;grid->y=56;grid->h=346;
  for(int i=0;i<4;++i){auto& c=h.children[i];c.x=12+(i%2)*218;c.y=8+(i/2)*166;c.w=206;c.h=154;}
  if(fault==1)h.failSnapshot=1;if(fault==2)h.failImage=1;if(fault==3)h.failSource=1;
  if(fault==4)h.failExternal=1;if(fault==5)h.badGeometry=1;
  if(fault==6)h.parent.bg=0;if(fault==7)grid->shadow=1;if(fault==8)grid->border=1;
  if(fault==9)h.children[3].y=190;if(fault==10)h.children[0].shadow=1;if(fault==11)grid->w=439;
  const bool ok=h.beginInset(grid,12);
  if(ok!=(fault==0)||grid->radius!=10||h.orderError)return false;
  if(ok){
   if(h.lastFormat!=LV_COLOR_FORMAT_RGB565||h.capturedRadius!=0)return false;
   auto* img=h.visualFor(grid);
   if(img==grid||img->parent!=&h.parent||img->align!=LV_IMAGE_ALIGN_TOP_LEFT||img->w!=440||img->h!=334)return false;
   h.setY(&h.parent,480); // Sheet is not cached: move live parent and cached child together.
   if(h.parent.y!=480||img->y!=56)return false;
  }else if(grid->flags||h.visualFor(grid)!=grid)return false;
  h.clear();if(h.orderError||grid->flags||grid->radius!=10)return false;
 }
 return true;
}
static_assert(insetCases(),"RGB565 settings crop, radius restoration, live parent and conservative fallback");
'''
        log = ROOT / '.artifacts/mosaico/view-transition-snapshot.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshot.cpp'
            path.write_text(code, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17', '-fsyntax-only', str(path)], capture_output=True, text=True)
        log.write_text(result.stdout + result.stderr + f'\ncompiler_exit={result.returncode}\n', encoding='utf8')
        self.assertEqual(result.returncode, 0, str(log))

    def test_view_lifecycle_and_unchanged_animation_contract(self):
        source = (VIEW / 'view_mosaico.cpp').read_text(encoding='utf8')
        def body(name):
            return source.split('CodexMicroView::' + name + '(', 1)[1].split('\nvoid CodexMicroView::', 1)[0]
        self.assertIn('_slideSnapshot.clear();', body('cancelPageSlide'))
        self.assertIn('_slideSnapshot.clear();', body('slideCompleted'))
        self.assertIn('_sheetSnapshot.clear();', body('settingsSheetCompleted'))
        self.assertLess(body('closeSettings').index('_sheetSnapshot.clear();'), body('closeSettings').index('if (!animate)'))
        for name in ('~CodexMicroView', 'setInputSuppressed', 'lockDisplay', 'refreshOta'):
            self.assertIn('closeSettings(false);', body(name))
            self.assertIn('cancelPageSlide();', body(name))
        self.assertIn('closeSettings(false); cancelPageSlide();', body('cancelOrientation'))
        for name, duration in (('navigatePage', 200), ('openSettings', 200), ('closeSettings', 180), ('settingsTileEvent', 160)):
            self.assertIn(f'lv_anim_set_duration(&anim, {duration})', body(name))
            self.assertIn('lv_anim_path_ease_out', body(name))
        self.assertIn('20 - self->_slideDirection * offset', body('slideExec'))
        self.assertIn('20 + self->_slideDirection * (480 - offset)', body('slideExec'))
        navigation = body('navigatePage')
        self.assertIn('if (_page == Page::Command) _slideSnapshot.begin(_slideTo);\n    else _slideSnapshot.begin(_slideFrom, _slideTo);', navigation)
        self.assertLess(navigation.index('setPageForDebug(pages['), navigation.index('if (_page == Page::Command) _slideSnapshot.begin'))
        self.assertLess(navigation.index('if (_page == Page::Command) _slideSnapshot.begin'), navigation.index('slideExec(this, 0);'))
        # Quota incoming alone is safe above the footer; outgoing stays live.
        single_target = re.search(r'if \(_page == Page::(\w+)\) _slideSnapshot.begin\(_slideTo\)', navigation).group(1)
        for target, captures in (('Command', 1), ('History', 2), ('OTA', 2), ('Sessions', 2)):
            self.assertEqual(1 if target == single_target else 2, captures)
        self.assertNotIn('if (_page == Page::Command) return;', navigation)
        self.assertIn('if (_settingsDetail == 0) _sheetSnapshot.beginInset(_settingsTiles)', body('openSettings'))
        self.assertIn('if (_settingsDetail == 0) _sheetSnapshot.beginInset(_settingsTiles)', body('closeSettings'))
        self.assertNotIn('_sheetSnapshot.begin(_settingsPage)', source)
        self.assertNotIn('Snapshot', body('settingsDetailExec'))


if __name__ == '__main__':
    unittest.main()
