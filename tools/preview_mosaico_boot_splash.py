"""Compile shared native splash geometry/sequence checks and export its exact pixels.
No firmware build, SDK change, device or external service is used.
"""
from pathlib import Path
import json, subprocess, hashlib, struct
from PIL import Image
R=Path(__file__).resolve().parents[1]
OUT=R/'.artifacts/mosaico/boot-splash'
def export():
    OUT.mkdir(parents=True,exist_ok=True)
    compiler=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
    header=R/'main/hal/mosaico/mosaico_boot_splash_model.h'
    code='#include "'+header.as_posix()+'"\n'+r'''
#include <array>
using namespace mosaico_boot_splash;
struct Fake {
 bool fail=false,pending=false,freed=false,enabled=false,valid=true; int renders=0,draws=0,drains=0;
 constexpr bool allocate(){return !fail;}
 constexpr void render(int y){if(pending || y!=renders*StripRows)valid=false;++renders;}
 constexpr void draw(int y){if(pending || y!=draws*StripRows)valid=false;pending=true;++draws;}
 constexpr void drainDark(){if(!pending)valid=false;pending=false;++drains;}
 constexpr void release(){if(pending || drains!=Height/StripRows)valid=false;freed=true;}
 constexpr void enable(){if(!freed || pending)valid=false;enabled=true;}
};
constexpr bool check(){
 Fake ok; if(!show(ok)||!ok.valid||!ok.enabled||ok.draws!=60||ok.drains!=60)return false;
 Fake fail;fail.fail=true;if(show(fail)||fail.renders||fail.draws||fail.drains||fail.freed||fail.enabled)return false;
 int count=0,minX=480,minY=480,maxX=0,maxY=0;
 for(int y=0;y<Height;++y)for(int x=0;x<Width;++x){
  if(pixel(x,y)){++count;if(x<minX)minX=x;if(y<minY)minY=y;if(x>maxX)maxX=x;if(y>maxY)maxY=y;}
  if((x<Left||x>=Left+LogoWidth||y<Top||y>=Top+LogoHeight)&&pixel(x,y))return false;
 }
 int dots=0;
 for(int letter=0;letter<5;++letter)for(int y=0;y<7;++y)for(int x=0;x<5;++x)
  if(row(letter,y)&(1U<<(4-x)))++dots;
 return count==dots*Diameter*Diameter && minX+maxX==Width-1 && minY+maxY==Height-1;
}
static_assert(check(),"native geometry, black edges, drain-before-reuse/free/enable, allocation failure");
constexpr auto bake(){std::array<uint8_t,LogoWidth*LogoHeight> out{};
 for(int y=0;y<LogoHeight;++y)for(int x=0;x<LogoWidth;++x)out[y*LogoWidth+x]=pixel(Left+x,Top+y)?255:0;
 return out;
}
__attribute__((used,section(".preview"))) constexpr auto preview=bake();
__attribute__((used,section(".preview_meta"))) constexpr uint32_t metadata[]={Width,Height,Left,Top,LogoWidth,LogoHeight,Brightness};
'''
    source=OUT/'splash-preview.cpp';source.write_text(code)
    obj=OUT/'splash-preview.o';binary=OUT/'splash-preview.bin'
    command=[str(compiler),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-fconstexpr-ops-limit=100000000','-c',str(source),'-o',str(obj)]
    run=subprocess.run(command,capture_output=True,text=True)
    (OUT/'splash-preview.log').write_text(run.stdout+run.stderr)
    if run.returncode:raise RuntimeError(run.stderr)
    subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')),'-O','binary','--only-section=.preview',str(obj),str(binary)],capture_output=True,check=True)
    metadata=OUT/'splash-preview-meta.bin'
    subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')),'-O','binary','--only-section=.preview_meta',str(obj),str(metadata)],capture_output=True,check=True)
    width,height,left,top,logo_width,logo_height,brightness=struct.unpack('<7I',metadata.read_bytes())
    data=binary.read_bytes();assert len(data)==logo_width*logo_height
    image=Image.new('L',(width,height),0);image.paste(Image.frombytes('L',(logo_width,logo_height),data),(left,top))
    image.convert('RGB').save(OUT/'boot-splash-native.png')
    detail=image.crop((max(0,left-16),max(0,top-16),min(width,left+logo_width+16),min(height,top+logo_height+16)))
    detail.resize((detail.width*2,detail.height*2),Image.Resampling.NEAREST).convert('RGB').save(OUT/'boot-splash-detail.png')
    (OUT/'manifest.json').write_text(json.dumps({'model_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),'command':command,'geometry':[width,height],'logo_box':[left,top,logo_width,logo_height],'panel_brightness':brightness,'preview_note':'Exact RGB565 white/black pixels; image does not simulate panel luminance or optical acceptance.'},indent=2))
    return OUT/'boot-splash-native.png'
if __name__=='__main__':print(export())
