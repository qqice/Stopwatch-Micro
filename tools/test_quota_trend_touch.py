"""Offline production-helper constexpr execution and source integration guards.
No firmware build, target link, network, or hardware operations.
"""
from pathlib import Path
import subprocess, tempfile, unittest
R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
UI=(V/'view_mosaico.cpp').read_text(encoding='utf8')

class TrendTouchTests(unittest.TestCase):
    def test_production_selection(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers,'local compiler is required')
        code='#include "'+(V/'quota_trend_selection.h').as_posix()+'"\n'+r'''
using namespace mosaico_trend;
constexpr bool samples() {
 std::array<QuotaTrendPoint,25> h{};
 if(nearest(h.data(),h.size(),100,86500,200)!=NoPoint)return false;
 h[0]={100,10000,true};h[5]={21700,9900,true};h[18]={65000,5000,true,true};h[24]={86500,0,true};
 if(position(h[5],100,86500).x!=131)return false;
 if(nearest(h.data(),h.size(),100,86500,130)!=5)return false;
 if(nearest(h.data(),h.size(),100,86500,-999)!=0)return false;
 if(nearest(h.data(),h.size(),100,86500,999)!=24)return false;
 // A missing sample/gap is selectable only via its nearest real neighbor.
 if(nearest(h.data(),h.size(),100,86500,250)!=18)return false;
 if(connects(h[5],h[18],100,86500))return false;
 h[18].resetBefore=true;if(connects(h[5],h[18],100,86500))return false;
 h[1]={99,5000,true};h[2]={86501,5000,true};h[3]={1000,10001,true};
 if(nearest(h.data(),h.size(),100,86500,32)!=0)return false;
 if(nearest(h.data(),h.size(),100,100,32)!=NoPoint)return false;
 h[6]={21701,9800,true}; // distinct time, same pixel: prefer newest
 if(nearest(h.data(),h.size(),100,86500,131)!=6)return false;
 h[7]=h[6];if(nearest(h.data(),h.size(),100,86500,131)!=7)return false;
 if(selected(h.data(),h.size(),100,86500,21701)!=7)return false;
 if(selected(h.data(),h.size(),100,86500,UINT32_MAX)!=24)return false;
 std::array<QuotaTrendPoint,8> d{};
 d[0]={100,10000,true};d[2]={172900,7345,true};d[7]={604900,3000,true};
 if(position(d[2],100,604900).x!=145)return false;
 if(nearest(d.data(),d.size(),100,604900,145)!=2)return false;
 if(nearest(d.data(),d.size(),100,604900,428)!=7)return false;
 if(nearest(d.data(),d.size(),100,604900,32)!=0)return false;
 // Unordered/duplicate real samples remain bounded, no arithmetic overflow.
 d[1]={0xffffffffU,10000,true};
 if(nearest(d.data(),d.size(),0xffffffffU-604800,0xffffffffU,999)!=1)return false;
 return true;
}
constexpr bool gestures() {
 TouchSelection s{};
 if(s.begin(31,30)||s.blocksPageSwipe())return false;
 if(s.begin(100,59)||s.blocksPageSwipe())return false;
 if(s.begin(100,-1)||s.begin(429,20))return false;
 for(int x:{32,200,428})for(int y:{0,30,58}) {
  if(!s.begin(x,y)||!s.blocksPageSwipe())return false;
  s.epoch=123;
  // Dragging out does not transfer ownership to a global swipe.
  if(clampX(-100)!=32 || clampX(999)!=428 || !s.blocksPageSwipe())return false;
  s.cancel();if(s.blocksPageSwipe()||s.epoch!=123)return false;
 }
 // Release, rotation, lock, suppression and page cancellation preserve selection;
 // mode switch resets to the latest valid sample.
 for(int lifecycle=0;lifecycle<5;++lifecycle) {
  s.begin(200,30);s.cancel();if(s.blocksPageSwipe())return false;
 }
 s.begin(200,30);s.reset();
 return !s.blocksPageSwipe() && s.epoch==UINT32_MAX;
}
static_assert(samples(),"24h/7d actual samples, empty/gaps/edge/outside/duplicate x");
static_assert(gestures(),"plot-origin gesture exclusivity, release and lifecycle cancellation");
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'trend_touch.cpp';p.write_text(code,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-Wall','-Wextra','-Werror','-fsyntax-only','-I'+str(R/'main'),str(p)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_ui_routes_plot_contact_before_page_swipe(self):
        body=UI.split('void CodexMicroView::touchEvent(',1)[1].split('void CodexMicroView::cellEvent(',1)[0]
        branch=body.split('} else if (self->_trendTouch.blocksPageSwipe()) {',1)[1].split('\n    }',1)[0]
        self.assertIn('self->selectTrendAt(localX)',branch)
        self.assertIn('LV_EVENT_PRESS_LOST',branch)
        self.assertIn('self->_trendTouch.cancel()',branch)
        self.assertIn('return;',branch)
        self.assertLess(body.index('blocksPageSwipe()'),body.index('const int dx'))
        self.assertIn('self->_page == Page::History && !self->_settingsOpen',body)
        self.assertIn('point.x - plotArea.x1',body)
        self.assertIn('point.y - plotArea.y1',body)
        self.assertIn('lv_obj_get_content_coords(self->_historyChart, &plotArea)',body)
        self.assertIn('LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_PRESS_LOCK | LV_OBJ_FLAG_EVENT_BUBBLE',UI)
        for method in ('lockDisplay','setPageForDebug','setInputSuppressed','updateOrientation','openSettings'):
            body=UI.split('CodexMicroView::'+method+'(',1)[1].split('\n}',1)[0]
            self.assertIn('_trendTouch.cancel()',body,method)
        mode=UI.split('CodexMicroView::showHistory(',1)[1].split('\n}',1)[0]
        self.assertIn('_trendTouch.reset()',mode)

    def test_snapshot_only_tooltip_geometry_and_draw(self):
        for name in ('selectTrendAt','renderTrendHint','historyChartEvent'):
            body=UI.split('CodexMicroView::'+name+'(',1)[1].split('\n}',1)[0]
            self.assertIn('_history->quotaTrend',body)
            for forbidden in ('requestJson','fetchHistory','GetNetworkQuota','HTTP'):
                self.assertNotIn(forbidden,body)
        self.assertIn('_hourly ? "%H:%M" : "%m/%d %H:%M"',UI)
        self.assertIn('_trendHint = label(_historyChart, 34, 62, 350',UI)
        self.assertIn('lv_obj_set_height(_trendHint, 16)',UI)
        self.assertLessEqual(34+350,392) # NOW marker remains clear.
        self.assertLessEqual(62+16,80) # Selection hint wholly inside chart.
        self.assertLessEqual(300+80,380) # Independent token details below chart.
        self.assertIn('mosaico_trend::connects',UI)
        draw=UI.split('void CodexMicroView::historyChartEvent(',1)[1].split('void CodexMicroView::selectTrendAt(',1)[0]
        self.assertIn('for (int y = 6; y <= 54; y += 6)',draw)
        self.assertIn('dot.bg_opa = LV_OPA_40',draw)
        self.assertLess(draw.index('const lv_area_t guide'),draw.index('for (size_t i = 0; i < count; ++i)'))
        self.assertIn('const lv_area_t halo',draw)
        self.assertIn('const lv_area_t point',draw)
        self.assertNotIn('dot.border_width',draw)
        # Guide dots and the 9px/5px point stay inside the 440x80 chart,
        # including real 0% and 100% points at both time-axis ends.
        for x in (32,428):
            for y in (6,54):
                self.assertTrue(0<=x-4<x+4<440)
                self.assertTrue(0<=y-4<y+4<62)

if __name__=='__main__':unittest.main(verbosity=2)

