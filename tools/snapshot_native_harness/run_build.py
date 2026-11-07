"""Build real LVGL and production sources; no downloads or generated substitute renderer."""
from pathlib import Path
import concurrent.futures, hashlib, os, subprocess, sys, time
ROOT=Path(__file__).resolve().parent
BUILD=ROOT/'build'
BUILD.mkdir(exist_ok=True)
flags=['-O1','-g','-fno-omit-frame-pointer','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE','-DLV_KCONFIG_IGNORE','-I'+str(ROOT),'-I'+str(ROOT/'sdk'),'-I'+str(ROOT/'lvgl'),'-I'+str(ROOT/'view')]
sources=sorted((ROOT/'lvgl/src').rglob('*.c'))+sorted((ROOT/'lvgl/src').rglob('*.cpp'))+[ROOT/'view/dot_widgets.cpp',ROOT/'view/view_transition_snapshot.cpp',ROOT/'test.cpp']
def compile_one(src):
    obj=BUILD/(hashlib.sha256(str(src.relative_to(ROOT)).encode()).hexdigest()[:20]+'.o')
    cmd=[os.environ.get('CXX','clang++') if src.suffix=='.cpp' else os.environ.get('CC','clang'),'-std=c++17' if src.suffix=='.cpp' else '-std=c11',*flags,'-c',str(src),'-o',str(obj)]
    r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    if r.returncode: raise RuntimeError(' '.join(cmd)+'\n'+r.stdout)
    return obj
started=time.monotonic()
with (BUILD/'build.log').open('w') as log:
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            objects=list(pool.map(compile_one,sources))
        cmd=[os.environ.get('CXX','clang++'),'-fsanitize=address,undefined',*[str(p) for p in objects],'-o',str(BUILD/'snapshot_test')]
        r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        log.write(r.stdout)
        if r.returncode: raise RuntimeError('link failed: '+r.stdout)
        print(f'BUILD PASS sources={len(sources)} seconds={time.monotonic()-started:.2f}',flush=True)
    except Exception as e:
        log.write(str(e)); print(e,flush=True); sys.exit(1)
env=os.environ.copy(); env.setdefault('ASAN_OPTIONS','detect_leaks='+('0' if sys.platform=='darwin' else '1')+':halt_on_error=1'); env.setdefault('UBSAN_OPTIONS','halt_on_error=1:print_stacktrace=1')
r=subprocess.run([str(BUILD/'snapshot_test')],cwd=BUILD,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
(BUILD/'test.log').write_text(r.stdout)
print(r.stdout);sys.exit(r.returncode)
