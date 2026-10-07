"""Export shared C++ dot primitives with the existing target compiler, rasterize only.

This does not build/link firmware or edit its renderer. PNG/GIF geometry, masks,
RGB colours and phase samples come solely from dot_render_model.h (.preview).
Pillow only fills the exported circle primitives and composes labelled sheets.
"""
import hashlib, json, struct, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
OUT=R/'.artifacts/mosaico'
PHASES=(0,60,120,180,240,300)
COLORS={'Quota':0x67E7AE,'Coin':0xE9C46A,'ResetCard':0xB399F7,'Refresh':0xE9C46A,
        'green':0x67E7AE,'cyan':0x68C9D6,'ota':0xD18C37}
ACCENTS={'Quota':11,'Coin':12,'ResetCard':5,'Refresh':17}
BG=(0,0,0)
SUFFIX="-final"

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def export():
    specs=[];lookup={}
    def add(kind,name,w,h,phase=0,bp=10000,known=True,enabled=True,reverse=False,style=0):
        key=(kind,name,w,h,phase,bp,known,enabled,reverse,style)
        if key in lookup:return lookup[key]
        lookup[key]=len(specs)
        specs.append(dict(kind=kind,name=name,w=w,h=h,phase=phase,bp=bp,known=known,enabled=enabled,reverse=reverse,style=style))
        return lookup[key]
    for name in ACCENTS:
        for size in (28,32,34,56,88):add('icon',name,size,size)
    for name in ('Coin','ResetCard'):
        for phase in (0,45,90,135,180,225,270,315):add('icon',name,28,28,phase)
        for phase in range(0,360,10):add('icon',name,28,28,phase)
    for phase in range(0,360,10):add('icon','Refresh',88,88,phase)
    for name,w,h in (('green',198,24),('cyan',198,24),('ota',440,28),('green',408,24)):
        for bp in (0,500,5000,10000):
            for phase in PHASES:add('meter',name,w,h,phase,bp,reverse=name=='ota')
    for phase in range(0,360,10):
        for name in ('green','cyan'):
            add('meter',name,198,24,phase,5000)
            add('meter',name,198,24,phase,100) # One filled column: intensity pulse, no travel.
        add('meter','ota',440,28,phase,5000,reverse=True)
    for known,enabled in ((False,True),(True,False)):
        for phase in PHASES:add('meter','green',198,24,phase,5000,known,enabled)
    for phase in range(0,360,10):
        for name,w,h in (('green',198,24),('cyan',198,24),('ota',440,28)):
            add('meter',name,w,h,phase,5000,reverse=name=='ota',style=1)
    entries=[]
    for s in specs:
        entries.append('{'+','.join(map(str,(int(s['kind']=='meter'),ACCENTS.get(s['name'],0),s['w'],s['h'],s['bp'],s['phase'],
                    int(s['known']),int(s['enabled']),int(s['reverse']),COLORS[s['name']],s['style'])))+'}')
    cpp=f'''#include "{(V/'dot_render_model.h').as_posix()}"
#include <array>
#include <cstdint>
struct Dot {{ int16_t x,y,d; uint16_t reserved; uint32_t rgb; }};
struct Frame {{ uint16_t w=0,h=0,count=0,reserved=0; std::array<Dot,256> commands{{}};
 constexpr void dot(int x,int y,int d,uint32_t rgb) {{ commands[count++]={{static_cast<int16_t>(x),static_cast<int16_t>(y),static_cast<int16_t>(d),0,rgb}}; }}
}};
struct Request {{ unsigned meter,accent,w,h,bp,phase,known,enabled,reverse,color,style; }};
constexpr std::array<Request,{len(specs)}> requests={{{{{','.join(entries)}}}}};
struct Export {{ uint32_t magic=0x444f5433,count={len(specs)};std::array<Frame,{len(specs)}> frames{{}}; }};
constexpr Export bake() {{
 Export out;
 for(unsigned i=0;i<requests.size();++i) {{ const auto& r=requests[i];auto& f=out.frames[i];f.w=r.w;f.h=r.h;
  if(r.meter)mosaico_dot::render::sampleMeter(r.w,r.h,3,r.bp,r.known,r.reverse?(360-r.phase)%360:r.phase,r.enabled,r.color,f,static_cast<mosaico_dot::render::SweepStyle>(r.style));
  else mosaico_dot::render::sampleIcon(static_cast<mosaico_dot::render::Accent>(r.accent),r.w,r.h,r.phase,r.enabled,r.color,f);
 }}return out;
}}
static_assert(sizeof(Dot)==12 && sizeof(Frame)==3080,"stable preview record ABI");
__attribute__((used,section(".preview"))) constexpr Export preview=bake();
'''
    OUT.mkdir(parents=True,exist_ok=True)
    source=OUT/f'dot-model-0123{SUFFIX}-preview.cpp';source.write_text(cpp,encoding='utf8')
    compiler=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
    objcopy=compiler.with_name('riscv32-esp-elf-objcopy.exe')
    obj=OUT/f'dot-model-0123{SUFFIX}-preview.o';binary=OUT/f'dot-model-0123{SUFFIX}-preview.bin'
    command=[str(compiler),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-fconstexpr-ops-limit=100000000','-c',str(source),'-o',str(obj)]
    run=subprocess.run(command,capture_output=True,text=True)
    (OUT/f'dot-model-0123{SUFFIX}-preview.log').write_text(run.stdout+run.stderr,encoding='utf8')
    if run.returncode:raise RuntimeError(run.stderr)
    subprocess.run([str(objcopy),'-O','binary','--only-section=.preview',str(obj),str(binary)],check=True,capture_output=True)
    data=binary.read_bytes();magic,count=struct.unpack_from('<II',data)
    assert magic==0x444f5433 and count==len(specs) and len(data)==8+count*3080
    frames=[]
    for i,s in enumerate(specs):
        offset=8+i*3080;w,h,n,_=struct.unpack_from('<HHHH',data,offset)
        assert (w,h)==(s['w'],s['h']) and n<=256
        primitives=[]
        for j in range(n):
            x,y,d,_,color=struct.unpack_from('<hhhHI',data,offset+8+j*12)
            assert 0<=x and 0<=y and x+d<=w and y+d<=h and d>0
            primitives.append((x,y,d,color))
        frames.append(primitives)
    manifest=dict(primitive_shape='circle',primitive_opacity=255,firmware_uses_shared_model='render::sampleMeter' in (V/'dot_widgets.cpp').read_text(),model_sha256=sha(V/'dot_render_model.h'),geometry_sha256=sha(V/'dot_patterns.h'),
                  existing_firmware_renderer_sha256=sha(V/'dot_widgets.cpp'),preview_binary_sha256=sha(binary),
                  source_sha256=sha(source),compiler=str(compiler),command=command,frame_count=count,specs=specs)
    (OUT/f'dot-model-0123{SUFFIX}-preview.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    return specs,lookup,frames

def raster(spec,primitives):
    im=Image.new('RGB',(spec['w'],spec['h']),BG);draw=ImageDraw.Draw(im)
    for x,y,d,color in primitives:
        draw.ellipse((x,y,x+d-1,y+d-1),fill=((color>>16)&255,(color>>8)&255,color&255))
    return im

def generate():
    before=sha(V/'dot_widgets.cpp')
    specs,lookup,frames=export();images=[raster(s,p) for s,p in zip(specs,frames)]
    font=ImageFont.load_default(size=15);small=ImageFont.load_default(size=13)
    def label(im,pos,text,small_font=False):ImageDraw.Draw(im).text(pos,text,font=small if small_font else font,fill=(215,221,229))
    def fetch(kind,name,w,h,phase=0,bp=10000,known=True,enabled=True,reverse=False,style=0):
        return images[lookup[(kind,name,w,h,phase,bp,known,enabled,reverse,style)]]
    icon=Image.new('RGB',(1120,1550),BG)
    label(icon,(16,12),'FINAL shared C++ primitives - firmware integration matches approved v2 (not device proof)')
    for row,size in enumerate((28,32)):
        y=55+row*185
        for col,name in enumerate(('Quota','Coin','ResetCard')):
            x=18+col*360;im=fetch('icon',name,size,size)
            label(icon,(x,y),f'{name} {size}px'+(' actual' if size==28 or name=='Quota' else ' reference'))
            icon.paste(im,(x,y+30));icon.paste(im.resize((size*4,size*4),Image.Resampling.NEAREST),(x+80,y+30))
            label(icon,(x,y+size*4+36),'1x left | nearest 4x right',True)
    y=440;im=fetch('icon','Refresh',88,88)
    label(icon,(18,y),'OTA INSTALL Refresh - actual 88px - shape unchanged at every pulse phase')
    icon.paste(im,(18,y+40));icon.paste(im.resize((352,352),Image.Resampling.NEAREST),(190,y+40))
    for col,size in enumerate((34,56)):
        xx=600+col*240
        label(icon,(xx,y+40),f'Coin {size}px reference')
        sample=fetch('icon','Coin',size,size)
        icon.paste(sample.resize((size*4,size*4),Image.Resampling.NEAREST),(xx,y+72))
    for row,name in enumerate(('Coin','ResetCard')):
        y=855+row*190;label(icon,(18,y),name+' rotation - same C/R on front AND back; edge naturally narrows')
        for col,phase in enumerate((0,45,90,135,180,270)):
            x=18+col*175;im=fetch('icon',name,28,28,phase)
            label(icon,(x,y+24),f'phase {phase}',True)
            icon.paste(im,(x,y+45));icon.paste(im.resize((112,112),Image.Resampling.NEAREST),(x+36,y+45))
    y=1250;label(icon,(18,y),'Refresh actual 88px: static outline / intensity pulse (no geometric distortion)')
    for col,phase in enumerate(PHASES):
        x=18+col*175;label(icon,(x,y+28),f'phase {phase}',True);icon.paste(fetch('icon','Refresh',88,88,phase),(x,y+54))
    icon.save(OUT/f'ui-icons-0123{SUFFIX}.png')
    charge=Image.new('RGB',(1800,2080),BG)
    label(charge,(16,10),'FINAL STRONG DARK C++ sweep - white core + dim/grey shoulders - stationary dots / exact fill / 6s')
    y=42
    for name in ('green','cyan'):
        for bp in (0,500,5000,10000):
            label(charge,(16,y+10),f'{name} {bp/100:g}%',True)
            for col,phase in enumerate(PHASES):
                x=130+col*220;label(charge,(x,y),f'{phase}',True)
                charge.paste(fetch('meter',name,198,24,phase,bp),(x,y+21))
            y+=59
    for known,enabled,name in ((False,True,'UNKNOWN'),(True,False,'STOPPED 50%')):
        label(charge,(16,y+10),name,True)
        for col,phase in enumerate(PHASES):charge.paste(fetch('meter','green',198,24,phase,5000,known,enabled),(130+col*220,y+21))
        y+=59
    label(charge,(16,y),'One filled column (1%): brightness breath at one fixed position, no fake filled dots')
    y+=28
    for col,phase in enumerate(PHASES):
        im=fetch('meter','green',198,24,phase,100)
        charge.paste(im,(130+col*220,y));charge.paste(im.crop((0,0,48,24)).resize((192,96),Image.Resampling.NEAREST),(130+col*220,y+34))
    y+=148
    label(charge,(16,y),'50% quota / reset: nearest 4x; white core -> DARK/GREY shoulders -> base (5-column band)')
    y+=26
    for col,name in enumerate(('green','cyan')):
        im=fetch('meter',name,198,24,120,5000)
        charge.paste(im.resize((792,96),Image.Resampling.NEAREST),(16+col*880,y))
    y+=120
    label(charge,(16,y),'Single-window 408x24 actual: 50% / phase 60 (1x then 4x)')
    y+=26;im=fetch('meter','green',408,24,60,5000);charge.paste(im,(16,y));y+=38
    charge.paste(im.resize((1632,96),Image.Resampling.NEAREST),(16,y));y+=125
    label(charge,(16,y),'OTA actual 440x28, caller reversed phase => LEFT to RIGHT (others RIGHT to LEFT)')
    y+=28
    for bp in (0,500,5000,10000):
        label(charge,(16,y+8),f'{bp/100:g}%',True)
        for col,phase in enumerate(PHASES):
            x=130+col%3*545;yy=y+col//3*47
            label(charge,(x,yy),f'{phase}',True)
            charge.paste(fetch('meter','ota',440,28,phase,bp,reverse=True),(x,yy+18))
        y+=101
    label(charge,(16,y),'OTA 50% phase 180 - nearest 4x (same compiled primitives)');y+=25
    im=fetch('meter','ota',440,28,180,5000,reverse=True)
    charge.paste(im.resize((1760,112),Image.Resampling.NEAREST),(16,y))
    charge.save(OUT/f'ui-charge-0123{SUFFIX}.png')
    archived=json.loads((OUT/'dot-model-0123-preview.json').read_text())
    raw=(OUT/'dot-model-0123-preview.bin').read_bytes()
    index=next(i for i,s in enumerate(archived['specs']) if s['kind']=='meter' and s['name']=='green' and s['w']==198 and s['phase']==120 and s['bp']==5000 and s['known'] and s['enabled'])
    w,h,n,_=struct.unpack_from('<HHHH',raw,8+index*3080)
    oldcommands=[]
    for j in range(n):
        x,yy,d,_,color=struct.unpack_from('<hhhHI',raw,8+index*3080+8+j*12);oldcommands.append((x,yy,d,color))
    old=raster(archived['specs'][index],oldcommands)
    compare=Image.new('RGB',(1120,680),BG)
    label(compare,(16,12),'v1 vs FINAL: same primitive coordinates, different C++ colour shader (phase120 / 50%)')
    for row,(title,sample) in enumerate((('v1 WHITE ONLY',old),('FINAL STRONG DARK / GREY SHOULDERS',fetch('meter','green',198,24,120,5000)),
                                      ('MILD REFERENCE (not firmware default)',fetch('meter','green',198,24,120,5000,style=1)))):
        yy=55+row*190;label(compare,(16,yy),title)
        compare.paste(sample,(16,yy+28));compare.paste(sample.resize((792,96),Image.Resampling.NEAREST),(225,yy+26))
        # First emitted dot row, cropped from the same raster: 4x colour profile, not another sampler.
        compare.paste(sample.crop((0,3,198,8)).resize((792,20),Image.Resampling.NEAREST),(225,yy+137))
        label(compare,(16,yy+137),'first-row profile',True)
    compare.save(OUT/f'ui-charge-0123{SUFFIX}-compare.png')
    gif=[]
    for phase in range(0,360,10):
        im=Image.new('RGB',(1040,440),BG);label(im,(16,12),'Proposed ~6s cycle | stationary charging sweep | shared C++ samples')
        for row,(name,bp) in enumerate((('green',5000),('cyan',5000),('green',100))):
            source=fetch('meter',name,198,24,phase,bp)
            label(im,(16,45+row*115),f'{name} {bp/100:g}%')
            im.paste(source,(16,70+row*115));im.paste(source.resize((792,96),Image.Resampling.NEAREST),(225,65+row*115))
        gif.append(im)
    gif[0].save(OUT/f'ui-charge-0123{SUFFIX}.gif',save_all=True,append_images=gif[1:],duration=167,loop=0,optimize=False)
    rotation=[]
    for phase in range(0,360,10):
        im=Image.new('RGB',(820,405),BG);label(im,(16,12),'C/R rotation keeps front/back orientation | proposed shared C++ model')
        for col,name in enumerate(('Coin','ResetCard')):
            source=fetch('icon',name,28,28,phase);label(im,(16+col*205,45),name)
            im.paste(source,(16+col*205,72));im.paste(source.resize((112,112),Image.Resampling.NEAREST),(75+col*205,72))
        source=fetch('icon','Refresh',88,88,phase);label(im,(440,45),'Refresh88 pulse - fixed shape')
        im.paste(source,(440,72));im.paste(source.resize((264,264),Image.Resampling.NEAREST),(548,110))
        rotation.append(im)
    rotation[0].save(OUT/f'ui-icons-0123{SUFFIX}.gif',save_all=True,append_images=rotation[1:],duration=333,loop=0,optimize=False)
    assert sha(V/'dot_widgets.cpp')==before,'simulator never changes firmware source'
    print(json.dumps({'frames':len(frames),'model_sha256':sha(V/'dot_render_model.h'),
                      'icons':str(OUT/f'ui-icons-0123{SUFFIX}.png'),'charge':str(OUT/f'ui-charge-0123{SUFFIX}.png')},indent=2))

if __name__=='__main__':generate()
