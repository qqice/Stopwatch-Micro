"""Compile production OTA presentation + dot assets into stage-only preview records.

Ordinary labels use Arial, not exact Montserrat; this is not a device screenshot.
CTA predicates are extracted from production with fixture network/power inputs.
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
OUT = R / '.artifacts/mosaico/ota-stages-preview-003'
HEADER = None
RECTS = 'phase badge currentBox targetBox activity percent meter byteBox hashBox signatureBox imageIcon signatureIcon slot0 slot1 arrow action slot0_name slot0_chip slot0_number slot1_name slot1_chip slot1_number'.split()
FLAGS = 'title loader numeric current target targetIsCurrent mode bytes ratio hash signature error phaseCentered actionEnabled'.split()
CURRENT = '0.13.95-r62-mosaico-ota'
TARGET = '0.13.95-r63-mosaico-ota'


def fixture(name, stage, work='None', delta=False, mode=False, **extra):
    item = dict(name=name, stage=stage, work=work, delta=delta, mode=mode,
                currentVersion=CURRENT, targetVersion=TARGET, sha256='a12b34c56d78'+'a'*52,
                signatureShort='9abc1234abcd5678', error='HASH_MISMATCH',
                currentSlot=0, targetSlot=1, signatureVerified=True, imageVerified=False,
                size=3852320, received=1926160, transferSize=60296, transferReceived=30148,
                progressBasisPoints=5000, network=True, power=True, approved=False)
    item.update(extra)
    return item


UNBOUND = dict(targetVersion='', targetSlot=-1, signatureVerified=False, sha256='', signatureShort='')
VERIFIED = dict(imageVerified=True, received=3852320)
CASES = [
    fixture('Current', 'Idle', **UNBOUND),
    fixture('Checking', 'Checking', **UNBOUND),
    fixture('Offer', 'Available'),
    fixture('PreparingBound', 'Downloading', 'Preparing'),
    fixture('PreparingUnbound', 'Downloading', 'Preparing', **UNBOUND),
    fixture('Negotiating', 'Downloading', 'Negotiating'),
    fixture('DeltaDownload', 'Downloading', 'Downloading', True, True),
    fixture('FullDownload', 'Downloading', 'Downloading', False, True),
    fixture('DeltaDownloadZero', 'Downloading', 'Downloading', True, True, transferReceived=0),
    fixture('FullDownloadComplete', 'Downloading', 'Downloading', False, True, received=3852320),
    fixture('Merging', 'Downloading', 'Merging', True, True, transferReceived=60296),
]
for label, stage, work in [('Verify', 'Verifying', 'Verifying'), ('Ready', 'ReadyInstall', 'None'),
                           ('Installing', 'Installing', 'None'), ('Reboot', 'ReadyReboot', 'None')]:
    for delta in (True, False):
        CASES.append(fixture(('Delta' if delta else 'Full')+label, stage, work, delta, True,
                             **(VERIFIED if label!='Verify' else dict(received=3852320))))
CASES += [
    fixture('BootCheck', 'BootChecking', currentVersion=TARGET, currentSlot=1, targetSlot=-1, **VERIFIED),
    fixture('Complete', 'Complete', currentVersion=TARGET, currentSlot=1, targetSlot=-1, **VERIFIED),
    fixture('FailedDelta', 'Failed', delta=True, mode=True),
    fixture('FailedFull', 'Failed', delta=False, mode=True),
    fixture('FailedBeforeMode', 'Failed', error='DISCOVERY_TIMEOUT', **UNBOUND),
    fixture('WaitingInstallPowerDelta', 'WaitingPower', delta=True, mode=True, power=False, **VERIFIED),
    fixture('WaitingInstallPowerFull', 'WaitingPower', delta=False, mode=True, power=False, **VERIFIED),
    fixture('DownloadBlocked', 'WaitingPower', network=False),
    fixture('CheckRequired', 'WaitingPower', network=False, **UNBOUND),
]



def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export():
    widgets = (V / 'dot_widgets.cpp').read_text(encoding='utf8')
    view = (V / 'view_mosaico.cpp').read_text(encoding='utf8')
    glyphs = 'constexpr detail::Glyph otaGlyphs[] = {' + widgets.split('constexpr detail::Glyph otaGlyphs[] = {', 1)[1].split('struct Motion', 1)[0]
    masks = 'constexpr uint16_t masks[][9] = {' + widgets.split('constexpr uint16_t masks[][9] = {', 1)[1].split('};', 1)[0] + '};\n'
    action_switch = view.split('void CodexMicroView::renderOtaAction() {', 1)[1].split('    switch (_ota.stage) {', 1)[1].split('    const bool enabled = otaActionEnabled();', 1)[0]
    action_switch = 'switch (s.stage) {' + action_switch
    action_switch = action_switch.replace('_ota.imageVerified', 's.imageVerified').replace('otaHasDownloadOffer()', 'hasOffer(s)')
    offer_body = view.split('bool CodexMicroView::otaHasDownloadOffer() const {', 1)[1].split('\n}', 1)[0].replace('_ota.', 's.')
    enabled_body = view.split('bool CodexMicroView::otaActionEnabled() const {', 1)[1].split('\n}', 1)[0]
    enabled_body = enabled_body.replace('_otaApproved', 'approved').replace('_otaNetworkReady', 'network').replace('_externalPowerReady', 'power').replace('_ota.', 's.')
    # The header uses the production battery-specific normal/full fixture,
    # not the generic source mask (firmware replaces that outline at draw time).
    battery_body = widgets.split('} else if (s->icon == Icon::Battery) {', 1)[1].split('            dsc.bg_color', 1)[0]
    battery_body = battery_body.rsplit('}', 1)[0].replace('s->level * 100', '10000')
    # One six-frame Checking cycle; all other stages need just a single sample.
    requests = [(case, 0) for case in CASES] + [(CASES[1], phase) for phase in (60, 120, 180, 240, 300)]
    cpp = '#include "ota_presentation_model.h"\n#include "lock_session_geometry.h"\n#include "dot_render_model.h"\n#include <array>\nusing namespace mosaico_dot;\nusing namespace mosaico_ota_view;\n' + glyphs + masks
    cpp += 'constexpr bool hasOffer(MosaicoOta::UiSnapshot s) {'+offer_body+'\n}\n'
    cpp += 'constexpr bool enabledFor(MosaicoOta::UiSnapshot s,bool network,bool power,bool approved) {'+enabled_body+'\n}\n'
    cpp += r'''
constexpr void copy(char* out,const char* in) {while(*in)*out++=*in++;*out=0;}
struct Dot {int16_t x,y,d;uint16_t reserved;uint32_t rgb;};
struct Frame {
 uint32_t meta[16]{};Rect rects[22]{};char heading[32]{},action[16]{};
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
 for(int y=0;y<9;++y)for(int x=0;x<9;++x) {
  bool lit=(masks[index][y]&(1<<(8-x)))!=0;
  if(index==2) { BATTERY_BODY }
  if(lit)sink.dot(ox+detail::projectedX(x,p,1000),oy+y*p,d,color);
 }
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
    cpp = cpp.replace('BATTERY_BODY', battery_body)
    cpp += action_switch + ' return action;\n}\n'
    cpp += r'''
constexpr void number(unsigned n,char* s) {
 if(n>=100){*s++='0'+n/100;n%=100;*s++='0'+n/10;*s++='0'+n%10;}
 else if(n>=10){*s++='0'+n/10;*s++='0'+n%10;}else *s++='0'+n;
 *s=0;
}
constexpr Frame frame(MosaicoOta::UiSnapshot s,unsigned phase,bool network,bool power,bool approved) {
 const auto m=present(s);Frame f;const bool enabled=enabledFor(s,network,power,approved);
 const bool flags[]={m.title,m.loader,m.numeric,m.current,m.target,m.targetIsCurrent,m.mode,m.bytes,m.ratio,m.hash,m.signature,m.error,m.phaseCentered,enabled};
 uint32_t bits=0;for(unsigned i=0;i<14;++i)if(flags[i])bits|=1U<<i;
 const uint32_t values[]={m.color,bits,m.done,m.total,m.bp,m.slots,unsigned(m.screen),unsigned(s.stage),s.deltaTransfer,s.modeKnown,phase,0,unsigned(s.currentSlot),unsigned(s.targetSlot),s.imageVerified,s.signatureVerified};
 for(int i=0;i<16;++i)f.meta[i]=values[i];
 const Rect rects[]={m.phase,m.badge,m.currentBox,m.targetBox,m.activity,m.percent,m.meter,m.byteBox,m.hashBox,m.signatureBox,m.imageIcon,m.signatureIcon,m.slot[0],m.slot[1],m.arrow,m.action};
 for(int i=0;i<16;++i)f.rects[i]=rects[i];copy(f.heading,m.heading);copy(f.action,actionFor(s));
 text(f,m.phase,m.heading,m.phasePitch,m.color);
 if(m.mode)text(f,m.badge,s.deltaTransfer?"DELTA":"FULL",4,s.deltaTransfer?0x9868cd:0xd18c37);
 const bool staticIcon=m.screen==Screen::Ready||m.screen==Screen::Reboot||m.screen==Screen::Complete||m.screen==Screen::Failed;
 if(m.loader||staticIcon)icon(f,m.activity,m.loader?17:m.error?18:14,m.color,phase);
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
  const auto r=m.slot[i];const auto c=slotContent(r);
  const Rect names{r.x+c.name.x,r.y+c.name.y,c.name.w,c.name.h};
  const Rect chip{r.x+c.chip.x,r.y+c.chip.y,c.chip.w,c.chip.h};
  const Rect num{r.x+c.number.x,r.y+c.number.y,c.number.w,c.number.h};
  f.rects[16+i*3]=names;f.rects[17+i*3]=chip;f.rects[18+i*3]=num;
  icon(f,chip,15,i?0xe9c46a:0x68c9d6);
  char word[4]{};number(i?s.targetSlot:s.currentSlot,word);
  text(f,num,word,c.pitch,i?0xe9c46a:0x68c9d6);
 }
 if(m.slots==2)icon(f,m.arrow,16,0xe9c46a);
 text(f,{m.action.x,m.action.y+12,440,40},f.action,4,enabled?0xd18c37:0x68451c);
 return f;
}
'''
    cpp += f'struct Export {{uint32_t magic=0x4f545332,count={len(requests)};std::array<Frame,{len(requests)}> frames{{}};}};\nconstexpr Export bake() {{Export out;\n'
    for i, (case, phase) in enumerate(requests):
        cpp += ' {auto s=sample();'
        cpp += f's.stage=Stage::{case["stage"]};s.workPhase=Work::{case["work"]};s.deltaTransfer={str(case["delta"]).lower()};s.modeKnown={str(case["mode"]).lower()};'
        for key in ('currentVersion','targetVersion','sha256','signatureShort','error'):
            cpp += f'copy(s.{key},{json.dumps(case[key])});'
        for key in ('currentSlot','targetSlot','signatureVerified','imageVerified','size','received','transferSize','transferReceived','progressBasisPoints'):
            value=str(case[key]).lower()
            cpp += f's.{key}={value};'
        cpp += f'out.frames[{i}]=frame(s,{phase},{str(case["network"]).lower()},{str(case["power"]).lower()},{str(case["approved"]).lower()});}}\n'
    cpp += 'return out;}\nstatic_assert(sizeof(Dot)==12 && sizeof(Frame)==25040,"stable preview ABI");\n__attribute__((used,section(".preview"))) constexpr Export preview=bake();\n'
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',20)
    bbox=font.getbbox('100%*');header_width=bbox[2]-bbox[0]
    cpp += f"constexpr Frame headerFrame() {{Frame f;auto r=mosaico_lock_sessions::awakeBatteryRow({header_width},24);f.rects[0]={{r.iconX,20,32,32}};f.rects[1]={{r.textX,r.textY,r.textWidth,24}};f.rects[2]={{r.boltX,24,24,24}};icon(f,f.rects[0],2,0x67e7ae);icon(f,f.rects[2],3,0xe9c46a);return f;}}\n__attribute__((used,section(\".status_header\"))) constexpr Frame header=headerFrame();\n"
    OUT.mkdir(parents=True, exist_ok=True)
    source=OUT/'stages.cpp';source.write_text(cpp,encoding='utf8')
    compiler=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
    command=[str(compiler),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fconstexpr-ops-limit=100000000','-I'+str(V),'-I'+str(R/'main'),'-c',str(source),'-o',str(OUT/'stages.o')]
    run=subprocess.run(command,capture_output=True,text=True)
    (OUT/'compile.log').write_text(run.stdout+run.stderr,encoding='utf8')
    if run.returncode:raise RuntimeError(run.stderr)
    subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')),'-O','binary','--only-section=.preview',str(OUT/'stages.o'),str(OUT/'stages.bin')],check=True,capture_output=True)
    global HEADER
    subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')),'-O','binary','--only-section=.status_header',str(OUT/'stages.o'),str(OUT/'header.bin')],check=True,capture_output=True)
    hb=(OUT/'header.bin').read_bytes();assert len(hb)==25040
    hn=struct.unpack_from('<16I',hb)[11]
    HEADER={'rects':[list(struct.unpack_from('<4i',hb,64+j*16)) for j in range(3)],'dots':[]}
    for j in range(hn):
        x,y,d,_,c=struct.unpack_from('<hhhHI',hb,464+j*12);assert 0<=x and x+d<=480 and 0<=y and y+d<=64
        HEADER['dots'].append([x,y,d,c])
    data=(OUT/'stages.bin').read_bytes();assert struct.unpack_from('<II',data)==(0x4f545332,len(requests))
    assert len(data)==8+len(requests)*25040
    frames=[]
    for i,(case,phase) in enumerate(requests):
        off=8+i*25040
        values=struct.unpack_from('<16I',data,off)
        color,bits,done,total,bp,slots,screen,stage,delta,mode,phase,count,cur,tgt,iv,sv=values
        rects={name:list(struct.unpack_from('<4i',data,off+64+j*16)) for j,name in enumerate(RECTS)}
        heading=data[off+416:off+448].split(b'\0')[0].decode()
        action=data[off+448:off+464].split(b'\0')[0].decode()
        dots=[]
        assert count<=2048
        for j in range(count):
            x,y,d,_,rgb=struct.unpack_from('<hhhHI',data,off+464+j*12)
            assert d>0 and x>=0 and y>=0 and x+d<=440 and y+d<=402
            dots.append([x,y,d,rgb])
        frames.append(dict(name=case['name'],fixture=case,heading=heading,action=action,color=color,done=done,total=total,bp=bp,slots=slots,screen=screen,stage=stage,delta=bool(delta),modeKnown=bool(mode),phase=phase,currentSlot=cur,targetSlot=tgt,imageVerified=bool(iv),signatureVerified=bool(sv),rects=rects,flags={name:bool(bits&(1<<j)) for j,name in enumerate(FLAGS)},primitives=dots))
    byname={f['name']:f for f in frames[:len(CASES)]}
    assert {f['screen'] for f in byname.values()}==set(range(14))
    assert byname['DeltaDownload']['done']==30148 and byname['DeltaDownload']['total']==60296
    assert byname['Merging']['bp']==5000 and byname['Merging']['total']==3852320
    assert all(not byname[n]['flags']['numeric'] and byname[n]['flags']['loader'] for n in ('DeltaVerify','FullVerify'))
    assert all(byname[n]['flags']['actionEnabled'] for n in ('DeltaReady','FullReady','DeltaReboot','FullReboot'))
    assert not byname['PreparingUnbound']['flags']['target'] and byname['PreparingUnbound']['slots']==1
    assert all(not byname[n]['flags']['mode'] for n in ('BootCheck','Complete','FailedBeforeMode','CheckRequired'))
    assert byname['DeltaDownloadZero']['bp']==0 and byname['FullDownloadComplete']['bp']==10000
    assert not byname['Negotiating']['flags']['mode']
    manifest=dict(frames=frames,model_sha256=sha(V/'ota_presentation_model.h'),render_sha256=sha(V/'view_mosaico.cpp'),glyph_source_sha256=sha(V/'dot_widgets.cpp'),dot_model_sha256=sha(V/'dot_render_model.h'),geometry_sha256=sha(V/'dot_patterns.h'),source_sha256=sha(source),binary_sha256=sha(OUT/'stages.bin'),command=command,body_origin=[20,64],screen_size=[480,480],header_layout=HEADER,header_geometry_sha256=sha(V/'lock_session_geometry.h'),notes=['Presentation Rect/visibility/data exclusively from compiled present(snapshot).','Production otaGlyphs/uiGlyph and 9x9 masks extracted verbatim; actual shared sampleIcon/sampleMeter/textLayout.','CTA switch and enabled/offer predicates extracted from production; fixture network/power/approved inputs are explicit.','Arial ordinary labels approximate Montserrat; font fit 24->20->16->14 emulates but is not exact LVGL sizing.','Not a device screenshot or hardware validation.'])
    (OUT/'stages.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    (OUT/'validation.log').write_text(f'PASS: {len(CASES)} fixture cases / {len(frames)} compiled records; 14 presentation screens covered; point bounds; true delta/full/merge denominators; zero/fifty/full percent; mode visibility; signed/unbound targets; production CTA predicates; full-mode Ready/Reboot enabled.\n',encoding='utf8')
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
    # Fixed-right battery example uses the compiled header layout and source masks.
    for x,y,d,c in HEADER['dots']:draw.ellipse((x,y,x+d-1,y+d-1),fill=rgb(c))
    tx,ty,tw,th=HEADER['rects'][1];font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',20)
    b=draw.textbbox((0,0),'100%*',font=font);draw.text((tx+tw-(b[2]-b[0]),ty+(th-(b[3]-b[1]))//2-b[1]),'100%*',font=font,fill=rgb(0x67e7ae))
    data=f['fixture'];current=data['currentVersion']
    if v['current']:label(r['currentBox'],current,0x68c9d6,16 if r['currentBox'][3]<=28 else 24,fit=True)
    if v['target']:label(r['targetBox'],data['error'] if v['error'] else current if v['targetIsCurrent'] else data['targetVersion'],color if v['error'] else 0xe9c46a,24,fit=True)
    if v['bytes']:
        div,unit=(1000000,'M') if f['total']>=1000000 else (1000,'K')
        text=f'{f["done"]/div:.2f} / {f["total"]/div:.2f} {unit}B' if v['ratio'] else f'{f["total"]/div:.2f} {unit}B'
        label(r['byteBox'],text,0xe9edf2,20,'right' if v['hash'] and not v['signature'] else 'center')
    if v['hash']:label(r['hashBox'],'SHA '+data['sha256'][:12],0xe9edf2,14,'left')
    if v['signature']:label(r['signatureBox'],'SIG '+data['signatureShort'][:8],0xe9edf2,14,'left')
    for i in range(f['slots']):
        x,y,w,h=r[f'slot{i}'];c=0xe9c46a if i else 0x68c9d6
        draw.rectangle((20+x,64+y,20+x+w-1,64+y+h-1),outline=rgb(c),width=3 if i and f['imageVerified'] else 1)
        label(r[f'slot{i}_name'],'NEW' if i else 'CURRENT',c,14,'center')
    for x,y,d,c in f['primitives']:
        draw.ellipse((20+x,64+y,20+x+d-1,64+y+d-1),fill=rgb(c))
    enabled=v['actionEnabled']
    x,y,w,h=r['action'];c=rgb(0xd18c37 if enabled else 0x68451c)
    for a in range(1,w-1,10):
        for yy in (y+1,y+h-2):draw.line((20+x+a,64+yy,20+x+min(a+5,w-2),64+yy),fill=c,width=2)
    for a in range(1,h-1,10):
        for xx in (x+1,x+w-2):draw.line((20+xx,64+y+a,20+xx,64+y+min(a+5,h-2)),fill=c,width=2)
    return im


def generate():
    frames=export();images=[raster(f) for f in frames]
    for f,im in zip(frames,images):im.save(OUT/f'{f["name"]}-{f["phase"]:03d}.png')
    sheet=Image.new('RGB',(4*480,((len(CASES)+3)//4)*510),'#141414');draw=ImageDraw.Draw(sheet)
    for i,(f,im) in enumerate(zip(frames[:len(CASES)],images)):
        x=(i%4)*480;y=(i//4)*510
        sheet.paste(im,(x,y+25));draw.text((x+20,y+5),f['name'],fill='white')
    sheet.save(OUT/'overview.png')
    key_steps=['Current','Offer','Negotiating','DeltaDownload','Merging','DeltaReady','DeltaInstalling','Complete']
    lookup={f['name']:im for f,im in zip(frames,images)}
    flow=Image.new('RGB',(4*480,2*510),'#141414');flowdraw=ImageDraw.Draw(flow)
    for i,name in enumerate(key_steps):
        x=(i%4)*480;y=(i//4)*510
        flow.paste(lookup[name],(x,y+25));flowdraw.text((x+20,y+5),f'{i+1}. {name}',fill='white')
    flow.save(OUT/'flow.png')
    cards=''.join(f'<figure><figcaption>{f["name"]}</figcaption><img loading="lazy" width="480" height="480" src="{f["name"]}-000.png"></figure>' for f in frames[:len(CASES)])
    (OUT/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>OTA state preview</title><style>body{background:#151515;color:#eee;font:16px sans-serif}main{display:flex;flex-wrap:wrap}figure{margin:8px}figcaption{padding:8px}</style><h1>OTA all-state asset preview</h1><p>Actual production presentation/dot model. Arial ordinary labels approximate Montserrat. Not device screenshots.</p><p><a href="overview.png">Overview</a> | <a href="flow.png">8-step flow</a> | <a href="checking.gif">Activity cycle</a></p><main>'+cards+'</main>',encoding='utf8')

    loader=[images[1]]+images[len(CASES):]
    loader[0].save(OUT/'checking.gif',save_all=True,append_images=loader[1:],duration=[270,270,260,270,270,260],loop=0,optimize=False)
    print(json.dumps(dict(frames=len(frames),overview=str(OUT/'overview.png'),manifest=str(OUT/'stages.json')),indent=2))


if __name__=='__main__':generate()

