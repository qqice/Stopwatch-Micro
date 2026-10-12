"""Offline factory-style keyboard preview. Geometry/palette exported from shared C++.
Not LVGL font rasterization or physical touch/firmware acceptance.
"""
import argparse,json,re,struct,subprocess,tempfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
R=Path(__file__).resolve().parents[1]
MODEL=R/'main/apps/app_codex_micro/view/keyboard_style_model.h'
LV=R/'components/lvgl/src/widgets/keyboard/lv_keyboard.c'

def specs():
    s=LV.read_text(encoding='utf8'); result=[]
    names={'LV_KEYBOARD_CTRL_BUTTON_MODE_SPECIAL':'1#','LV_KEYBOARD_CTRL_BUTTON_MODE_TEXT_UPPER':'ABC','LV_KEYBOARD_CTRL_BUTTON_MODE_TEXT_LOWER':'abc','LV_SYMBOL_BACKSPACE':'DEL','LV_SYMBOL_NEW_LINE':'ENTER','LV_SYMBOL_KEYBOARD':'CANCEL','LV_SYMBOL_CLOSE':'CANCEL','LV_SYMBOL_LEFT':'<','LV_SYMBOL_RIGHT':'>','LV_SYMBOL_OK':'GO'}
    for mode in ('lc','uc','spec','num'):
        def body(name):
            t=re.search(r'static const .*? '+name+r'\[\] = \{(.*?)\};',s,re.S).group(1)
            return re.sub(r'#if.*?#endif','',t,flags=re.S)
        tokens=re.findall(r'"(?:\\.|[^"\\])*"|LV_[A-Z_]+',body('default_kb_map_'+mode))
        controls=body('default_kb_ctrl_'+mode+'_map').split(',')
        weights=[int(re.findall(r'\d+',c)[-1]) for c in controls if c.strip()]
        rows=[[]];i=0
        for t in tokens:
            text=json.loads(t) if t.startswith('"') else names[t]
            if not text:break
            if text=='\n':rows.append([]);continue
            kind='Go' if text=='GO' else 'Function' if t in names else 'Normal'
            rows[-1].append((text,weights[i],kind));i+=1
        result.append((mode,rows))
    return result

def export():
    modes=specs(); calls=[]; labels=[]
    for mode,rows in modes:
        for row,keys in enumerate(rows):
            total=sum(w for _,w,_ in keys); unit=0
            for index,(text,weight,kind) in enumerate(keys):
                calls.append(f'make({row},{len(rows)},{index},{len(keys)},{unit},{total},{weight},KeyKind::{kind})')
                labels.append((mode,text));unit+=weight
    cpp=f'#include "{MODEL.as_posix()}"\nusing namespace mosaico_keyboard;\n'+'''struct K {int x,y,w,h;unsigned color,text;};
constexpr K make(int r,int rows,int i,int n,int u,int us,int wt,KeyKind k){auto q=keyRect(r,rows,i,n,u,us,wt);return {SheetX+X+q.x,SheetY+Y+q.y,q.w,q.h,keyColor(k,false),k==KeyKind::Go?GoText:Text};}
struct Export {int radius,font,x,y,w,h;K keys[COUNT];};
__attribute__((section(".preview"),used)) constexpr Export data={Radius,FontSize,SheetX+X,SheetY+Y,Width,Height,{CALLS}};
'''.replace('COUNT',str(len(calls))).replace('CALLS',','.join(calls))
    compiler=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
    with tempfile.TemporaryDirectory() as d:
        p=Path(d);(p/'preview.cpp').write_text(cpp)
        run=subprocess.run([str(compiler),'-std=c++17','-Wall','-Wextra','-Werror','-c',str(p/'preview.cpp'),'-o',str(p/'preview.o')],capture_output=True,text=True)
        if run.returncode:raise RuntimeError(run.stderr)
        subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')),'-O','binary','--only-section=.preview',str(p/'preview.o'),str(p/'preview.bin')],capture_output=True,check=True)
        data=(p/'preview.bin').read_bytes()
    meta=struct.unpack_from('<6i',data);keys=[]
    for i,(mode,text) in enumerate(labels):keys.append(dict(mode=mode,label=text,rect=struct.unpack_from('<4i',data,24+i*24),color=struct.unpack_from('<I',data,40+i*24)[0],text_color=struct.unpack_from('<I',data,44+i*24)[0]))
    return meta,keys

def render(out):
    meta,keys=export();radius,font_size,x,y,w,h=meta
    image=Image.new('RGB',(960,960),'#15191f');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',font_size)
    small=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',14)
    for index,mode in enumerate(('lc','uc','spec','num')):
        dx=(index%2)*480;dy=(index//2)*480
        draw.text((dx+32,dy+144),f'{mode.upper()} / ACTIVE EDITOR',font=small,fill='#ececf0')
        draw.rounded_rectangle((dx+32,dy+190,dx+447,dy+237),radius=10,outline='#55555a')
        draw.text((dx+44,dy+204),'SSID / password hidden',font=font,fill='#ececf0')
        for k in keys:
            if k['mode']!=mode:continue
            a,b,c,d=k['rect'];draw.rounded_rectangle((dx+a,dy+b,dx+a+c-1,dy+b+d-1),radius=radius,fill=f"#{k['color']:06x}")
            # Factory retains icons on firmware. Use compact explanatory labels here.
            label=k['label'];f=small if label in ('DEL','ENTER','CANCEL') else font
            draw.text((dx+a+c/2,dy+b+d/2),label,anchor='mm',font=f,fill=f"#{k['text_color']:06x}")
    out.parent.mkdir(parents=True,exist_ok=True);image.save(out)
    out.with_suffix('.json').write_text(json.dumps({'disclaimer':'Shared geometry/palette; Arial surrogate, not LVGL/hardware render','meta':meta,'keys':keys},indent=2))
    return meta,keys
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=R/'artifacts/keyboard-style/keyboard-preview.png');a=p.parse_args();render(a.output);print(a.output)
