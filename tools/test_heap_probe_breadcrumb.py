"""RV32 execution of actual panic/probe functions; SDK CPU/frame and snprintf mocked.
No scheduler, live heap, or hardware fault attribution is claimed.
"""
from pathlib import Path
import hashlib, os, re, struct, subprocess, sys, tempfile, unittest
R=Path(__file__).resolve().parents[1]
RUN=R/'artifacts/heap-probe-breadcrumb-tests'
CXX=Path('C:/Espressif/tools/esp-clang/esp-21.1.3_20260408/esp-clang/bin/clang++.exe')
class HeapProbeTests(unittest.TestCase):
 def test_source_callers_and_panic_boundary(self):
  p=(R/'main/ota/panic_capture.cpp').read_text(); n=(R/'main/host/network_quota.cpp').read_text();o=(R/'main/ota/mosaico_ota.cpp').read_text()
  h=p.split('__wrap_esp_panic_handler(panic_info_t* info)',1)[1].split('bool MosaicoPanicNumeric',1)[0]
  for forbidden in ['esp_cpu_', 'heap_caps_', 'malloc(', 'printf(', 'memcpy(', 'xTask', 'portENTER', 'capture.sp +']:
   self.assertNotIn(forbidden,h)
  self.assertIn('capture.heapProbe0 = heapProbeStage[0]',h);self.assertIn('capture.heapProbe1 = heapProbeStage[1]',h)
  self.assertLess(h.index('capture.heapProbe1 ='),h.index('capture.magic = ProbeCaptureMagic'))
  self.assertEqual(n.count('heap_caps_check_integrity_all(true)'),3);self.assertEqual(n.count('heap_caps_check_integrity_all(false)'),1)
  self.assertEqual(o.count('heap_caps_check_integrity_all(false)'),1)
  for stage in ['ScanBefore','ScanAfter','TailnetBefore','TailnetAfter','DownloadBefore','DownloadChunkBoundary','DownloadComplete']:
   self.assertIn('MosaicoHeapProbeStage::'+stage,n)
  self.assertIn('if (budget >= 2) return;',n);self.assertIn('(offset % 65536U) == 0 && ![&]()',n)
  self.assertIn('psramHealthy() && integrityHealthy()',o)
  numeric=p.split('bool MosaicoPanicNumeric',1)[1].split('bool MosaicoPanicStatus',1)[0]
  self.assertIn('count != 11',numeric);self.assertIn('? 3U : 2U',numeric);self.assertNotIn('heapProbe',numeric)
 def test_actual_functions_rv32(self):
  import test_mosaico_gauge_scale_backend as support
  src=(R/'main/ota/panic_capture.cpp').read_text();src=re.sub(r'^#include .*$', '',src,flags=re.M)
  src=src.replace('#error "Mosaico panic capture requires the ESP32-S31 main RISC-V app"','')
  pre=r'''
#define MOSAICO_BOARD 1
#define CONFIG_IDF_TARGET_ESP32S31 1
#define CONFIG_IDF_TARGET_ARCH_RISCV 1
#define DRAM_ATTR
#define RTC_NOINIT_ATTR
#define IRAM_ATTR
#include "panic_capture.h"
#include <cstdio>
typedef unsigned int uintptr_t;
struct RvExcFrame {uint32_t mepc,ra,sp,mcause,mtval,a0,a1,a2,a3,a4,a5,a6,a7,s0;};
struct panic_info_t {void* frame;int32_t core,exception;void* addr;};
static unsigned cpu=0,realCalls=0,casCalls=0;static bool casFail=false;
unsigned esp_cpu_get_core_id(){return cpu;}
bool esp_cpu_compare_and_set(volatile uint32_t* p,uint32_t a,uint32_t b){++casCalls;if(casFail||*p!=a)return false;*p=b;return true;}
constexpr int ESP_RST_PANIC=4;int esp_reset_reason(){return ESP_RST_PANIC;}
extern "C" void __real_esp_panic_handler(panic_info_t*){++realCalls;}

extern "C" int printf(const char*,...){return 0;}
extern "C" int fflush(void*){return 0;}
void* stdout=nullptr;
'''
  cases=r'''
static bool has(const char* s,const char* sub){for(unsigned i=0;s[i];++i){unsigned j=0;while(sub[j]&&s[i+j]==sub[j])++j;if(!sub[j])return true;}return false;}
extern "C" int run_case(int id){
 heapProbeStage[0]=heapProbeStage[1]=0;cpu=0;capture.magic=0;
 if(id==0){auto t=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::ScanBefore);if((heapProbeStage[0]&15)!=1||heapProbeStage[1])return 1;MosaicoHeapProbeExit(t);return (heapProbeStage[0]&15)!=0;}
 if(id==1){auto t=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::TailnetBefore);cpu=1;MosaicoHeapProbeExit(t);return (heapProbeStage[0]&15)||heapProbeStage[1];}
 if(id==2){auto a=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::ScanAfter);cpu=1;auto b=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::OtaHealth);if((heapProbeStage[0]&15)!=2||(heapProbeStage[1]&15)!=8)return 2;MosaicoHeapProbeExit(a);if((heapProbeStage[1]&15)!=8)return 3;MosaicoHeapProbeExit(b);return (heapProbeStage[0]&15)||(heapProbeStage[1]&15);}
 if(id==3){auto a=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::ScanBefore);auto b=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::ScanAfter);MosaicoHeapProbeExit(a);if((heapProbeStage[0]&15)!=2)return 4;MosaicoHeapProbeExit(b);MosaicoHeapProbeExit(a);return (heapProbeStage[0]&15)!=0;}
 if(id==4){auto a=MosaicoHeapProbeEnter(static_cast<MosaicoHeapProbeStage>(9));cpu=2;auto b=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::ScanBefore);MosaicoHeapProbeExit(a);MosaicoHeapProbeExit(b);return heapProbeStage[0]||heapProbeStage[1];}
 if(id==5){cpu=0;auto a=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::DownloadBefore);cpu=1;auto b=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::OtaHealth);__wrap_esp_panic_handler(nullptr);if(capture.magic!=ProbeCaptureMagic||capture.core!=-1||capture.heapProbe0!=a.value||capture.heapProbe1!=b.value||!realCalls)return 5;return 0;}
 if(id==6){uint32_t words[12];for(auto& w:words)w=0xdeadbeef;capture.magic=ProbeCaptureMagic;capture.core=1;if(!MosaicoPanicNumeric(words,11)||words[9]!=3||words[10]||words[11]!=0xdeadbeef)return 6;return MosaicoPanicNumeric(words,12);}
 if(id==7||id==8){capture.magic=id==7?LegacyCaptureMagic:CaptureMagic;capture.registerMagic=RegisterMagic;capture.heapProbe0=capture.heapProbe1=0xffffffff;char text[768];if(!MosaicoPanicStatus(text,sizeof(text))||!has(text,"heap_probe_valid=0 heap_entry0=0 heap_entry1=0"))return 7;if(id==7&&!has(text,"regs_valid=0"))return 8;if(id==8&&!has(text,"regs_valid=1"))return 9;uint32_t words[11];if(!MosaicoPanicNumeric(words,11)||words[9]!=(id==7?2U:3U))return 10;return 0;}
 if(id==9){capture.magic=ProbeCaptureMagic;capture.heapProbe0=0xfffffff8;capture.heapProbe1=0x126;char text[768];MosaicoPanicStatus(text,sizeof(text));return !has(text,"heap_probe_valid=1 heap_entry0=8 heap_entry1=6 heap_seq0=268435455 heap_seq1=18");}
 if(id==10){capture.magic=ProbeCaptureMagic;capture.registerMagic=RegisterMagic;for(unsigned n=1;n<=768;++n){char buffer[770];for(auto& c:buffer)c='Q';MosaicoPanicStatus(buffer+1,n);bool nul=false;for(unsigned j=1;j<=n;++j)if(buffer[j]==0)nul=true;if(buffer[0]!='Q'||buffer[n+1]!='Q'||!nul)return 11;}return 0;}
 if(id==11){if(MosaicoPanicStatus(nullptr,20)||MosaicoPanicStatus(nullptr,0))return 12;char text[32];if(MosaicoPanicStatus(text,sizeof(text))||!has(text,"reason=no_saved_panic"))return 13;return 0;}
 if(id==13){casCalls=0;casFail=true;auto t=MosaicoHeapProbeEnter(MosaicoHeapProbeStage::ScanBefore);casFail=false;return t.core!=UINT32_MAX||casCalls!=4||heapProbeStage[0];}
 if(id==12){cpu=0;{MosaicoHeapProbeScope scoped(MosaicoHeapProbeStage::DownloadComplete);if((heapProbeStage[0]&15)!=7)return 14;cpu=1;}return (heapProbeStage[0]&15)!=0;}
 return 99;
}
extern "C" {volatile int test_results[14]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<14;++i)test_results[i]=run_case(i);test_done();}
'''
  RUN.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.probe-test-',dir=R) as td:
   td=Path(td)
   for name in ('cstdint','cstddef'):(td/name).write_text(support.HEADERS[name])
   (td/'sdkconfig.h').write_text('')
   (td/'cstdio').write_text('#pragma once\n#include <cstddef>\nnamespace std {extern "C" int snprintf(char*,size_t,const char*,...);extern "C" int printf(const char*,...);extern "C" int fflush(void*);}\n')
   stub=td/'format.cpp';stub.write_text('extern \"C\" int snprintf(char*,unsigned,const char*,...){return 0;}')
   cpp=td/'actual.cpp';cpp.write_text(pre+src+cases);elfpath=RUN/'actual-probe.elf'
   args=[str(CXX),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),'-I'+str(R/'main/ota'),str(cpp),str(stub),'--ld-path='+str(CXX.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elfpath)]
   result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TMP':str(td),'TEMP':str(td)});(RUN/'compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
  sys.path.insert(0,str(R/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
  from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32,UC_HOOK_CODE
  from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC,UC_RISCV_REG_A0,UC_RISCV_REG_RA
  elf=elfpath.read_bytes();entry=struct.unpack_from('<I',elf,24)[0];phoff=struct.unpack_from('<I',elf,28)[0];phsize,phnum=struct.unpack_from('<HH',elf,42);uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
  for i in range(phnum):
   kind,off,addr,_,length,_,_,_=struct.unpack_from('<8I',elf,phoff+i*phsize)
   if kind==1:uc.mem_write(addr,elf[off:off+length])
  shoff=struct.unpack_from('<I',elf,32)[0];shsize,shnum=struct.unpack_from('<HH',elf,46);symbols={}
  for i in range(shnum):
   sh=struct.unpack_from('<10I',elf,shoff+i*shsize)
   if sh[1]!=2:continue
   strings=struct.unpack_from('<10I',elf,shoff+sh[6]*shsize);names=elf[strings[4]:strings[4]+strings[5]]
   for pos in range(sh[4],sh[4]+sh[5],sh[9]):
    name,value,_,_,_,_=struct.unpack_from('<IIIBBH',elf,pos);symbols[names[name:].split(b'\0',1)[0].decode()]=value
  def format_hook(uc,address,size,unused):
   regs=[uc.reg_read(UC_RISCV_REG_A0+i) for i in range(8)];dest,cap,fmt=regs[:3];text=bytes(uc.mem_read(fmt,2048)).split(b'\0',1)[0].decode();idx=3
   def sub(match):
    nonlocal idx
    if idx<8:value=regs[idx]
    else:value=struct.unpack('<I',bytes(uc.mem_read(uc.reg_read(UC_RISCV_REG_SP)+(idx-8)*4,4)))[0]
    idx+=1;spec=match.group();kind=spec[-1]
    if kind=='d' and value&0x80000000:value-=0x100000000
    return (('%08x' if '08' in spec else '%x')%value) if kind=='x' else str(value)
   rendered=re.sub(r'%(?:08)?l?[dux]',sub,text).encode()
   if cap:uc.mem_write(dest,rendered[:cap-1]+b'\0')
   uc.reg_write(UC_RISCV_REG_A0,len(rendered));uc.reg_write(UC_RISCV_REG_PC,uc.reg_read(UC_RISCV_REG_RA))
  uc.hook_add(UC_HOOK_CODE,format_hook,begin=symbols['snprintf'],end=symbols['snprintf'])
  uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=10000000)
  values=struct.unpack('<14i',bytes(uc.mem_read(symbols['test_results'],56)))
  (RUN/'execution.log').write_text('Actual panic_capture.cpp and header; SDK/frame/CAS/snprintf mocked; RV32 ISA.\nELF_SHA256='+hashlib.sha256(elf).hexdigest()+'\n'+repr(values)+'\n')
  self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(values,(0,)*14)
if __name__=='__main__':unittest.main()
