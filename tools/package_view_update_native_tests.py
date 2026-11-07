"""Package extracted baseline/current OTA render functions with LVGL spies; no target IO."""
from pathlib import Path
import argparse, hashlib, shutil, subprocess
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline',default='5ef70acce9a6486c86f7c63bd60f9e37e06b76b8',help='Git revision for the pre-optimization OldView baseline')
args=parser.parse_args()
R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
D=R/'.artifacts/private/mosaico/view-update-native-harness-001'
D.mkdir(parents=True,exist_ok=True)
(D/'ota').mkdir(exist_ok=True)
shutil.copy2(R/'main/ota/mosaico_ota.h',D/'ota/mosaico_ota.h')
for n in ['ota_presentation_model.h','view_update_model.h']:
 shutil.copy2(V/n,D/n)
def function(s,sig):
 start=s.index(sig); brace=s.index('{',start);depth=1;i=brace+1
 while depth:
  if s[i]=='{':depth+=1
  elif s[i]=='}':depth-=1
  i+=1
 return s[start:i]+'\n'
baseline=subprocess.check_output(['git','rev-parse','--verify',args.baseline+'^{commit}'],cwd=R,text=True,encoding='utf8').strip()
old=subprocess.check_output(['git','show',baseline+':main/apps/app_codex_micro/view/view_mosaico.cpp'],cwd=R,text=True,encoding='utf8')
new=(V/'view_mosaico.cpp').read_text(encoding='utf8')
for name,src in [('OldView',old),('NewView',new)]:
 funcs=''.join(function(src,sig) for sig in ['bool CodexMicroView::otaHasDownloadOffer() const','bool CodexMicroView::otaActionEnabled() const','void CodexMicroView::renderOtaAction()','void CodexMicroView::renderOta()'])
 (D/(name+'.inc')).write_text(funcs.replace('CodexMicroView::',name+'::'),encoding='utf8')
for f in (R/'tools/view_update_native_harness').iterdir():
 if f.is_file():shutil.copy2(f,D/f.name)
(D/'manifest.txt').write_text('baseline='+baseline+'\n'+'\n'.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(D).as_posix() for p in sorted(D.rglob('*')) if p.is_file() and p.name!='manifest.txt')+'\n')
print(D)
