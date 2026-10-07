"""Shared C++ primitive sampler tests; firmware and simulator share the approved primitive model."""
import hashlib,json,struct,subprocess,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
OUT=R/'.artifacts/mosaico'

class DotRenderModelTests(unittest.TestCase):
    def test_actual_samplers_bounds_stationary_fill_and_gradient(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        code='#include "'+(V/'dot_render_model.h').as_posix()+'"\n'+r'''
#include <array>
using namespace mosaico_dot;
struct Dot { int x,y,d;uint32_t color; };
struct Sink { std::array<Dot,256> dots{};int count=0;
 constexpr void dot(int x,int y,int d,uint32_t rgb){dots[count++]={x,y,d,rgb};}
};
constexpr bool iconTests() {
 for(auto icon:{render::Accent::Quota,render::Accent::Coin,render::Accent::ResetCard,render::Accent::Refresh}) {
  for(int size:{28,32,34,56,88}) {
   auto layout=render::iconLayout(icon,size,size);
   if(layout.width<size-1 || layout.height<size-1 || layout.diameter<1)return false;
   for(int phase:{0,30,45,60,90,120,135,180,240,270,300,359}) {
    Sink s;render::sampleIcon(icon,size,size,phase,true,0xe9c46a,s);
    if(!s.count || s.count>225)return false;
    for(int i=0;i<s.count;++i){auto d=s.dots[i];if(d.x<0 || d.y<0 || d.x+d.d>size || d.y+d.d>size)return false;}
   }
   Sink a,b;render::sampleIcon(icon,size,size,0,true,0xe9c46a,a);render::sampleIcon(icon,size,size,180,true,0xe9c46a,b);
   if(a.count!=b.count)return false;
   for(int i=0;i<a.count;++i)if(a.dots[i].x!=b.dots[i].x || a.dots[i].y!=b.dots[i].y)return false;
  }
 }
 // Refresh is a Euclidean circular arc: a five-dot top and a vertical side,
 // with open arrowheads and intentional gaps rather than a rectangular cluster.
 for(int x=5;x<=9;++x)if(!render::topRefreshArc(x,1))return false;
 for(int y=5;y<=7;++y)if(!render::topRefreshArc(13,y))return false;
 if(render::topRefreshArc(12,7) || render::topRefreshArc(11,6) || render::topRefreshArc(1,7))return false;
 // Exact embedded C/R glyphs have a blank right-hand middle or an R leg.
 if(render::glyphPixel('C',4,3) || !render::glyphPixel('C',0,3))return false;
 if(!render::glyphPixel('R',4,6) || !render::glyphPixel('R',0,6))return false;
 return render::iconGrid(render::Accent::Refresh)==15 && render::iconGrid(render::Accent::Coin)==13;
}
constexpr bool meterTests() {
 for(auto style:{render::SweepStyle::StrongDark,render::SweepStyle::MildDark})for(uint32_t base:{0x67e7aeU,0x68c9d6U,0xd18c37U,0xe87575U}) {
  int previous=256;
  for(int distance=0;distance<=256;++distance) {
   const int value=render::luma(render::sweepColor(base,distance,style));
   if(value>previous || value<render::luma(0x283642))return false;previous=value;
  }
  for(int distance=384;distance<=640;++distance) {
   const int value=render::luma(render::sweepColor(base,distance,style));
   if(value<previous || value<render::luma(0x283642))return false;previous=value;
  }
  if(render::luma(render::sweepColor(base,0,style))<=render::luma(base) ||
     render::luma(render::darkSkirt(base,style))>=render::luma(base) || render::sweepColor(base,640,style)!=base)return false;
 }
 if(render::luma(render::darkSkirt(0x67e7ae,render::SweepStyle::StrongDark))>=render::luma(render::darkSkirt(0x67e7ae,render::SweepStyle::MildDark)))return false;
 for(auto style:{render::SweepStyle::StrongDark,render::SweepStyle::MildDark})for(int width:{198,408,440})for(int h:{24,28})for(int bp:{0,1,100,500,5000,10000}) {
  auto g=detail::meterLayout(width,h,3);int filled=detail::filledDots(g.columns*g.rows,bp);
  Sink base;render::sampleMeter(width,h,3,bp,true,0,false,0x67e7ae,base,style);
  for(int phase:{0,60,120,180,240,300}) {
   Sink s;render::sampleMeter(width,h,3,bp,true,phase,true,0x67e7ae,s,style);
   if(s.count!=base.count || s.count!=g.columns*g.rows)return false;
   int foreground=0;
   for(int i=0;i<s.count;++i) {
    auto d=s.dots[i],a=base.dots[i];
    if(d.x!=a.x || d.y!=a.y || d.d!=a.d || d.x<0 || d.y<0 || d.x+d.d>width || d.y+d.d>h)return false;
    if(i<filled){++foreground;if(d.color==0x283642)return false;}else if(d.color!=0x283642)return false;
   }
   if(foreground!=filled)return false;
   for(int x=0;x<g.columns;++x) {
    if(!filled && render::meterColumnColor(g,bp,true,phase,true,x,0x67e7ae)!=0x67e7ae)return false;
    if(render::meterColumnColor(g,bp,false,phase,true,x,0x67e7ae)!=0x67e7ae || render::meterColumnColor(g,bp,true,phase,false,x,0x67e7ae)!=0x67e7ae)return false;
    if(x*3>=filled && render::meterColumnColor(g,bp,true,phase,true,x,0x67e7ae)!=0x67e7ae)return false;
   }
   Sink unknown;render::sampleMeter(width,h,3,bp,false,phase,true,0x67e7ae,unknown,style);
   for(int i=0;i<unknown.count;++i)if(unknown.dots[i].color!=0x69716D && unknown.dots[i].color!=0x283642)return false;
  }
 }
 auto single=detail::meterLayout(198,24,3);
 if(render::meterColumnColor(single,100,true,0,true,0,0x67e7ae)==render::meterColumnColor(single,100,true,90,true,0,0x67e7ae))return false;
 // Default phase moves right->left; reversing caller phase moves left->right.
 if(detail::wavePosition(8,60)>=detail::wavePosition(8,0))return false;
 return render::blendWhite(0,0)==0 && render::blendWhite(0,255)==0xffffff;
}
static_assert(iconTests(),"actual-size icon bounds, no floor shrink, readable front/back C/R");
static_assert(meterTests(),"stationary geometry, exact fill, white core, truly dim monotone shoulders and no unknown/empty highlights");
'''
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'samplers.cpp';source.write_text(code,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            (OUT/'dot-model-0123-tests.log').write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_export_shared_header_hash_and_primitive_only_rasterizer(self):
        manifest=json.loads((OUT/'dot-model-0123-final-preview.json').read_text())
        def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(manifest['model_sha256'],sha(V/'dot_render_model.h'))
        self.assertEqual(manifest['geometry_sha256'],sha(V/'dot_patterns.h'))
        self.assertEqual(manifest['preview_binary_sha256'],sha(OUT/'dot-model-0123-final-preview.bin'))
        data=(OUT/'dot-model-0123-final-preview.bin').read_bytes()
        magic,count=struct.unpack_from('<II',data)
        self.assertEqual(magic,0x444f5433);self.assertEqual(count,len(manifest['specs']))
        self.assertEqual(len(data),8+count*3080)
        # Positions are exported from the same compiler, not calculated by Pillow.
        simulator=(R/'tools/preview_dot_model.py').read_text(encoding='utf8')
        self.assertIn('render::sampleIcon(',simulator);self.assertIn('render::sampleMeter(',simulator)
        raster=simulator.split('def raster(',1)[1].split('def generate()',1)[0]
        self.assertIn('for x,y,d,color in primitives',raster)
        for forbidden in ('sin(', 'cos(', 'iconPixel','sweepMix','mask','remaining'):
            self.assertNotIn(forbidden,raster)
        source=(OUT/'dot-model-0123-final-preview.cpp').read_text()
        self.assertIn(str(V/'dot_render_model.h').replace('\\','/'),source)

if __name__=='__main__':unittest.main()
