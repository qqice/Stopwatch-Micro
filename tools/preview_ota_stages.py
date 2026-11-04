"""Compile production OTA presentation + dot assets into stage-only preview records.

Ordinary labels use Arial, not exact Montserrat; this is not a device screenshot.
CTA admission assumes network/power available except the WaitingPower fixture.
"""
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

R = Path(__file__).resolve().parents[1]
V = R / 'main/apps/app_codex_micro/view'
OUT = R / '.artifacts/mosaico/ota-stages-preview-001'
RECTS = 'phase badge currentBox targetBox activity percent meter byteBox hashBox signatureBox imageIcon signatureIcon slot0 slot1 arrow action'.split()
FLAGS = 'title loader numeric current target targetIsCurrent mode bytes ratio hash signature error'.split()
CASES = [
    ('Current', 'Idle', 'None', False, False),
    ('Checking', 'Checking', 'None', False, False),
    ('Offer', 'Available', 'None', False, False),
    ('Preparing', 'Downloading', 'Preparing', False, False),
    ('Negotiating', 'Downloading', 'Negotiating', False, False),
    ('DeltaDownload', 'Downloading', 'Downloading', True, True),
    ('FullDownload', 'Downloading', 'Downloading', False, True),
    ('Merging', 'Downloading', 'Merging', True, True),
    ('Verifying', 'Verifying', 'Verifying', True, True),
    ('Ready', 'ReadyInstall', 'None', True, True),
    ('Installing', 'Installing', 'None', True, True),
    ('Reboot', 'ReadyReboot', 'None', True, True),
    ('BootCheck', 'BootChecking', 'None', True, True),
    ('Complete', 'Complete', 'None', True, True),
    ('Failed', 'Failed', 'None', True, True),
    ('WaitingPower', 'WaitingPower', 'None', True, True),
]
CURRENT = '0.13.95-r62-mosaico-ota'
TARGET = '0.13.95-r63-mosaico-ota'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export():
    widgets = (V / 'dot_widgets.cpp').read_text(encoding='utf8')
    view = (V / 'view_mosaico.cpp').read_text(encoding='utf8')
    glyphs = 'constexpr detail::Glyph otaGlyphs[] = {' + widgets.split('constexpr detail::Glyph otaGlyphs[] = {', 1)[1].split('struct Motion', 1)[0]
    masks = 'constexpr uint16_t masks[][9] = {' + widgets.split('constexpr uint16_t masks[][9] = {', 1)[1].split('};', 1)[0] + '};\n'
    action_switch = view.split('void CodexMicroView::renderOtaAction() {', 1)[1].split('    switch (_ota.stage) {', 1)[1].split('    const bool enabled = otaActionEnabled();', 1)[0]
    action_switch = 'switch (s.stage) {' + action_switch
    action_switch = action_switch.replace('_ota.imageVerified', 's.imageVerified').replace('otaHasDownloadOffer()', '(s.signatureVerified && known(s.sha256))')
    # One six-frame Checking cycle; all other stages need just a single sample.
    requests = [(case, 0) for case in CASES] + [(CASES[1], phase) for phase in (60, 120, 180, 240, 300)]
    cpp = '#include "ota_presentation_model.h"\n#include "dot_render_model.h"\n#include <array>\nusing namespace mosaico_dot;\nusing namespace mosaico_ota_view;\n' + glyphs + masks
    cpp += r'''
constexpr void copy(char* out,const char* in) {while(*in)*out++=*in++;*out=0;}
struct Dot {int16_t x,y,d;uint16_t reserved;uint32_t rgb;};
struct Frame {
 uint32_t meta[16]{};Rect rects[16]{};char heading[32]{},action[16]{};
 std::array<Dot,2048> dots{};
};
struct Sink {
 Frame& f;int x=0,y=0;
 constexpr void dot(int dx,int dy,int d,uint32_t color) {
  f.dots[f.meta[11]++]={static_cast<int16_t>(x+dx),static_cast<int16_t>(y+dy),static_cast<int16_t>(d),0,color};
 }
};
constexpr void text(Frame& f,Rect r,const char* word,int pitch,uint32_t color) {
 int n=0;while(word[n])++n;
 const auto l=detail::textLayout(r.w,r.h,n,pitch);Sink sink{f,r.x,r.y};
 for(int i=0;i<n;++i)for(int y=0;y<7;++y)for(int x=0;x<5;++x)
  if(uiGlyph(word[i]).rows[y]&(1<<(4-x)))sink.dot(l.x+(i*6+x)*l.pitch,l.y+y*l.pitch,l.diameter,color);
}
constexpr void icon(Frame& f,Rect r,int index,uint32_t color,unsigned phase=0) {
 Sink sink{f,r.x,r.y};
 if(render::supports(index)) {render::sampleIcon(static_cast<render::Accent>(index),r.w,r.h,phase,true,color,sink);return;}
 const int p=detail::min(r.w/9,r.h/9),d=detail::max(1,p*7/10);
 if(p<2)return;
 const int ox=(r.w-(8*p+d))/2,oy=(r.h-(8*p+d))/2;
 for(int y=0;y<9;++y)for(int x=0;x<9;++x)if(masks[index][y]&(1<<(8-x)))
  sink.dot(ox+detail::projectedX(x,p,1000),oy+y*p,d,color);
}
constexpr MosaicoOta::UiSnapshot sample() {
 MosaicoOta::UiSnapshot s{};
 copy(s.currentVersion,"0.13.95-r62-mosaico-ota");copy(s.targetVersion,"0.13.95-r63-mosaico-ota");
 copy(s.sha256,"a12b34c56d78aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");copy(s.signatureShort,"9abc1234abcd5678");
 copy(s.error,"HASH_MISMATCH");s.currentSlot=0;s.targetSlot=1;s.signatureVerified=true;
 s.size=3852320;s.received=1926160;s.transferSize=60296;s.transferReceived=30148;s.progressBasisPoints=5000;return s;
}
constexpr const char* actionFor(MosaicoOta::UiSnapshot s) {
 using S=MosaicoOta::UiStage;const char* action="CHECK";
'''
    cpp += action_switch + ' return action;\n}\n'
    cpp += r'''
constexpr void number(unsigned n,char* s) {
 if(n>=100){*s++='0'+n/100;n%=100;*s++='0'+n/10;*s++='0'+n%10;}
 else if(n>=10){*s++='0'+n/10;*s++='0'+n%10;}else *s++='0'+n;
 *s=0;
}
constexpr Frame frame(MosaicoOta::UiSnapshot s,unsigned phase) {
 const auto m=present(s);Frame f;
 const bool flags[]={m.title,m.loader,m.numeric,m.current,m.target,m.targetIsCurrent,m.mode,m.bytes,m.ratio,m.hash,m.signature,m.error};
 uint32_t bits=0;for(unsigned i=0;i<12;++i)if(flags[i])bits|=1U<<i;
 const uint32_t values[]={m.color,bits,m.done,m.total,m.bp,m.slots,unsigned(m.screen),unsigned(s.stage),s.deltaTransfer,s.modeKnown,phase,0,unsigned(s.currentSlot),unsigned(s.targetSlot),s.imageVerified,s.signatureVerified};
 for(int i=0;i<16;++i)f.meta[i]=values[i];
 const Rect rects[]={m.phase,m.badge,m.currentBox,m.targetBox,m.activity,m.percent,m.meter,m.byteBox,m.hashBox,m.signatureBox,m.imageIcon,m.signatureIcon,m.slot[0],m.slot[1],m.arrow,m.action};
 for(int i=0;i<16;++i)f.rects[i]=rects[i];copy(f.heading,m.heading);copy(f.action,actionFor(s));
 if(m.title)text(f,{0,0,170,56},"OTA",8,0x68c9d6);
 if(m.mode)text(f,m.badge,s.deltaTransfer?"DELTA":"FULL",4,s.deltaTransfer?0x9868cd:0xd18c37);
 const bool staticIcon=m.screen==Screen::Ready||m.screen==Screen::Reboot||m.screen==Screen::Complete;
 if(m.loader||staticIcon)icon(f,m.activity,m.loader?17:14,m.color,phase);
 if(m.numeric) {
  char word[16]{};number(m.bp/100,word);int n=0;while(word[n])++n;
  const unsigned fraction=m.bp%100;
  if(fraction) {word[n++]='.';word[n++]='0'+fraction/10;if(fraction%10)word[n++]='0'+fraction%10;}
  word[n++]='%';word[n]=0;text(f,m.percent,word,8,m.color);
  Sink sink{f,m.meter.x,m.meter.y};render::sampleMeter(m.meter.w,m.meter.h,3,m.bp,m.total!=0||m.screen==Screen::BootCheck,phase,true,m.color,sink);
 }
 if(m.hash)icon(f,m.imageIcon,s.imageVerified?14:15,s.imageVerified?0x67e7ae:0xe9c46a);
 if(m.signature)icon(f,m.signatureIcon,s.signatureVerified?14:8,s.signatureVerified?0x67e7ae:0xe9c46a);
 for(unsigned i=0;i<m.slots;++i) {
  const auto r=m.slot[i];const bool compact=r.h<=48;
  icon(f,{r.x+16,r.y+(compact?21:38),compact?22:28,compact?22:28},15,i?0xe9c46a:0x68c9d6);
  char word[4]{};number(i?s.targetSlot:s.currentSlot,word);
  text(f,{r.x+72,r.y+(compact?20:30),90,compact?26:52},word,compact?4:7,i?0xe9c46a:0x68c9d6);
 }
 if(m.slots==2)icon(f,m.arrow,16,0xe9c46a);
 const bool enabled=s.stage==Stage::Idle||s.stage==Stage::Available||s.stage==Stage::ReadyInstall||s.stage==Stage::ReadyReboot||s.stage==Stage::Complete||s.stage==Stage::Failed;
 text(f,{m.action.x,m.action.y+12,440,40},f.action,4,enabled?0xd18c37:0x68451c);
 return f;
}
'''
    cpp += f'struct Export {{uint32_t magic=0x4f545332,count={len(requests)};std::array<Frame,{len(requests)}> frames{{}};}};\nconstexpr Export bake() {{Export out;\n'
    for i, (case, phase) in enumerate(requests):
        name, stage, work, delta, mode = case
        cpp += f' {{auto s=sample();s.stage=Stage::{stage};s.workPhase=Work::{work};s.deltaTransfer={str(delta).lower()};s.modeKnown={str(mode).lower()};'
        if name in ('Ready', 'Installing', 'Reboot', 'BootCheck', 'Complete', 'WaitingPower'):
            cpp += 's.imageVerified=true;s.received=s.size;'
        if name == 'Merging':
            cpp += 's.transferReceived=s.transferSize;'
        if name in ('BootCheck', 'Complete'):
            cpp += 'copy(s.currentVersion,s.targetVersion);s.currentSlot=1;s.targetSlot=0;'
        cpp += f'out.frames[{i}]=frame(s,{phase});}}\n'
    cpp += 'return out;}\nstatic_assert(sizeof(Dot)==12 && sizeof(Frame)==24944,"stable preview ABI");\n__attribute__((used,section(".preview"))) constexpr Export preview=bake();\n'
    OUT.mkdir(parents=True, exist_ok=True)
    source=OUT/'stages.cpp';source.write_text(cpp,encoding='utf8')
    compiler=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
    command=[str(compiler),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fconstexpr-ops-limit=100000000','-I'+str(V),'-I'+str(R/'main'),'-c',str(source),'-o',str(OUT/'stages.o')]
    run=subprocess.run(command,capture_output=True,text=True)
    (OUT/'compile.log').write_text(run.stdout+run.stderr,encoding='utf8')
    if run.returncode:raise RuntimeError(run.stderr)
    subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')),'-O','binary','--only-section=.preview',str(OUT/'stages.o'),str(OUT/'stages.bin')],check=True,capture_output=True)
    data=(OUT/'stages.bin').read_bytes();assert struct.unpack_from('<II',data)==(0x4f545332,len(requests))
    assert len(data)==8+len(requests)*24944
    frames=[]
    for i,(case,phase) in enumerate(requests):
        off=8+i*24944
        values=struct.unpack_from('<16I',data,off)
        color,bits,done,total,bp,slots,screen,stage,delta,mode,phase,count,cur,tgt,iv,sv=values
        rects={name:list(struct.unpack_from('<4i',data,off+64+j*16)) for j,name in enumerate(RECTS)}
        heading=data[off+320:off+352].split(b'\0')[0].decode()
        action=data[off+352:off+368].split(b'\0')[0].decode()
        dots=[]
        assert count<=2048
        for j in range(count):
            x,y,d,_,rgb=struct.unpack_from('<hhhHI',data,off+368+j*12)
            assert d>0 and x>=0 and y>=0 and x+d<=440 and y+d<=402
            dots.append([x,y,d,rgb])
        frames.append(dict(name=case[0],heading=heading,action=action,color=color,done=done,total=total,bp=bp,slots=slots,screen=screen,stage=stage,delta=bool(delta),modeKnown=bool(mode),phase=phase,currentSlot=cur,targetSlot=tgt,imageVerified=bool(iv),signatureVerified=bool(sv),rects=rects,flags={name:bool(bits&(1<<j)) for j,name in enumerate(FLAGS)},primitives=dots))
    byname={f['name']:f for f in frames[:len(CASES)]}
    assert byname['DeltaDownload']['done']==30148 and byname['DeltaDownload']['total']==60296
    assert byname['Merging']['bp']==5000 and byname['Merging']['total']==3852320
    assert not byname['Verifying']['flags']['numeric'] and byname['Verifying']['flags']['loader']
    assert not byname['Negotiating']['flags']['mode']
    manifest=dict(frames=frames,model_sha256=sha(V/'ota_presentation_model.h'),render_sha256=sha(V/'view_mosaico.cpp'),glyph_source_sha256=sha(V/'dot_widgets.cpp'),dot_model_sha256=sha(V/'dot_render_model.h'),geometry_sha256=sha(V/'dot_patterns.h'),source_sha256=sha(source),binary_sha256=sha(OUT/'stages.bin'),command=command,body_origin=[20,64],screen_size=[480,480],notes=['Presentation Rect/visibility/data exclusively from compiled present(snapshot).','Production otaGlyphs/uiGlyph and 9x9 masks extracted verbatim; actual shared sampleIcon/sampleMeter/textLayout.','CTA switch extracted from renderOtaAction; network/power admission approximated, WaitingPower disabled.','Arial ordinary labels approximate Montserrat; font fit 24->20->16->14 emulates but is not exact LVGL sizing.','Not a device screenshot or hardware validation.'])
    (OUT/'stages.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    return frames


def rgb(c):return ((c>>16)&255,(c>>8)&255,c&255)


def raster(f):
    im=Image.new('RGB',(480,480),'black');draw=ImageDraw.Draw(im)
    def label(rect,text,color,size=20,align='center',fit=False):
        x,y,w,h=rect
        sizes=([size,20,16,14] if fit else [size])
        for sz in sizes:
            font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',sz)
            b=draw.textbbox((0,0),text,font=font)
            if b[2]-b[0]<=w and b[3]-b[1]<=h:break
        tw=b[2]-b[0];th=b[3]-b[1]
        tx=x if align=='left' else x+w-tw if align=='right' else x+(w-tw)//2
        draw.text((20+tx,64+y+(h-th)//2-b[1]),text,font=font,fill=rgb(color))
    r=f['rects'];v=f['flags'];color=f['color']
    # Neutral top strip: context only, not a reproduction of live status widgets.
    draw.text((20,20),'OTA  /  presentation preview',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16),fill=(105,113,109))
    label(r['phase'],f['heading'],color,20,'right' if v['title'] else 'left')
    current=TARGET if f['name'] in ('BootCheck','Complete') else CURRENT
    if v['current']:label(r['currentBox'],TARGET if v['error'] else current,0x68c9d6,16 if r['currentBox'][3]<=28 else 24,fit=True)
    if v['target']:label(r['targetBox'],'HASH_MISMATCH' if v['error'] else current if v['targetIsCurrent'] else TARGET,0xd18c37 if v['error'] else 0xe9c46a,24,fit=True)
    if v['bytes']:
        div,unit=(1000000,'M') if f['total']>=1000000 else (1000,'K')
        text=f'{f["done"]/div:.2f} / {f["total"]/div:.2f} {unit}B' if v['ratio'] else f'{f["total"]/div:.2f} {unit}B'
        label(r['byteBox'],text,0xe9edf2,20,'right' if v['hash'] and not v['signature'] else 'center')
    if v['hash']:label(r['hashBox'],'SHA a12b34c56d78',0xe9edf2,14,'left')
    if v['signature']:label(r['signatureBox'],'SIG 9abc1234',0xe9edf2,14,'left')
    for i in range(f['slots']):
        x,y,w,h=r[f'slot{i}'];c=0xe9c46a if i else 0x68c9d6
        draw.rectangle((20+x,64+y,20+x+w-1,64+y+h-1),outline=rgb(c),width=3 if i and f['imageVerified'] else 1)
        label([x+8,y+(3 if h<=48 else 6),170,17],'NEW' if i else 'CURRENT',c,14,'left')
    for x,y,d,c in f['primitives']:
        draw.ellipse((20+x,64+y,20+x+d-1,64+y+d-1),fill=rgb(c))
    enabled=f['name'] in ('Current','Offer','Ready','Reboot','Complete','Failed')
    x,y,w,h=r['action'];c=rgb(0xd18c37 if enabled else 0x68451c)
    for a in range(1,w-1,10):
        for yy in (y+1,y+h-2):draw.line((20+x+a,64+yy,20+x+min(a+5,w-2),64+yy),fill=c,width=2)
    for a in range(1,h-1,10):
        for xx in (x+1,x+w-2):draw.line((20+xx,64+y+a,20+xx,64+y+min(a+5,h-2)),fill=c,width=2)
    return im


def generate():
    frames=export();images=[raster(f) for f in frames]
    for f,im in zip(frames,images):im.save(OUT/f'{f["name"]}-{f["phase"]:03d}.png')
    sheet=Image.new('RGB',(4*480,4*510),'#141414');draw=ImageDraw.Draw(sheet)
    for i,(f,im) in enumerate(zip(frames[:len(CASES)],images)):
        x=(i%4)*480;y=(i//4)*510
        sheet.paste(im,(x,y+25));draw.text((x+20,y+5),f['name'],fill='white')
    sheet.save(OUT/'overview.png')
    loader=[images[1]]+images[len(CASES):]
    loader[0].save(OUT/'checking.gif',save_all=True,append_images=loader[1:],duration=[270,270,260,270,270,260],loop=0,optimize=False)
    print(json.dumps(dict(frames=len(frames),overview=str(OUT/'overview.png'),manifest=str(OUT/'stages.json')),indent=2))


if __name__=='__main__':generate()

