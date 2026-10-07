#pragma once
#include "dot_patterns.h"

// Shared primitive samplers consumed by both the firmware LVGL sink and the
// compiler-exported simulator. The approved default sweep is StrongDark.
namespace mosaico_dot { namespace render {
enum class SweepStyle : uint8_t { StrongDark, MildDark };
enum class Accent : uint8_t { ResetCard=5, Quota=11, Coin=12, Refresh=17 };
constexpr bool supports(unsigned index) { return index==5 || index==11 || index==12 || index==17; }
constexpr uint32_t blendWhite(uint32_t color,int mix) {
    mix=detail::max(0,detail::min(255,mix));
    uint32_t out=0;
    for(int shift : {0,8,16}) {
        const int base=(color>>shift)&255;
        out|=static_cast<uint32_t>(base+((255-base)*mix+127)/255)<<shift;
    }
    return out;
}
constexpr uint32_t blend(uint32_t a,uint32_t b,int amount) {
    amount=detail::max(0,detail::min(255,amount));uint32_t out=0;
    for(int shift:{0,8,16}) {
        const int av=(a>>shift)&255,bv=(b>>shift)&255;
        out|=static_cast<uint32_t>((av*(255-amount)+bv*amount+127)/255)<<shift;
    }
    return out;
}
constexpr int luma(uint32_t color) { return (((color>>16)&255)*54+((color>>8)&255)*183+(color&255)*19)/256; }
constexpr uint32_t darkSkirt(uint32_t color,SweepStyle style) {
    const int gray=luma(color), desaturate=style==SweepStyle::StrongDark?220:140;
    const int gain=style==SweepStyle::StrongDark?128:176;
    uint32_t out=0;
    for(int shift:{0,8,16}) {
        const int channel=(color>>shift)&255;
        out|=static_cast<uint32_t>(((channel*(255-desaturate)+gray*desaturate+127)/255)*gain/255)<<shift;
    }
    const int floor=detail::max(0,72-luma(out));
    if(floor) {
        uint32_t raised=0;for(int shift:{0,8,16})raised|=static_cast<uint32_t>(detail::min(255,((out>>shift)&255)+floor))<<shift;
        out=raised;
    }
    return out;
}
constexpr uint32_t sweepColor(uint32_t color,int distance256,SweepStyle style) {
    if(distance256<0)distance256=-distance256;
    if(distance256>=640)return color;
    if(distance256<=128)return blendWhite(color,232-distance256*52/128);
    const auto skirt=darkSkirt(color,style);
    if(distance256<=256)return blend(blendWhite(color,180),skirt,(distance256-128)*255/128);
    if(distance256<=384)return skirt;
    return blend(skirt,color,(distance256-384)*255/256); // Dark shoulders gradually recover the normal base.
}
constexpr uint32_t meterColumnColor(detail::Grid g,uint16_t bp,bool known,uint16_t phase,bool enabled,int column,
                                  uint32_t color,SweepStyle style=SweepStyle::StrongDark) {
    const int filled=known?detail::filledDots(g.columns*g.rows,bp):0;
    const int columns=filled?(filled+g.rows-1)/g.rows:0;
    if(!enabled || !columns || column<0 || column>=columns)return color;
    if(columns==1)return blendWhite(color,48+detail::pulse(phase)*184/80);
    const int circumference=columns*256;
    int distance=column*256-detail::wavePosition(columns,phase);
    if(distance<0)distance=-distance;
    distance=detail::min(distance,circumference-distance);
    return sweepColor(color,distance,style);
}
// Sink receives the exact circle/rounded-rectangle primitives used by both
// preview export and (after approval) LVGL. No canvas, allocation or timer.
template<typename Sink>
constexpr void sampleMeter(int w,int h,int rows,uint16_t bp,bool known,uint16_t phase,bool enabled,uint32_t color,Sink& sink,SweepStyle style=SweepStyle::StrongDark) {
    const auto g=detail::meterLayout(w,h,rows);
    for(int x=0;x<g.columns;++x) {
        const uint32_t foreground=known?meterColumnColor(g,bp,known,phase,enabled,x,color,style):0x69716D;
        for(int y=0;y<g.rows;++y) {
            const bool lit=detail::meterLit(x,y,g,bp,known);
            sink.dot(g.x+x*g.pitch,g.y+y*g.pitch,g.diameter,lit?foreground:0x283642); // Stationary coordinates.
        }
    }
}
constexpr bool glyphPixel(char glyph,int x,int y) {
    if(x<0 || x>=5 || y<0 || y>=7)return false;
    constexpr uint8_t r[]={30,17,17,30,20,18,17};
    const auto row=glyph=='R'?r[y]:detail::glyph('C').rows[y];
    return row&(1U<<(4-x));
}
constexpr bool topRefreshArc(int x,int y) {
    const int dx=x-7,dy=y-7,radius=dx*dx+dy*dy;
    const bool arc=radius>=32 && radius<=40 && y<=6 && !(x<=2 && y>=5);
    // Open arrowhead, not a dense rectangular cluster. The opposing arc leaves
    // a two-row gap beyond its tip; Euclidean arc has a five-dot top plateau.
    return arc || (x==10 && y==6) || (x==11 && y==7) || (x==12 && y==8) ||
           (x==13 && y==7) || (x==14 && y==6);
}
constexpr int iconGrid(Accent icon) { return icon==Accent::Refresh?15:13; }
constexpr bool iconPixel(Accent icon,int x,int y) {
    if(icon==Accent::Refresh)return topRefreshArc(x,y) || topRefreshArc(14-x,14-y);
    if(icon==Accent::Coin) {
        const int dx=x-6,dy=y-6,radius=dx*dx+dy*dy;
        return (radius>=29 && radius<=40) || glyphPixel('C',x-4,y-3);
    }
    if(icon==Accent::ResetCard) {
        const bool frame=((y==0 || y==12) && x>=2 && x<=10) ||
            ((x==0 || x==12) && y>=2 && y<=10) || ((y==1 || y==11) && (x==1 || x==11));
        return frame || glyphPixel('R',x-4,y-3);
    }
    // Wallet, with a protruding bill and a buttoned side clasp. No circular
    // gauge/clock rim or clock hands can be confused with the allowance icon.
    return (y==0 && x>=3 && x<=9) || (y==1 && (x==2 || x==10)) ||
           (y==2 && (x==1 || x==11)) || ((y==3 || y==12) && x>=1 && x<=11) ||
           (x==1 && y>=4 && y<=11) || (x==11 && (y==4 || y==5 || y==10 || y==11)) ||
           ((y==6 || y==9) && x>=7 && x<=12) ||
           ((y==7 || y==8) && (x==7 || x==9 || x==12)) ||
           ((y==6 || y==9) && x>=3 && x<=5);
}
struct IconLayout { int grid,pitch256,diameter,width,height,x,y; };
constexpr IconLayout iconLayout(Accent icon,int w,int h) {
    const int n=iconGrid(icon),size=detail::min(w,h);
    if(size<n)return {n,0,0,0,0,0,0};
    const int d=detail::max(1,(size*7+n*5)/(n*10));
    const int step=(size-d)*256/(n-1);
    const int span=(n-1)*step/256+d;
    return {n,step,d,span,span,(w-span)/2,(h-span)/2};
}
constexpr int iconX(IconLayout g,int column,int scale) {
    const int centre=(g.grid-1)*g.pitch256/2;
    return g.x+(centre+(column*g.pitch256-centre)*scale/1000)/256;
}
constexpr int accentScale(Accent icon,uint16_t phase,bool enabled) {
    if(!enabled || (icon!=Accent::Coin && icon!=Accent::ResetCard))return 1000;
    const int scale=detail::flipScale(phase);return scale<0?-scale:scale;
}
constexpr uint32_t accentColor(Accent icon,uint16_t phase,bool enabled,uint32_t color) {
    return icon==Accent::Refresh && enabled?blendWhite(color,detail::pulse(phase)):color;
}
constexpr bool meterChanged(int w,int h,int rows,uint16_t bp,bool known,uint16_t before,bool beforeEnabled,
                            uint16_t after,bool afterEnabled,uint32_t color) {
    const auto g=detail::meterLayout(w,h,rows);
    const int filled=known?detail::filledDots(g.columns*g.rows,bp):0;
    for(int x=0;x*g.rows<filled;++x)
        if(meterColumnColor(g,bp,known,before,beforeEnabled,x,color)!=meterColumnColor(g,bp,known,after,afterEnabled,x,color))return true;
    return false;
}
constexpr bool iconChanged(Accent icon,int w,int h,uint16_t before,bool beforeEnabled,uint16_t after,bool afterEnabled,uint32_t color) {
    const auto g=iconLayout(icon,w,h);if(!g.diameter)return false;
    if(accentColor(icon,before,beforeEnabled,color)!=accentColor(icon,after,afterEnabled,color))return true;
    const int a=accentScale(icon,before,beforeEnabled),b=accentScale(icon,after,afterEnabled);
    if(a==b)return false;
    for(int x=0;x<g.grid;++x)if(iconX(g,x,a)!=iconX(g,x,b))
        for(int y=0;y<g.grid;++y)if(iconPixel(icon,x,y))return true;
    return false;
}
template<typename Sink>
constexpr void sampleIcon(Accent icon,int w,int h,uint16_t phase,bool enabled,uint32_t color,Sink& sink) {
    const auto g=iconLayout(icon,w,h);
    if(!g.diameter)return;
    const int scale=accentScale(icon,phase,enabled); // C/R read the same on both faces.
    const uint32_t rgb=accentColor(icon,phase,enabled,color);
    for(int y=0;y<g.grid;++y)for(int x=0;x<g.grid;++x)if(iconPixel(icon,x,y))
        sink.dot(iconX(g,x,scale),g.y+y*g.pitch256/256,g.diameter,rgb);
}
struct BoundsSink {
    int w,h,count=0;bool valid=true;
    constexpr void dot(int x,int y,int d,uint32_t) { ++count;valid=valid && d>0 && x>=0 && y>=0 && x+d<=w && y+d<=h; }
};
constexpr bool modelTest() {
    for(auto icon:{Accent::Quota,Accent::Coin,Accent::ResetCard,Accent::Refresh})for(int size:{28,32,88})for(int phase:{0,45,90,180}) {
        BoundsSink s{size,size};sampleIcon(icon,size,size,phase,true,0xe9c46a,s);
        if(!s.valid || !s.count || s.count>225)return false;
    }
    for(int w:{198,408,440})for(int h:{24,28})for(int phase:{0,60}) {
        BoundsSink s{w,h};sampleMeter(w,h,3,5000,true,phase,true,0x67e7ae,s);
        if(!s.valid || !s.count || s.count>200)return false;
    }
    return luma(darkSkirt(0x67e7ae,SweepStyle::StrongDark))<luma(0x67e7ae) &&
        luma(darkSkirt(0x67e7ae,SweepStyle::StrongDark))>luma(0x283642) &&
        !meterChanged(198,24,3,0,true,0,true,60,true,0x67e7ae) &&
        !iconChanged(Accent::Coin,28,28,0,true,180,true,0xe9c46a);
}
static_assert(modelTest(),"shared icon/sweep geometry, honest empty fill and readable symmetric coin faces");
} }
