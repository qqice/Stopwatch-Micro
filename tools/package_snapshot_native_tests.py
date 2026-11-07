"""Package only native harness, actual LVGL src and selected actual view sources."""
from pathlib import Path
import hashlib, json, shutil, tarfile
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.artifacts/private/mosaico/snapshot-native-harness-007'
OUT.mkdir(parents=True,exist_ok=True)
STAGE=OUT/'source'
STAGE.mkdir(exist_ok=True)
files=[]
def copy(src,dest):
    dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
    files.append({'path':str(dest.relative_to(STAGE)).replace('\\','/'),'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'bytes':src.stat().st_size})
for src in sorted((ROOT/'components/lvgl/src').rglob('*')):
    if src.is_file(): copy(src,STAGE/'lvgl/src'/src.relative_to(ROOT/'components/lvgl/src'))
for src in sorted((ROOT/'components/lvgl').glob('*.h')):
    copy(src,STAGE/'lvgl'/src.name)
view=ROOT/'main/apps/app_codex_micro/view'
for name in ['dot_widgets.cpp','dot_widgets.h','dot_patterns.h','dot_render_model.h','view_transition_snapshot.cpp','view_transition_snapshot.h']:
    copy(view/name,STAGE/'view'/name)
for src in sorted((ROOT/'tools/snapshot_native_harness').rglob('*')):
    if src.is_file() and src.suffix in ['.h','.cpp','.py'] and 'build' not in src.parts and '__pycache__' not in src.parts: copy(src,STAGE/src.relative_to(ROOT/'tools/snapshot_native_harness'))
(OUT/'manifest.json').write_text(json.dumps(files,indent=2)+'\n')
archive=OUT/'snapshot-native-harness.tar.gz'
with tarfile.open(archive,'w:gz') as t: t.add(STAGE,arcname='snapshot-native-harness')
print(json.dumps({'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':len(files),'bytes':archive.stat().st_size}))
