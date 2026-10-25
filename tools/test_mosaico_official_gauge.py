"""Official profile provenance/contracts and constexpr tests of actual transport.
No firmware build, hardware, network, or shared build directory is used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'main/hal/mosaico'
VENDOR = BASE / 'vendor/bq27220'

def text(path): return path.read_text(encoding='utf-8')
def profile_fields():
    table = text(VENDOR / 'bq27220_data_memory.c')
    addresses = {m[1]: int(m[0], 16) for m in re.findall(r'0x([0-9A-Fa-f]+), (BQ_DM_\w+),', table)}
    return [(n, addresses[d], int(w)) for n,d,w in re.findall(
        r'PROFILE_FIELD\("([^"]+)", (BQ_DM_\w+), .*?, ([12])\);', text(VENDOR/'bq27220.c'))]

def profiles():
    source = text(BASE/'mosaico_official_gauge.cpp')
    profiles = []
    for body in re.findall(r'parameter_cedv_t cedv = \{(.*?)\n    \};', source, re.S):
        vals = {n:int(v) for n,v in re.findall(r'\.(\w+) = (\d+),', body)}
        vals['TC_C1'] = vals.pop('TC')*256 + vals.pop('C1')
        profiles.append(vals)
    profiles[0]['gauging_config'] = 0x102a
    config = source.split('static const gauging_config_t cfg = {',2)[2].split('};',1)[0]
    shifts = {'CCT':0,'CSYNC':1,'EDV_CMP':3,'SC':4,'FIXED_EDV0':5,'FCC_LIM':8,'FC_FOR_VDQ':10,'IGNORE_SD':11,'SME0':12}
    profiles[1]['gauging_config'] = sum(int(v)<<shifts[n] for n,v in re.findall(r'\.(\w+) = (\d+)',config))
    return profiles

class OfficialGaugeTests(unittest.TestCase):
    def test_vendor_and_board_provenance(self):
        manifest = json.loads(text(VENDOR/'PROVENANCE.json'))
        for name,sha in manifest['files'].items():
            self.assertEqual(hashlib.sha256((VENDOR/name).read_bytes()).hexdigest(),sha)
            original=ROOT/manifest['source']/name
            if original.exists(): self.assertEqual((VENDOR/name).read_bytes(),original.read_bytes())
        for name,sha in manifest['previous_profile_sources'].items():
            if (ROOT/name).exists(): self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),sha)
        board = ROOT/manifest['board_profile_source']
        if board.exists():
            self.assertEqual(hashlib.sha256(board.read_bytes()).hexdigest(),manifest['board_profile_sha256'])
        official = profiles()[1]
        expected = dict(full_charge_cap=65,design_cap=65,reserve_cap=0,near_full=5,
            self_discharge_rate=20,EDV0=3000,EDV1=3410,EDV2=3530,EMF=3670,C0=115,
            R0=968,T0=4547,R1=4764,TC_C1=0x0b00,DOD0=4147,DOD10=4002,DOD20=3969,
            DOD30=3938,DOD40=3880,DOD50=3824,DOD60=3794,DOD70=3753,DOD80=3677,
            DOD90=3574,DOD100=3490,gauging_config=0x0d11)
        if board.exists():
            body = text(board).split('parameter_cedv_t default_cedv = {',1)[1].split('};',1)[0]
            board_values = {n:65 if v=='design_cap' else int(v) for n,v in re.findall(r'\.(\w+) = (\w+),',body)}
            board_values['TC_C1']=board_values.pop('TC')*256+board_values.pop('C1')
            board_values['gauging_config']=0x0d11
            self.assertEqual(board_values,expected)
        self.assertEqual(official,expected)
        self.assertEqual(len(official),26)

    def test_previous_audit_bytes_and_5same21different(self):
        previous,official=profiles()
        # Non-sensitive independent fixture: only the 26 permitted profile fields.
        expected = dict(full_charge_cap=65,design_cap=65,reserve_cap=0,near_full=5,
            self_discharge_rate=20,EDV0=3031,EDV1=3385,EDV2=3501,EMF=3743,C0=149,
            R0=867,T0=4030,R1=316,TC_C1=0x0900,DOD0=4173,DOD10=4043,DOD20=3925,
            DOD30=3821,DOD40=3725,DOD50=3656,DOD60=3619,DOD70=3582,DOD80=3515,
            DOD90=3439,DOD100=2713,gauging_config=0x102a)
        self.assertEqual(previous,expected)
        audit_path=ROOT/'.artifacts/private/mosaico/near-full-a200-postmeasure-audit-001/postmeasure-public131.json'
        if audit_path.exists():
            raw={}
            for field in json.loads(text(audit_path))['fields']:
                for i,b in enumerate(bytes.fromhex(field['rawBE'])):raw[int(field['address'],16)+i]=b
            raw[0x926b],raw[0x926c]=0,5
            audited={name:int.from_bytes(bytes(raw[addr+i] for i in range(width)),'big') for name,addr,width in profile_fields()}
            self.assertEqual(previous,audited)
        self.assertEqual(previous['TC_C1'],0x0900)
        self.assertEqual(sum(previous[n]==official[n] for n in official),5)
        self.assertEqual(sum(previous[n]!=official[n] for n in official),21)
        diff_path=ROOT/'.artifacts/private/mosaico/near-full-b5-postmeasure-audit-001/comparison.json'
        if diff_path.exists():
            diff=json.loads(text(diff_path))
            self.assertEqual(len(diff),1)
            self.assertEqual(diff[0]['after']['near_i2'],'5/0005')

    def test_targeted_write_blocks_only_profile_bytes(self):
        source=text(VENDOR/'bq27220.c')
        block=source.split('s_profile_write_blocks[] = {',1)[1].split('};',1)[0]
        table=text(VENDOR/'bq27220_data_memory.c')
        addresses={m[1]:int(m[0],16) for m in re.findall(r'0x([0-9A-Fa-f]+), (BQ_DM_\w+),',table)}
        written=set()
        for field,size in re.findall(r'first_field = (BQ_DM_\w+), .size = (\d+)',block):
            written.update(range(addresses[field],addresses[field]+int(size)))
        expected={addr+i for _,addr,width in profile_fields() for i in range(width)}
        self.assertEqual(written,expected)
        self.assertEqual(len(profile_fields()),26)

    def test_boot_single_owner_no_legacy_replay(self):
        source=text(BASE/'hal_mosaico.cpp')
        boot=source.split('Hal::GaugeBootReloadStatus Hal::gaugeBootReload(',1)[1].split('Hal::MosaicoClockDiagnostics',1)[0]
        self.assertEqual(boot.count('mosaico_official_gauge_apply(gauge)'),1)
        for retired in ['gaugeSetNominalCapacity(', 'gaugeAccess(', 'near_boot_apply(', 'near_boot_finish(', 'gauge_load_journal(', 'normal_gauge_fence_locked(', 'MosaicoGaugeCold::']:
            self.assertNotIn(retired,boot)
        self.assertIn('gauge_boot_info.attempted = true',boot)
        self.assertIn('fcc > 0 && fcc <= 65',boot)
        self.assertIn('!official.exitAttempted && gauge_exit_config(true)',boot)
        self.assertIn('!official.sealAttempted',boot)
        self.assertNotIn('finish(Status::Critical',boot)
        self.assertIn('gauge_diagnostic_locked(gauge_boot_before',boot)
        self.assertIn('gauge_diagnostic_locked(gauge_boot_after',boot)

    def test_actual_transport_constexpr_failure_bounds_ownership(self):
        source=text(BASE/'mosaico_official_gauge_driver.c')
        adapter=source.split('void mosaico_bq_reset_attempts',1)[1].split('#ifndef CONFIG_BQ27220',1)[0]
        adapter='constexpr void mosaico_bq_reset_attempts'+adapter
        adapter=adapter.replace('unsigned mosaico_bq_get_attempts','constexpr unsigned mosaico_bq_get_attempts')
        adapter=adapter.replace('static i2c_bus_device_handle_t','constexpr i2c_bus_device_handle_t')
        adapter=adapter.replace('static esp_err_t','constexpr esp_err_t').replace('memcpy(','copy_bytes(')
        hal=text(BASE/'hal_mosaico.cpp')
        cleanup=hal.split('    bool cleanup = observed;',1)[1].split('    char details[128]{};',1)[0]
        code='''#include <cstddef>
#include <cstdint>
#include <initializer_list>
using i2c_bus_device_handle_t=uintptr_t;
using i2c_bus_handle_t=uintptr_t;
using i2c_master_dev_handle_t=uintptr_t;
using esp_err_t=int;
#undef NULL
#define NULL 0
constexpr int ESP_OK=0,ESP_ERR_INVALID_ARG=-1,ESP_ERR_INVALID_SIZE=-2;
constexpr void copy_bytes(uint8_t* to,const uint8_t* from,size_t n){for(size_t i=0;i<n;++i)to[i]=from[i];}
struct Harness {
 unsigned mosaico_bq_attempts=0,exits=0,seals=0,localExits=0;
 bool marked=true,readable=true;
 uint16_t op=0x0402;
 uintptr_t gauge=7;
 struct {bool exitAttempted=false,sealAttempted=false;} official;
 constexpr int i2c_master_transmit_receive(uintptr_t t,const uint8_t* reg,size_t prefix,uint8_t*,size_t n,int timeout){return t==7&&*reg==0x3e&&prefix==1&&n<=34&&timeout==100?-71:-99;}
 // Fault injection: every submitted write reports a transport failure/unknown ACK.
 constexpr int i2c_master_transmit(uintptr_t t,const uint8_t* data,size_t n,int timeout){
  if(t!=7||timeout!=100)return -99;
  if(data[0]==0&&n==3&&data[2]==0){
   if(data[1]==0x91||data[1]==0x92){++exits;marked=marked&&bool(mosaico_bq_attempts&1);}
   if(data[1]==0x30){++seals;marked=marked&&bool(mosaico_bq_attempts&2);}
   return -72;
  }
  return data[0]==0x3e&&data[1]==42&&n<=35?-72:-99;
 }
 constexpr bool gauge_word(uint8_t,uint16_t& out){out=op;return readable;}
 constexpr bool gauge_exit_config(bool){++localExits;return false;}
 constexpr void gauge_delay_ms(unsigned){}
 constexpr bool configExitAccepted(uint16_t value){return !(value&(0x0400|1));}
'''+adapter+'''
 constexpr bool cleanup(bool observed,uint16_t operation){
    bool cleanup = observed;
'''+cleanup+'''
    return cleanup;
 }
};
constexpr bool test(){
 Harness h;uint8_t data[35]={42};uintptr_t token=7;
 if(h.mosaico_bq_device_create(7,0x55,0)!=7||h.mosaico_bq_device_create(7,0x54,0)||h.mosaico_bq_device_create(0,0x55,0))return false;
 for(size_t n:{size_t(1),size_t(34)}){
  if(h.mosaico_bq_read(token,0x3e,n,data)!=-71||h.mosaico_bq_write(token,0x3e,n,data)!=-72)return false;
 }
 if(h.mosaico_bq_read(token,0x3e,35,data)!=-2||h.mosaico_bq_write(token,0x3e,35,data)!=-2)return false;
 if(h.mosaico_bq_read(0,0x3e,1,data)!=-1||h.mosaico_bq_write(0,0x3e,1,data)!=-1)return false;
 if(h.mosaico_bq_read(token,0x3e,0,data)!=-1||h.mosaico_bq_write(token,0x3e,0,data)!=-1)return false;
 if(h.mosaico_bq_device_delete(&token)!=0||token||h.mosaico_bq_device_delete(&token)!=-1||h.mosaico_bq_device_delete(nullptr)!=-1)return false;
 for(uint8_t command:{uint8_t(0x91),uint8_t(0x92)}){
  Harness f;uint8_t payload[2]={command,0};
  if(f.mosaico_bq_write(7,0,2,payload)!=-72||f.mosaico_bq_get_attempts()!=1||!f.marked)return false;
  f.official.exitAttempted=true;
  if(f.cleanup(true,f.op)||f.exits!=1||f.localExits||f.seals)return false;
  f.mosaico_bq_reset_attempts();if(f.mosaico_bq_get_attempts())return false;
 }
 Harness sealed;sealed.op=0x0002;uint8_t seal[2]={0x30,0};
 if(sealed.mosaico_bq_write(7,0,2,seal)!=-72||sealed.mosaico_bq_get_attempts()!=2||!sealed.marked)return false;
 sealed.official.sealAttempted=true;
 if(sealed.cleanup(true,sealed.op)||sealed.seals!=1||sealed.localExits)return false;
 // If vendor never attempted cleanup, HAL may issue one local exit or Seal only.
 Harness fresh;if(fresh.cleanup(true,fresh.op)||fresh.localExits!=1||fresh.seals)return false;
 Harness unsealed;unsealed.op=0x0002;
 if(unsealed.cleanup(true,unsealed.op)||unsealed.seals!=1||unsealed.localExits)return false;
 // Same byte values at a different register, wrong width, or nonzero high byte
 // must not masquerade as a vendor cleanup attempt.
 Harness unrelated;uint8_t wrong[2]={0x91,1};
 unrelated.mosaico_bq_write(7,1,2,seal);unrelated.mosaico_bq_write(7,0,1,seal);
 unrelated.mosaico_bq_write(7,0,2,wrong);
 return unrelated.mosaico_bq_get_attempts()==0;
}
static_assert(test());
'''
        compilers=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        compiler=os.environ.get('CXX') or (str(compilers[-1]) if compilers else None)
        if not compiler:self.skipTest('set CXX or install the target compiler for constexpr checks')
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path=Path(directory)/'adapter.cpp';path.write_text(code,encoding='utf-8')
            result=subprocess.run([compiler,'-std=c++20','-fsyntax-only',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
        for forbidden in ['i2c_new_master_bus(', 'i2c_master_bus_add_device(', 'i2c_master_bus_rm_device(', 'i2c_del_master_bus(']:
            self.assertNotIn(forbidden,source)
        for api in ['device_create','device_delete','read_bytes','write_bytes']:
            self.assertIn('#undef i2c_bus_'+api,source)

if __name__=='__main__':unittest.main()

