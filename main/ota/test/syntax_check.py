"""Reuse the existing S31 compile flags without changing the shared build tree."""
import json, pathlib, re, subprocess
root = pathlib.Path(__file__).resolve().parents[3]
build = root / '.artifacts/mosaico/build'
entries = json.loads((build / 'compile_commands.json').read_text())
base = next(e for e in entries if e['file'].endswith('serial_debug.cpp'))
log = root / '.artifacts/mosaico/ota-syntax-check.log'
with log.open('w') as out:
    for name in ['main/ota/mosaico_ota.cpp', 'main/main.cpp', 'main/debug/serial_debug.cpp', 'main/apps/app_codex_micro/app_codex_micro.cpp', 'main/ota/test/readiness_model.cpp']:
        command = re.sub(r' -o .*? -c .*$', lambda _: ' -fsyntax-only ' + (root/name).as_posix(), base['command'])
        out.write('\nSOURCE '+name+'\n'); out.flush()
        p = subprocess.run(command, cwd=base['directory'], stdout=out, stderr=out)
        print(name, p.returncode)
        if p.returncode: raise SystemExit(p.returncode)
main = (root/'main/main.cpp').read_text()
app = (root/'main/apps/app_codex_micro/app_codex_micro.cpp').read_text()
ota = (root/'main/ota/mosaico_ota.cpp').read_text()
assert 'healthPoll(ota_app->otaReady())' in main and 'healthPoll(true)' not in main
assert app.index('_view->update(state)') < app.index('++_ota_completed_loops')
assert '_serial_debug.reset();' in app and '_serial_debug != nullptr, _ota_completed_loops' in app
assert 'void healthTimeout(void*)\n{\n    std::lock_guard<std::mutex> guard(healthLock);' in ota
assert 'void healthPoll(bool appLoopReady)\n{\n    std::lock_guard<std::mutex> guard(healthLock);' in ota
print('readiness source wiring and common health outcome lock PASS')
print(log)
