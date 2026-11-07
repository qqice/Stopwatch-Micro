"""Compile current test + actual current helper, reusing existing native LVGL objects.
Usage: python3 run_reuse.py /absolute/existing/native-harness
"""
from pathlib import Path
import hashlib, os, subprocess, sys
BASE=Path(sys.argv[1]).resolve(); INPUT=Path(__file__).resolve().parent
OUT=BASE/'build/final007';OUT.mkdir(parents=True,exist_ok=True)
flags=['-std=c++17','-O1','-g','-fno-omit-frame-pointer','-fsanitize=address,undefined','-DLV_CONF_INCLUDE_SIMPLE','-DLV_KCONFIG_IGNORE','-I'+str(INPUT/'view'),'-I'+str(BASE),'-I'+str(BASE/'sdk'),'-I'+str(BASE/'lvgl'),'-I'+str(BASE/'view')]
CXX=os.environ.get('CXX','clang++')
sources=sorted((BASE/'lvgl/src').rglob('*.c'))+sorted((BASE/'lvgl/src').rglob('*.cpp'))+[BASE/'view/dot_widgets.cpp']
objects=[BASE/'build'/(hashlib.sha256(str(src.relative_to(BASE)).encode()).hexdigest()[:20]+'.o') for src in sources]
if any(not p.exists() for p in objects): raise RuntimeError('Required reusable native LVGL/dot object missing')
log=[]
try:
 for src,obj in [(INPUT/'test.cpp',OUT/'test.o'),(INPUT/'view/view_transition_snapshot.cpp',OUT/'helper.o')]:
  cmd=[CXX,*flags,'-c',str(src),'-o',str(obj)];r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);log.append(r.stdout)
  if r.returncode: raise RuntimeError('compile failed: '+' '.join(cmd)+'\n'+r.stdout)
 cmd=[CXX,'-fsanitize=address,undefined',*[str(p) for p in objects],str(OUT/'test.o'),str(OUT/'helper.o'),'-o',str(OUT/'snapshot_test')]
 r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);log.append(r.stdout)
 if r.returncode: raise RuntimeError('link failed: '+r.stdout)
except Exception as e:
 log.append(str(e));(OUT/'build.log').write_text('\n'.join(log));print(e);sys.exit(1)
(OUT/'build.log').write_text('\n'.join(log))
env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=0:halt_on_error=1';env['UBSAN_OPTIONS']='halt_on_error=1:print_stacktrace=1'
r=subprocess.run([str(OUT/'snapshot_test')],cwd=OUT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
(OUT/'test.log').write_text(r.stdout);print('Reused native LVGL/dot objects; ASan leak detection disabled for host platform support.');print(r.stdout);sys.exit(r.returncode)
