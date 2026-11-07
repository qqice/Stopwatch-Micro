"""Offline export/property test of production meter sampler and bounded dirty regions.

Cross-compiles data only (no firmware build, target run or device access). The
retained JSON is also input for independent pixel replay. Use the pinned IDF Python.
"""
import hashlib
import json
import struct
import subprocess
import unittest
from pathlib import Path

R = Path(__file__).resolve().parents[1]
V = R / 'main/apps/app_codex_micro/view'
OUT = R / '.artifacts/mosaico/meter-dirty-regions'
JUMPS = (0, 6, 97, 180, 359)
SPECS = [
    (408, 24, 3, 10000, True), (198, 28, 3, 5000, True),
    (32, 8, 1, 2500, True), (408, 24, 4, 0, True),
    (198, 28, 3, 5000, False), (1, 1, 4, 10000, True),
    (440, 32, 4, 9999, True), (32, 8, 1, 1, True),
]


def export():
    OUT.mkdir(parents=True, exist_ok=True)
    compiler = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
        '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
    code = '#include "' + (V / 'dot_render_model.h').as_posix() + '"\n'
    code += r'''
#include <array>
using namespace mosaico_dot;
struct Dot { int x,y,d;uint32_t color; };
struct Frame { int count=0;std::array<Dot,200> dots{};
 constexpr void dot(int x,int y,int d,uint32_t c){dots[count++]={x,y,d,c};}
};
struct Record { Frame animated,off;std::array<render::MeterDirtyRegions,15> dirty{}; };
constexpr Record bake(int w,int h,int rows,int bp,bool known,int phase) {
 Record r;
 render::sampleMeter(w,h,rows,bp,known,phase,true,0x67e7ae,r.animated);
 render::sampleMeter(w,h,rows,bp,known,phase,false,0x67e7ae,r.off);
 int i=0;
 for(int jump:{0,6,97,180,359})for(int mode=0;mode<3;++mode)
  r.dirty[i++]=render::meterDirtyRegions(w,h,rows,bp,known,phase,mode!=1,
      (phase+jump)%360,true,0x67e7ae,mode==2);
 return r;
}
static_assert(sizeof(Record)==6948,"export ABI");
struct Export {std::array<Record,2880> records;};
__attribute__((used,section(".preview"))) constexpr Export output{{
'''
    for spec in SPECS:
        w, h, rows, bp, known = spec
        for phase in range(360):
            code += f' bake({w},{h},{rows},{bp},{str(known).lower()},{phase}),\n'
    code += '}};\n'
    source = OUT / 'sampler-export.cpp'
    source.write_text(code, encoding='utf8')
    commands = [
        [str(compiler), '-std=c++17', '-fconstexpr-ops-limit=1000000000', '-O0', '-c', str(source), '-o', str(OUT/'sampler-export.o')],
        [str(compiler.with_name('riscv32-esp-elf-objcopy.exe')), '-O', 'binary', '--only-section=.preview', str(OUT/'sampler-export.o'), str(OUT/'sampler-export.bin')],
    ]
    signature = hashlib.sha256((code + (V/'dot_render_model.h').read_text() +
                                (V/'dot_patterns.h').read_text()).encode()).hexdigest()
    stamp = OUT/'export-signature.txt'
    binary = OUT/'sampler-export.bin'
    cached = (stamp.exists() and binary.exists() and stamp.read_text() ==
              signature + ':' + hashlib.sha256(binary.read_bytes()).hexdigest())
    if not cached:
        logs = []
        for command in commands:
            run = subprocess.run(command, capture_output=True, text=True)
            logs.append('COMMAND: ' + subprocess.list2cmdline(command) + '\n' + run.stdout + run.stderr)
            (OUT/'compile.log').write_text('\n'.join(logs), encoding='utf8')
            if run.returncode:
                raise RuntimeError(f'Compiler exit {run.returncode}: {run.stderr}')
        stamp.write_text(signature + ':' + hashlib.sha256(binary.read_bytes()).hexdigest())
    data = (OUT/'sampler-export.bin').read_bytes()
    assert len(data) == 6948*360*len(SPECS)
    frames = []
    for offset in range(0, len(data), 6948):
        record = []
        for base in (offset, offset+3204):
            count, = struct.unpack_from('<i', data, base)
            record.append([struct.unpack_from('<iiiI', data, base+4+i*16) for i in range(count)])
        dirty = []
        for j in range(15):
            base = offset+6408+j*36
            count, = struct.unpack_from('<i', data, base+32)
            dirty.append([struct.unpack_from('<iiii', data, base+i*16) for i in range(count)])
        frames.append(dict(animated=record[0], off=record[1], dirty=dirty))
    payload = dict(specs=SPECS, jumps=JUMPS, frames=frames,
                   model_sha256=hashlib.sha256((V/'dot_render_model.h').read_bytes()).hexdigest(),
                   note='Exact production sampleMeter primitives; dirty modes animated, static, animated-plus-static; all 360 phases.')
    (OUT/'sampler-export.json').write_text(json.dumps(payload, separators=(',', ':')), encoding='utf8')
    return frames


class MeterDirtyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = export()

    def test_changed_actual_primitives_covered_all_phases(self):
        checks = 0
        for si, spec in enumerate(SPECS):
            for phase in range(360):
                old = self.frames[si*360+phase]
                for ji, jump in enumerate(JUMPS):
                    new = self.frames[si*360+(phase+jump)%360]['animated']
                    for mode in range(3):
                        rects = old['dirty'][ji*3+mode]
                        self.assertLessEqual(len(rects), 2)
                        priors = [old['off']] if mode == 1 else [old['animated']]
                        if mode == 2:
                            priors.append(old['off']) # Any partial static draw while disabled.
                        changed = False
                        for prior in priors:
                            self.assertEqual(len(prior), len(new))
                            for a, b in zip(prior, new):
                                self.assertEqual(a[:3], b[:3])
                                if a[3] == b[3]:
                                    continue
                                changed = True
                                x, y, d, _ = a
                                # Includes AA guard, not just centre or primitive interior.
                                self.assertTrue(any(x1 <= x-1 and y1 <= y-1 and x2 >= x+d and y2 >= y+d
                                                    for x1, y1, x2, y2 in rects), (spec, phase, jump, mode, a, rects))
                                checks += 1
                        self.assertEqual(bool(rects), changed)
        print(f'Covered {checks} changed production primitives; 8 specs x 360 phases x 5 jumps x 3 prior modes; <=2 regions.')
        (OUT/'coverage.json').write_text(json.dumps(dict(changed_primitive_comparisons=checks,
            phases=360, specs=SPECS, jumps=JUMPS, modes=3), indent=2), encoding='utf8')

    def test_translated_clipped_pending_and_disabled_mixture(self):
        # Consecutive not-yet-drawn requests accumulate invalid regions. Each
        # intermediate transition is covered, hence the original->final delta is.
        si = 0
        path = (358, 4, 101, 107, 287)
        rects = []
        for a, b in zip(path, path[1:]):
            jump = (b-a) % 360
            ji = JUMPS.index(jump)
            rects.extend(self.frames[si*360+a]['dirty'][ji*3])
        old = self.frames[path[0]]['animated']
        new = self.frames[path[-1]]['animated']
        for ox, oy in ((0, 0), (-97, -13), (31, 211), (480, -90)):
            for clip in ((-1000, -1000, 2000, 2000), (0, 0, 239, 239), (20, 4, 80, 18)):
                for a, b in zip(old, new):
                    if a[3] == b[3]: continue
                    x, y, d, _ = a
                    bounds = (max(x+ox-1, clip[0]), max(y+oy-1, clip[1]),
                              min(x+ox+d, clip[2]), min(y+oy+d, clip[3]))
                    if bounds[0] > bounds[2] or bounds[1] > bounds[3]: continue
                    self.assertTrue(any(x1+ox <= bounds[0] and y1+oy <= bounds[1] and
                                        x2+ox >= bounds[2] and y2+oy >= bounds[3]
                                        for x1, y1, x2, y2 in rects))

    def test_production_integration_boundaries(self):
        source = (V/'dot_widgets.cpp').read_text(encoding='utf8')
        motion = source.split('void setMotion(', 1)[1].split('void setText(', 1)[0]
        self.assertLess(motion.index('if (!enabled) return;'), motion.index('meterDirtyRegions('))
        self.assertIn('lv_obj_get_content_coords(obj, &content)', motion)
        self.assertIn('!previousEnabled && s->meterMotion', motion)
        self.assertIn('lv_obj_invalidate_area(obj,&area)', motion)
        self.assertIn('if (changed) lv_obj_invalidate(obj)', motion) # Icons unchanged.
        meter = source.split('void setMeter(', 1)[1].split('lv_obj_t* createIcon(', 1)[0]
        self.assertIn('lv_obj_invalidate(obj)', meter) # Data changes remain full.
        # Size/visibility invalidation remains native LVGL; no event interception.
        self.assertNotIn('LV_EVENT_SIZE_CHANGED', source)
        self.assertNotIn('LV_EVENT_STYLE_CHANGED', source)


if __name__ == '__main__':
    unittest.main()

