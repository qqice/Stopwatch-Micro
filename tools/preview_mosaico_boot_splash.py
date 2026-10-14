"""Compile shared native splash geometry/sequence checks and export its exact pixels.
No firmware build, SDK change, device or external service is used.
"""
from pathlib import Path
import json, subprocess, hashlib
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
 return count>100 && count<1000 && minX+maxX==Width-1 && minY+maxY==Height-1;
}
static_assert(check(),"native geometry, black edges, drain-before-reuse/free/enable, allocation failure");
constexpr auto bake(){std::array<uint8_t,LogoWidth*LogoHeight> out{};
 for(int y=0;y<LogoHeight;++y)for(int x=0;x<LogoWidth;++x)out[y*LogoWidth+x]=pixel(Left+x,Top+y)?255:0;
 return out;
}
__attribute__((used,section(".preview"))) constexpr auto preview=bake();
'''
    source=OUT/'splash-preview.cpp';source.write_text(code)
    obj=OUT/'splash-preview.o';binary=OUT/'splash-preview.bin'
    command=[str(compiler),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-fconstexpr-ops-limit=100000000','-c',str(source),'-o',str(obj)]
    run=subprocess.run(command,capture_output=True,text=True)
    (OUT/'splash-preview.log').write_text(run.stdout+run.stderr)
    if run.returncode:raise RuntimeError(run.stderr)
    subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')),'-O','binary','--only-section=.preview',str(obj),str(binary)],capture_output=True,check=True)
    data=binary.read_bytes();assert len(data)==114*26
    image=Image.new('L',(480,480),0);image.paste(Image.frombytes('L',(114,26),data),(183,227))
    image.convert('RGB').save(OUT/'boot-splash-native.png')
    image.crop((167,211,313,269)).resize((876,348),Image.Resampling.NEAREST).convert('RGB').save(OUT/'boot-splash-detail.png')
    (OUT/'manifest.json').write_text(json.dumps({'model_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),'command':command,'geometry':[480,480],'logo_box':[183,227,114,26],'panel_brightness':30,'preview_note':'Exact RGB565 white/black pixels; image does not simulate panel luminance or optical acceptance.'},indent=2))
    return OUT/'boot-splash-native.png'
if __name__=='__main__':print(export())
