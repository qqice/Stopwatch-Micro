"""Package unmodified production decoder + official codec + real cJSON for Mac native tests.
Run with the project's explicit Espressif Python. No firmware build or device IO.
"""
from pathlib import Path
import shutil
R=Path(__file__).resolve().parents[1]
D=R/'.artifacts/private/mosaico/delta-native-harness-001'
for name in ('sdk/psa','sdk/mbedtls','sdk/freertos','production','vendor','cjson'):
 (D/name).mkdir(parents=True,exist_ok=True)
for name in ('mosaico_delta.cpp','mosaico_delta.h','mosaico_ota.h'):
 shutil.copy2(R/'main/ota'/name,D/'production'/name)
shutil.copytree(R/'components/esp_delta_ota',D/'vendor/esp_delta_ota',dirs_exist_ok=True)
for name in ('cJSON.c','cJSON.h'):
 shutil.copy2(R/'boards/mosaico/managed_components/espressif__cjson/cJSON'/name,D/'cjson'/name)
for src in (R/'tools/delta_native_harness').rglob('*'):
 if src.is_file():
  dst=D/src.relative_to(R/'tools/delta_native_harness');dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
(D/'production/host').mkdir(exist_ok=True)
shutil.copy2(R/'main/host/token_history.h',D/'production/host/token_history.h')
nesting=(R/'main/host/token_history.cpp').read_text().split('bool JsonNestingValid(',1)[1].split('\nbool InitTokenHistory()',1)[0]
(D/'nesting_actual.h').write_text('bool JsonNestingValid('+nesting+'\n')
s=(R/'main/ota/mosaico_ota.cpp').read_text()
function=s.split('bool resetDownloadForFullFallback()\n',1)[1].split('\nbool busy()',1)[0]
(D/'reset_actual.h').write_text('bool resetDownloadForFullFallback()\n'+function+'\n')
assert 'if(!active.load()||selected.load()||imageReady.load()||fullFallbackUsed)return false;' in function
assert 'fullFallbackUsed && (d.size!=expectedSize.load()||std::memcmp(d.hash,expectedHash,32))' in s
n=(R/'main/host/network_quota.cpp').read_text()
assert 'heap_caps_malloc(plan.size,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)' in n
assert 'heap_caps_malloc(2049,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)' in n
assert 'if(patch) {' in n and 'if(!MosaicoOta::resetDownloadForFullFallback())return;' in n
print(D)
