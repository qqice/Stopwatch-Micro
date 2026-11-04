"""Narrow OTA asset simulator. Compile actual shared sampling + production glyphs.

No firmware link/device access. Pillow only rasterizes compiler-exported circles.
"""
import hashlib
import json
import struct
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

R = Path(__file__).resolve().parents[1]
V = R / 'main/apps/app_codex_micro/view'
OUT = R / '.artifacts/mosaico/ota-activity-preview-001'
PHASES = (0, 60, 120, 180, 240, 300)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export():
    source_text = (V / 'dot_widgets.cpp').read_text(encoding='utf8')
    glyph_source = source_text.split('constexpr detail::Glyph otaGlyphs[] = {', 1)[1]
    glyph_source = 'constexpr detail::Glyph otaGlyphs[] = {' + glyph_source.split('struct Motion', 1)[0]
    specs = [dict(kind='icon', name='Refresh', w=88, h=88, phase=p, color=0xD18C37) for p in PHASES]
    specs += [dict(kind='text', name=name, w=132, h=32, pitch=4, phase=0, color=color)
              for name, color in [('DELTA', 0x9868CD), ('FULL', 0xD18C37)]]
    cpp = '#include "' + (V / 'dot_render_model.h').as_posix() + '"\n#include <array>\nusing namespace mosaico_dot;\n'
    cpp += glyph_source + r'''
struct Dot { int16_t x,y,d;uint16_t reserved;uint32_t rgb; };
struct Frame { uint16_t w=0,h=0,count=0,reserved=0;std::array<Dot,256> dots{};
 constexpr void dot(int x,int y,int d,uint32_t rgb) {dots[count++]={static_cast<int16_t>(x),static_cast<int16_t>(y),static_cast<int16_t>(d),0,rgb};}
};
constexpr void text(const char* word,int count,uint32_t color,Frame& f) {
 const auto l=detail::textLayout(f.w,f.h,count,4);
 for(int i=0;i<count;++i) {
  const auto& glyph=uiGlyph(word[i]);
  for(int y=0;y<7;++y)for(int x=0;x<5;++x)if(glyph.rows[y]&(1<<(4-x)))
   f.dot(l.x+(i*6+x)*l.pitch,l.y+y*l.pitch,l.diameter,color);
 }
}
struct Export {uint32_t magic=0x4f544131,count=8;std::array<Frame,8> frames{};};
constexpr Export bake() {
 Export out;
'''
    for i, spec in enumerate(specs):
        cpp += f' out.frames[{i}].w={spec["w"]};out.frames[{i}].h={spec["h"]};\n'
        if spec['kind'] == 'icon':
            cpp += f' render::sampleIcon(render::Accent::Refresh,88,88,{spec["phase"]},true,{spec["color"]},out.frames[{i}]);\n'
        else:
            cpp += f' text("{spec["name"]}",{len(spec["name"])},{spec["color"]},out.frames[{i}]);\n'
    cpp += ''' return out;
}
static_assert(sizeof(Dot)==12 && sizeof(Frame)==3080,"preview record ABI");
__attribute__((used,section(".preview"))) constexpr Export preview=bake();
'''
    OUT.mkdir(parents=True, exist_ok=True)
    source = OUT / 'assets.cpp'
    source.write_text(cpp, encoding='utf8')
    compiler = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
    command = [str(compiler), '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-c', str(source), '-o', str(OUT / 'assets.o')]
    run = subprocess.run(command, capture_output=True, text=True)
    (OUT / 'compile.log').write_text(run.stdout + run.stderr, encoding='utf8')
    if run.returncode:
        raise RuntimeError(run.stderr)
    subprocess.run([str(compiler.with_name('riscv32-esp-elf-objcopy.exe')), '-O', 'binary', '--only-section=.preview', str(OUT / 'assets.o'), str(OUT / 'assets.bin')], check=True, capture_output=True)
    data = (OUT / 'assets.bin').read_bytes()
    assert struct.unpack_from('<II', data) == (0x4f544131, 8)
    assert len(data) == 8 + 8 * 3080
    frames = []
    for i, spec in enumerate(specs):
        offset = 8 + i * 3080
        w, h, count, _ = struct.unpack_from('<HHHH', data, offset)
        assert (w, h) == (spec['w'], spec['h']) and count <= 256
        primitives = []
        for j in range(count):
            x, y, d, _, color = struct.unpack_from('<hhhHI', data, offset + 8 + j * 12)
            assert d > 0 and x >= 0 and y >= 0 and x + d <= w and y + d <= h
            primitives.append((x, y, d, color))
        frames.append(primitives)
    manifest = dict(specs=specs, frames=frames, primitive_shape='circle', cycle_ms=1600,
                    model_sha256=sha(V / 'dot_render_model.h'), geometry_sha256=sha(V / 'dot_patterns.h'),
                    glyph_source_sha256=sha(V / 'dot_widgets.cpp'), extracted_glyph_sha256=hashlib.sha256(glyph_source.encode()).hexdigest(),
                    preview_binary_sha256=sha(OUT / 'assets.bin'), source_sha256=sha(source), command=command,
                    note='Actual production otaGlyphs/uiGlyph source extracted verbatim; actual textLayout; same 5x7 text draw coordinates; shared sampleIcon. Asset simulation only, not device proof.')
    (OUT / 'assets.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    return specs, frames


def raster(spec, primitives):
    im = Image.new('RGB', (spec['w'], spec['h']), 'black')
    draw = ImageDraw.Draw(im)
    for x, y, d, color in primitives:
        draw.ellipse((x, y, x + d - 1, y + d - 1), fill=((color >> 16) & 255, (color >> 8) & 255, color & 255))
    return im


def generate():
    specs, frames = export()
    images = [raster(s, p) for s, p in zip(specs, frames)]
    sheet = Image.new('RGB', (960, 420), 'black')
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 10), 'Shared C++ Refresh activity / unchanged mask / 1.6s cycle (not progress)', fill='white')
    for i in range(6):
        x = 12 + i * 156
        draw.text((x, 35), f'{PHASES[i]} deg', fill='white')
        sheet.paste(images[i].resize((132, 132), Image.Resampling.NEAREST), (x, 55))
    for i in range(6, 8):
        im = images[i]
        im.save(OUT / f'{specs[i]["name"].lower()}.png')
        x = 16 + (i - 6) * 470
        draw.text((x, 216), f'{specs[i]["name"]} 132x32 pitch4: 1x then nearest 3x', fill='white')
        sheet.paste(im, (x, 240))
        sheet.paste(im.resize((396, 96), Image.Resampling.NEAREST), (x, 290))
    sheet.save(OUT / 'assets.png')
    animation = [im.resize((264, 264), Image.Resampling.NEAREST) for im in images[:6]]
    animation[0].save(OUT / 'activity.gif', save_all=True, append_images=animation[1:], duration=[270, 270, 260, 270, 270, 260], loop=0, optimize=False)
    print(str(OUT / 'assets.png'))


if __name__ == '__main__':
    generate()
